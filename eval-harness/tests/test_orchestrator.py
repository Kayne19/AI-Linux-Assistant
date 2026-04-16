from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from eval_harness.adapters.base import SubjectAdapter, SubjectSession
from eval_harness.backends.base import SandboxBackend, SandboxHandle
from eval_harness.controllers.base import SandboxController, SandboxControllerFactory
from eval_harness.judges.base import BlindJudge
from eval_harness.models import (
    AdapterTurnResult,
    BlindJudgeRequest,
    BlindJudgeResult,
    CommandExecutionResult,
    PlannerReviewDecision,
    PlannerReviewOutcome,
    PlannerScenarioRequest,
    RunEvent,
    RunEventType,
    ScenarioSetupStatus,
    ScenarioSpec,
    SubjectSpec,
    TurnSeed,
    VerificationCheck,
)
from eval_harness.orchestration import (
    BenchmarkRunOrchestrator,
    JudgeJobOrchestrator,
    ScenarioSetupFailedError,
    ScenarioSetupOrchestrator,
)
from eval_harness.persistence.database import build_engine, build_session_factory, create_all_tables
from eval_harness.persistence.postgres_models import ScenarioSetupRunRecord
from eval_harness.persistence.store import EvalHarnessStore
from eval_harness.planners.base import ScenarioPlanner


def _build_store() -> EvalHarnessStore:
    engine = build_engine("sqlite+pysqlite:///:memory:")
    create_all_tables(engine)
    return EvalHarnessStore(build_session_factory(engine))


def _scenario() -> ScenarioSpec:
    return ScenarioSpec(
        scenario_name="nginx-recovery",
        title="Nginx recovery",
        summary="Recover a broken nginx service",
        what_it_tests=("systemd recovery", "log inspection"),
        target_image="ami-golden",
        observable_problem_statement="The website is down and nginx will not start.",
        sabotage_procedure=("Break nginx with a bad unit override.",),
        verification_probes=(
            VerificationCheck(
                name="nginx-broken",
                command="systemctl is-active nginx",
                expected_substrings=("failed",),
            ),
        ),
        repair_checks=(
            VerificationCheck(
                name="nginx-fixed",
                command="systemctl is-active nginx",
                expected_substrings=("active",),
            ),
        ),
        judge_rubric=("diagnosis", "actionability"),
        context_seed=(TurnSeed(role="system", content="Use concise shell reasoning."),),
        turn_budget=3,
    )


@dataclass
class FakePlanner(ScenarioPlanner):
    generated_scenario: ScenarioSpec = field(default_factory=_scenario)
    review_decisions: list[PlannerReviewDecision] = field(default_factory=list)
    review_calls: list[tuple[int, int]] = field(default_factory=list)
    name: str = "fake_planner"

    def generate_scenario(self, request: PlannerScenarioRequest) -> ScenarioSpec:
        del request
        return self.generated_scenario

    def review_sabotage(
        self,
        scenario: ScenarioSpec,
        *,
        round_index: int,
        command_results: tuple[CommandExecutionResult, ...],
        correction_count: int,
    ) -> PlannerReviewDecision:
        del scenario, command_results
        self.review_calls.append((round_index, correction_count))
        if self.review_decisions:
            return self.review_decisions.pop(0)
        return PlannerReviewDecision(outcome=PlannerReviewOutcome.APPROVE, summary="approved")


@dataclass
class FakeController(SandboxController):
    name: str = "fake_controller"
    send_responses: list[str] = field(default_factory=list)
    execute_batches: list[tuple[CommandExecutionResult, ...]] = field(default_factory=list)
    sent_messages: list[str] = field(default_factory=list)
    session_keys: list[str] = field(default_factory=list)
    closed: bool = False
    send_exception: Exception | None = None

    def send(self, *, agent_id: str, message: str, session_key: str | None = None, system_prompt: str | None = None) -> str:
        del agent_id, system_prompt
        self.sent_messages.append(message)
        self.session_keys.append(session_key or "")
        if self.send_exception is not None:
            raise self.send_exception
        if self.send_responses:
            return self.send_responses.pop(0)
        return "ack"

    def execute_commands(
        self,
        commands: tuple[str, ...],
        *,
        agent_id: str,
        session_key: str | None = None,
    ) -> tuple[CommandExecutionResult, ...]:
        del commands, agent_id
        self.session_keys.append(session_key or "")
        if self.execute_batches:
            return self.execute_batches.pop(0)
        return ()

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeControllerFactory(SandboxControllerFactory):
    controllers: list[FakeController]
    opened_purposes: list[str] = field(default_factory=list)

    def open(self, handle: SandboxHandle, *, purpose: str = "") -> SandboxController:
        del handle
        self.opened_purposes.append(purpose)
        return self.controllers.pop(0)


@dataclass
class FakeBackend(SandboxBackend):
    name: str = "fake_backend"
    created_broken_images: list[str] = field(default_factory=list)
    destroyed_handles: list[str] = field(default_factory=list)
    wait_calls: list[str] = field(default_factory=list)
    requested_target_images: list[str] = field(default_factory=list)
    diagnostics: dict = field(default_factory=lambda: {"service": "ok"})
    collected_failure_handles: list[str] = field(default_factory=list)
    configured_runtime_handles: list[str] = field(default_factory=list)
    cleared_runtime_handles: list[str] = field(default_factory=list)
    broken_image_wait_progress: list[dict] = field(default_factory=list)
    broken_image_wait_exception: Exception | None = None

    def launch_staging(self, group_id: str, scenario_id: str, *, target_image: str | None = None) -> SandboxHandle:
        self.requested_target_images.append(str(target_image or ""))
        return SandboxHandle(
            handle_id=f"staging-{group_id}",
            kind="instance",
            backend_name=self.name,
            remote_id=f"staging-{scenario_id}",
            image_id="ami-resolved",
            metadata={
                "requested_target_image": str(target_image or ""),
                "resolved_target_image": str(target_image or ""),
                "resolved_golden_ami_id": "ami-resolved",
                "golden_image_build_triggered": False,
                "golden_image_build_source": "existing_ami",
            },
        )

    def wait_until_ready(self, handle: SandboxHandle, timeout_seconds: int = 600) -> None:
        del timeout_seconds
        self.wait_calls.append(handle.remote_id)

    def request_broken_image(self, staging: SandboxHandle, group_id: str, scenario_id: str) -> str:
        del staging, scenario_id
        image_id = f"broken-{group_id}"
        self.created_broken_images.append(image_id)
        return image_id

    def wait_for_broken_image(self, image_id: str, *, progress_callback=None) -> None:
        self.wait_calls.append(f"wait-image:{image_id}")
        for metadata in self.broken_image_wait_progress:
            if progress_callback is not None:
                progress_callback(dict(metadata))
        if self.broken_image_wait_exception is not None:
            raise self.broken_image_wait_exception

    def launch_subject_clones(
        self,
        group_id: str,
        scenario_id: str,
        broken_image_id: str,
        subject_names: list[str],
    ) -> dict[str, SandboxHandle]:
        del scenario_id, broken_image_id
        return {
            subject_name: SandboxHandle(
                handle_id=f"{group_id}-{subject_name}",
                kind="instance",
                backend_name=self.name,
                remote_id=f"clone-{subject_name}",
            )
            for subject_name in subject_names
        }

    def destroy_handle(self, handle: SandboxHandle) -> None:
        self.destroyed_handles.append(handle.remote_id)

    def destroy_broken_image(self, image_id: str) -> None:
        del image_id

    def collect_failure_diagnostics(self, handle: SandboxHandle) -> dict:
        self.collected_failure_handles.append(handle.remote_id)
        return dict(self.diagnostics)

    def configure_controller_runtime(self, handle: SandboxHandle) -> dict:
        self.configured_runtime_handles.append(handle.remote_id)
        return {
            "openclaw_runtime_provider": "openai",
            "openclaw_runtime_model": "openai/gpt-5.4-mini",
            "openclaw_runtime_thinking": "medium",
            "openclaw_runtime_configured": True,
            "openclaw_runtime_probe_passed": True,
        }

    def clear_controller_runtime(self, handle: SandboxHandle) -> dict:
        self.cleared_runtime_handles.append(handle.remote_id)
        return {"openclaw_runtime_cleared": True}


@dataclass
class FakeSubjectSession(SubjectSession):
    turn_results: list[AdapterTurnResult]
    seeded: tuple[TurnSeed, ...] = ()
    submitted_messages: list[str] = field(default_factory=list)
    abort_called: bool = False

    def seed_context(self, context_seed: tuple[TurnSeed, ...]) -> None:
        self.seeded = context_seed

    def submit_user_message(self, message: str) -> AdapterTurnResult:
        self.submitted_messages.append(message)
        if self.turn_results:
            return self.turn_results.pop(0)
        return AdapterTurnResult(
            user_message=message,
            assistant_message="No-op",
            run_id="run-default",
            status="completed",
            terminal_event_type="done",
            events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
        )

    def close(self) -> dict[str, str]:
        return {"closed": "true"}

    def abort(self) -> dict[str, str]:
        self.abort_called = True
        return {"closed": "true", "aborted": "true"}


@dataclass
class FakeSubjectAdapter(SubjectAdapter):
    name: str = "fake_subject_adapter"
    session: FakeSubjectSession = field(
        default_factory=lambda: FakeSubjectSession(
            turn_results=[
                AdapterTurnResult(
                    user_message="",
                    assistant_message="Please run systemctl status nginx",
                    run_id="run-1",
                    status="completed",
                    terminal_event_type="done",
                    events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
                ),
                AdapterTurnResult(
                    user_message="",
                    assistant_message="Restart nginx and verify the site now.",
                    run_id="run-2",
                    status="completed",
                    terminal_event_type="done",
                    events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
                ),
            ]
        )
    )
    created_subjects: list[str] = field(default_factory=list)

    def create_session(self, benchmark_run_id: str, subject: SubjectSpec) -> SubjectSession:
        del benchmark_run_id
        self.created_subjects.append(subject.subject_name)
        return self.session


@dataclass
class FakeJudge(BlindJudge):
    name: str = "fake_blind_judge"
    requests: list[BlindJudgeRequest] = field(default_factory=list)

    def grade(self, request: BlindJudgeRequest) -> BlindJudgeResult:
        self.requests.append(request)
        return BlindJudgeResult(
            blind_label=request.blind_label,
            summary="Clear troubleshooting session",
            scores={"diagnosis": 4, "actionability": 5},
            raw_response={"blind_label": request.blind_label},
        )


def test_setup_orchestrator_kills_after_second_planner_correction() -> None:
    store = _build_store()
    backend = FakeBackend()
    controller = FakeController(
        send_responses=["applied first sabotage", "applied second sabotage"],
        execute_batches=[
            (
                CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),
            ),
            (
                CommandExecutionResult(command="systemctl is-active nginx", stdout="activating", stderr="", exit_code=0),
            ),
        ],
    )
    planner = FakePlanner(
        review_decisions=[
            PlannerReviewDecision(
                outcome=PlannerReviewOutcome.CORRECT,
                summary="The service is still healthy.",
                correction_instructions=("Actually break the unit override.",),
            ),
            PlannerReviewDecision(
                outcome=PlannerReviewOutcome.CORRECT,
                summary="The service still is not objectively broken.",
                correction_instructions=("Second correction should not get another attempt.",),
            ),
        ]
    )
    orchestrator = ScenarioSetupOrchestrator(
        backend=backend,
        controller_factory=FakeControllerFactory([controller]),
        planner=planner,
        store=store,
    )

    with pytest.raises(ScenarioSetupFailedError):
        orchestrator.run(
            PlannerScenarioRequest(planning_brief="break nginx", target_image="ami-golden"),
            group_id="group-1",
            max_corrections=2,
        )

    scenario_row = store.get_scenario_by_name("nginx-recovery")
    assert scenario_row is not None
    assert scenario_row.lifecycle_status == "failed_setup"
    verified_revision = store.get_current_verified_revision(scenario_row.id)
    assert verified_revision is None
    assert backend.created_broken_images == []
    assert backend.requested_target_images == ["ami-golden"]
    assert backend.configured_runtime_handles == ["staging-nginx-recovery"]
    assert backend.cleared_runtime_handles == []
    assert planner.review_calls == [(0, 0), (1, 1)]
    assert "staging-nginx-recovery" in backend.destroyed_handles
    with store._session_factory() as session:
        failed_setup_run = session.scalars(select(ScenarioSetupRunRecord)).one()
    assert failed_setup_run.backend_metadata_json["resolved_golden_ami_id"] == "ami-resolved"


def test_setup_orchestrator_collects_failure_diagnostics_before_teardown() -> None:
    store = _build_store()
    backend = FakeBackend(diagnostics={"journal": "gateway crashed", "status": "failed"})
    controller = FakeController(send_exception=ConnectionError("gateway closed connection"))
    orchestrator = ScenarioSetupOrchestrator(
        backend=backend,
        controller_factory=FakeControllerFactory([controller]),
        planner=FakePlanner(),
        store=store,
    )

    with pytest.raises(ConnectionError, match="gateway closed connection"):
        orchestrator.run(
            PlannerScenarioRequest(planning_brief="break nginx", target_image="ami-golden"),
            group_id="group-1",
        )

    with store._session_factory() as session:
        failed_setup_run = session.scalars(select(ScenarioSetupRunRecord)).one()
    assert failed_setup_run.status == "failed_infra"
    assert failed_setup_run.backend_metadata_json["failure_exception"] == "ConnectionError: gateway closed connection"
    assert failed_setup_run.backend_metadata_json["failure_diagnostics"] == {
        "journal": "gateway crashed",
        "status": "failed",
    }
    assert failed_setup_run.backend_metadata_json["openclaw_runtime_configured"] is True
    assert backend.collected_failure_handles == ["staging-nginx-recovery"]
    assert "staging-nginx-recovery" in backend.destroyed_handles


def test_setup_orchestrator_authorizes_sandbox_sabotage_in_prompt() -> None:
    store = _build_store()
    backend = FakeBackend()
    controller = FakeController(send_responses=["applied sabotage"])
    orchestrator = ScenarioSetupOrchestrator(
        backend=backend,
        controller_factory=FakeControllerFactory([controller]),
        planner=FakePlanner(),
        store=store,
    )

    orchestrator.run(
        PlannerScenarioRequest(planning_brief="break nginx", target_image="ami-golden"),
        group_id="group-1",
    )

    sabotage_prompt = controller.sent_messages[0]
    assert "disposable eval-harness staging sandbox" in sabotage_prompt
    assert "This is not a live service, production host, or customer environment." in sabotage_prompt
    assert "Destructive changes inside this sandbox are intentional and authorized." in sabotage_prompt
    assert "If the plan requires installing packages" in sabotage_prompt
    assert "Do not refuse just because the requested failure-state preparation breaks the machine." in sabotage_prompt
    assert "Use the normal host execution path for commands." in sabotage_prompt
    assert "Do not rely on OpenClaw elevated exec mode for privileged work." in sabotage_prompt
    assert "prefix it with sudo -n." in sabotage_prompt
    assert "Do not undo, clean up, or repair the sabotage after you verify it." in sabotage_prompt
    assert "Leave the machine in the final broken state when you reply." in sabotage_prompt
    assert "Failure-state plan:" in sabotage_prompt


def test_setup_orchestrator_fails_fast_when_setup_agent_refuses_authorized_sabotage() -> None:
    store = _build_store()
    backend = FakeBackend(diagnostics={"journal": "gateway healthy", "status": "running"})
    controller = FakeController(send_responses=["I can't help break the machine in that way."])
    orchestrator = ScenarioSetupOrchestrator(
        backend=backend,
        controller_factory=FakeControllerFactory([controller]),
        planner=FakePlanner(),
        store=store,
    )

    with pytest.raises(ScenarioSetupFailedError, match="could not apply authorized sabotage"):
        orchestrator.run(
            PlannerScenarioRequest(planning_brief="break nginx", target_image="ami-golden"),
            group_id="group-1",
        )

    with store._session_factory() as session:
        failed_setup_run = session.scalars(select(ScenarioSetupRunRecord)).one()
    assert failed_setup_run.status == "failed_infra"
    assert failed_setup_run.failure_reason == "setup_agent_refused_authorized_sabotage"
    assert failed_setup_run.backend_metadata_json["sabotage_refusal_detected"] is True
    assert failed_setup_run.backend_metadata_json["failure_exception"].startswith("ScenarioSetupFailedError:")


def test_setup_orchestrator_marks_sandbox_runtime_block_as_failed_infra() -> None:
    store = _build_store()
    backend = FakeBackend(diagnostics={"journal": "gateway healthy", "status": "running"})
    controller = FakeController(
        send_responses=[
            "Blocked by the sandbox, not by the plan.\n"
            "- root filesystem is read-only.\n"
            "- elevated exec is disabled here.\n"
            "If you can give me a writable/root-enabled sandbox, I can do the exact sabotage sequence.\n"
        ]
    )
    orchestrator = ScenarioSetupOrchestrator(
        backend=backend,
        controller_factory=FakeControllerFactory([controller]),
        planner=FakePlanner(),
        store=store,
    )

    with pytest.raises(ScenarioSetupFailedError, match="could not apply authorized sabotage"):
        orchestrator.run(
            PlannerScenarioRequest(planning_brief="break nginx", target_image="ami-golden"),
            group_id="group-1",
        )

    with store._session_factory() as session:
        failed_setup_run = session.scalars(select(ScenarioSetupRunRecord)).one()
    assert failed_setup_run.status == "failed_infra"
    assert failed_setup_run.failure_reason == "setup_agent_blocked_by_runtime"
    assert failed_setup_run.backend_metadata_json["sabotage_runtime_block_detected"] is True


def test_setup_orchestrator_marks_sandbox_permissions_runtime_block_as_failed_infra() -> None:
    store = _build_store()
    backend = FakeBackend(diagnostics={"journal": "gateway healthy", "status": "running"})
    controller = FakeController(
        send_responses=[
            "Blocked by sandbox permissions: this environment has no nginx installed, apt writes are permission-denied, "
            "and binding to port 80 fails as non-root."
        ]
    )
    orchestrator = ScenarioSetupOrchestrator(
        backend=backend,
        controller_factory=FakeControllerFactory([controller]),
        planner=FakePlanner(),
        store=store,
    )

    with pytest.raises(ScenarioSetupFailedError, match="could not apply authorized sabotage"):
        orchestrator.run(
            PlannerScenarioRequest(planning_brief="break nginx", target_image="ami-golden"),
            group_id="group-1",
        )

    with store._session_factory() as session:
        failed_setup_run = session.scalars(select(ScenarioSetupRunRecord)).one()
    assert failed_setup_run.status == "failed_infra"
    assert failed_setup_run.failure_reason == "setup_agent_blocked_by_runtime"
    assert failed_setup_run.backend_metadata_json["sabotage_runtime_block_detected"] is True


def test_planner_review_decision_normalizes_string_correction_instructions() -> None:
    decision = PlannerReviewDecision.from_dict(
        {
            "outcome": "correct",
            "summary": "Need to retry",
            "correction_instructions": "Install nginx and retry sabotage.",
        }
    )

    assert decision.correction_instructions == ("Install nginx and retry sabotage.",)


def test_benchmark_orchestrator_keeps_proxy_blind_and_records_objective_repair() -> None:
    store = _build_store()
    scenario = store.create_scenario(title="Nginx recovery", scenario_name_hint="nginx-recovery")
    revision = store.create_scenario_revision(
        scenario_id=scenario.id,
        target_image="ami-golden",
        summary="Recover nginx",
        what_it_tests={"items": ["systemd recovery"]},
        observable_problem_statement="The website is down and nginx will not start.",
        sabotage_plan={"steps": ["Break nginx with a bad unit override."]},
        verification_plan={"probes": [{"name": "broken", "command": "true", "expected_exit_code": 0}]},
        judge_rubric={"items": ["diagnosis", "actionability"]},
        planner_metadata={
            "repair_checks": [
                {
                    "name": "nginx-fixed",
                    "command": "systemctl is-active nginx",
                    "expected_substrings": ["active"],
                }
            ],
            "turn_budget": 3,
        },
    )
    setup = store.create_setup_run(scenario_revision_id=revision.id, status="running")
    store.update_setup_run_status(
        setup_run_id=setup.id,
        status="verified",
        broken_image_id="ami-broken",
        planner_approved=True,
    )
    store.upsert_subject(
        subject_name="system-a",
        adapter_type="fake_adapter",
        display_name="System A",
        adapter_config={"max_turns": 3},
    )

    clone_controller = FakeController(
        send_responses=["I checked and `systemctl status nginx` shows failure."],
        execute_batches=[
            (
                CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),
            ),
            (
                CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),
            ),
        ],
    )
    backend = FakeBackend()
    adapter = FakeSubjectAdapter()
    orchestrator = BenchmarkRunOrchestrator(
        backend=backend,
        controller_factory=FakeControllerFactory([clone_controller]),
        subject_adapters={"fake_adapter": adapter},
        store=store,
    )

    result = orchestrator.run(
        scenario_revision_id=revision.id,
        verified_setup_run_id=setup.id,
        user_proxy_agent_id="user_proxy_agent",
        verification_agent_id="verification_executor",
    )

    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)
    assert len(evaluation_runs) == 1
    assert evaluation_runs[0].repair_success is True
    assert evaluation_runs[0].status == "completed"
    assert "bad unit override" not in clone_controller.sent_messages[0].lower()
    assert backend.configured_runtime_handles == ["clone-system-a"]


def test_benchmark_orchestrator_proxy_approval_leak_skips_turn_after_two_leaks() -> None:
    """When the proxy returns /approve tokens twice in a row the turn is skipped."""
    store = _build_store()
    scenario = store.create_scenario(title="Nginx recovery", scenario_name_hint="nginx-recovery")
    revision = store.create_scenario_revision(
        scenario_id=scenario.id,
        target_image="ami-golden",
        summary="Recover nginx",
        what_it_tests={"items": ["systemd recovery"]},
        observable_problem_statement="The website is down and nginx will not start.",
        sabotage_plan={"steps": ["Break nginx with a bad unit override."]},
        verification_plan={"probes": [{"name": "broken", "command": "true", "expected_exit_code": 0}]},
        judge_rubric={"items": ["diagnosis", "actionability"]},
        planner_metadata={
            "repair_checks": [
                {
                    "name": "nginx-fixed",
                    "command": "systemctl is-active nginx",
                    "expected_substrings": ["active"],
                }
            ],
            "turn_budget": 3,
        },
    )
    setup = store.create_setup_run(scenario_revision_id=revision.id, status="running")
    store.update_setup_run_status(
        setup_run_id=setup.id,
        status="verified",
        broken_image_id="ami-broken",
        planner_approved=True,
    )
    store.upsert_subject(
        subject_name="system-a",
        adapter_type="fake_adapter",
        display_name="System A",
        adapter_config={"max_turns": 3},
    )

    clone_controller = FakeController(
        send_responses=[
            # First proxy call: approval leak
            "/approve 70068ec3 allow-once",
            # Re-prompt (reminder): still leaking
            "/approve 70068ec3 allow-once",
            # Second turn proxy call: clean reply
            "I can see nginx is still not running.",
        ],
        execute_batches=[
            # Turn 1 repair check: not fixed
            (
                CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),
            ),
            # Turn 2 repair check: fixed
            (
                CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),
            ),
            # Turn 3 repair check (if reached): fixed
            (
                CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),
            ),
        ],
    )
    session = FakeSubjectSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message="Please run `systemctl status nginx` and show me the output.",
                run_id="run-1",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
            AdapterTurnResult(
                user_message="",
                assistant_message="Fixed. nginx is active now.",
                run_id="run-2",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )
    adapter = FakeSubjectAdapter(session=session)
    orchestrator = BenchmarkRunOrchestrator(
        backend=FakeBackend(),
        controller_factory=FakeControllerFactory([clone_controller]),
        subject_adapters={"fake_adapter": adapter},
        store=store,
    )

    result = orchestrator.run(
        scenario_revision_id=revision.id,
        verified_setup_run_id=setup.id,
        user_proxy_agent_id="user_proxy_agent",
        verification_agent_id="verification_executor",
    )

    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)
    assert len(evaluation_runs) == 1
    assert evaluation_runs[0].repair_success is True

    # Turn 1 should have been skipped (approval leak suppressed) — a skipped_turn event recorded
    events = store.list_evaluation_events(evaluation_runs[0].id)
    skipped = [e for e in events if e.event_kind == "skipped_turn"]
    assert len(skipped) == 1
    assert skipped[0].payload_json["reason"] == "proxy_approval_leak_suppressed"


def test_benchmark_orchestrator_marks_run_failed_when_clone_launch_raises() -> None:
    store = _build_store()
    scenario = store.create_scenario(title="Nginx recovery", scenario_name_hint="nginx-recovery")
    revision = store.create_scenario_revision(
        scenario_id=scenario.id,
        target_image="ami-golden",
        summary="Recover nginx",
        what_it_tests={"items": ["systemd recovery"]},
        observable_problem_statement="The website is down and nginx will not start.",
        sabotage_plan={"steps": ["Break nginx with a bad unit override."]},
        verification_plan={"probes": [{"name": "broken", "command": "true", "expected_exit_code": 0}]},
        judge_rubric={"items": ["diagnosis"]},
        planner_metadata={
            "repair_checks": [
                {
                    "name": "nginx-fixed",
                    "command": "systemctl is-active nginx",
                    "expected_substrings": ["active"],
                }
            ],
            "turn_budget": 3,
        },
    )
    setup = store.create_setup_run(scenario_revision_id=revision.id, status="running")
    store.update_setup_run_status(
        setup_run_id=setup.id,
        status="verified",
        broken_image_id="ami-broken",
        planner_approved=True,
    )
    store.upsert_subject(
        subject_name="system-a",
        adapter_type="fake_adapter",
        display_name="System A",
        adapter_config={"max_turns": 3},
    )

    class FailingBackend(FakeBackend):
        def launch_subject_clones(self, group_id, scenario_id, broken_image_id, subject_names):
            del group_id, scenario_id, broken_image_id, subject_names
            raise RuntimeError("EC2 quota exceeded")

    backend = FailingBackend()
    orchestrator = BenchmarkRunOrchestrator(
        backend=backend,
        controller_factory=FakeControllerFactory([]),
        subject_adapters={"fake_adapter": FakeSubjectAdapter()},
        store=store,
    )

    with pytest.raises(RuntimeError, match="EC2 quota exceeded"):
        orchestrator.run(
            scenario_revision_id=revision.id,
            verified_setup_run_id=setup.id,
            user_proxy_agent_id="user_proxy_agent",
            verification_agent_id="verification_executor",
        )

    benchmark_runs = store.list_benchmark_runs_for_revision(revision.id) if hasattr(store, "list_benchmark_runs_for_revision") else []
    if benchmark_runs:
        assert benchmark_runs[0].status == "interrupted"
    else:
        # Fall back to direct table inspection — status must not be left as "running".
        from eval_harness.persistence.postgres_models import BenchmarkRunRecord
        with store._session_factory() as session:
            row = session.scalars(select(BenchmarkRunRecord)).one()
        assert row.status == "interrupted"


def test_benchmark_orchestrator_marks_interrupting_runs_failed_and_aborts_session() -> None:
    store = _build_store()
    scenario = store.create_scenario(title="Nginx recovery", scenario_name_hint="nginx-recovery")
    revision = store.create_scenario_revision(
        scenario_id=scenario.id,
        target_image="ami-golden",
        summary="Recover nginx",
        what_it_tests={"items": ["systemd recovery"]},
        observable_problem_statement="The website is down and nginx will not start.",
        sabotage_plan={"steps": ["Break nginx with a bad unit override."]},
        verification_plan={"probes": [{"name": "broken", "command": "true", "expected_exit_code": 0}]},
        judge_rubric={"items": ["diagnosis"]},
        planner_metadata={
            "repair_checks": [
                {
                    "name": "nginx-fixed",
                    "command": "systemctl is-active nginx",
                    "expected_substrings": ["active"],
                }
            ],
            "turn_budget": 3,
        },
    )
    setup = store.create_setup_run(scenario_revision_id=revision.id, status="running")
    store.update_setup_run_status(
        setup_run_id=setup.id,
        status="verified",
        broken_image_id="ami-broken",
        planner_approved=True,
    )
    store.upsert_subject(
        subject_name="system-a",
        adapter_type="fake_adapter",
        display_name="System A",
        adapter_config={"max_turns": 3},
    )

    interrupting_session = FakeSubjectSession(turn_results=[])

    def _raise_interrupt(message: str) -> AdapterTurnResult:
        del message
        raise KeyboardInterrupt("stop benchmark")

    interrupting_session.submit_user_message = _raise_interrupt  # type: ignore[method-assign]
    adapter = FakeSubjectAdapter(session=interrupting_session)
    orchestrator = BenchmarkRunOrchestrator(
        backend=FakeBackend(),
        controller_factory=FakeControllerFactory([FakeController()]),
        subject_adapters={"fake_adapter": adapter},
        store=store,
    )

    with pytest.raises(KeyboardInterrupt, match="stop benchmark"):
        orchestrator.run(
            scenario_revision_id=revision.id,
            verified_setup_run_id=setup.id,
            user_proxy_agent_id="user_proxy_agent",
            verification_agent_id="verification_executor",
        )

    from eval_harness.persistence.postgres_models import BenchmarkRunRecord, EvaluationRunRecord

    with store._session_factory() as session:
        benchmark_row = session.scalars(select(BenchmarkRunRecord)).one()
        evaluation_row = session.scalars(select(EvaluationRunRecord)).one()
    assert benchmark_row.status == "interrupted"
    assert benchmark_row.finished_at is not None
    assert evaluation_row.status == "failed"
    assert evaluation_row.finished_at is not None
    assert evaluation_row.resolution_result_json["exception_type"] == "KeyboardInterrupt"
    assert interrupting_session.abort_called is True


def test_setup_orchestrator_clears_runtime_before_creating_broken_image() -> None:
    store = _build_store()
    backend = FakeBackend()
    controller = FakeController(send_responses=["applied sabotage"])
    orchestrator = ScenarioSetupOrchestrator(
        backend=backend,
        controller_factory=FakeControllerFactory([controller]),
        planner=FakePlanner(),
        store=store,
    )

    result = orchestrator.run(
        PlannerScenarioRequest(planning_brief="break nginx", target_image="ami-golden"),
        group_id="group-1",
    )

    assert result.broken_image_id == "broken-group-1"
    assert backend.configured_runtime_handles == ["staging-nginx-recovery"]
    assert backend.cleared_runtime_handles == ["staging-nginx-recovery"]


def test_setup_orchestrator_persists_broken_image_progress_metadata() -> None:
    store = _build_store()
    backend = FakeBackend(
        broken_image_wait_progress=[
            {
                "broken_image_state": "pending",
                "broken_image_last_checked_at": datetime.now(timezone.utc).isoformat(),
                "broken_image_wait_elapsed_seconds": 0,
                "broken_image_wait_timeout_seconds": 1800,
            },
            {
                "broken_image_state": "available",
                "broken_image_last_checked_at": datetime.now(timezone.utc).isoformat(),
                "broken_image_wait_elapsed_seconds": 15,
                "broken_image_wait_timeout_seconds": 1800,
            },
        ]
    )
    controller = FakeController(send_responses=["applied sabotage"])
    orchestrator = ScenarioSetupOrchestrator(
        backend=backend,
        controller_factory=FakeControllerFactory([controller]),
        planner=FakePlanner(),
        store=store,
    )

    result = orchestrator.run(
        PlannerScenarioRequest(planning_brief="break nginx", target_image="ami-golden"),
        group_id="group-1",
    )

    setup_run = store.get_setup_run(result.setup_run_id)
    assert setup_run is not None
    assert setup_run.status == ScenarioSetupStatus.VERIFIED.value
    assert setup_run.broken_image_id == "broken-group-1"
    assert setup_run.backend_metadata_json["broken_image_id"] == "broken-group-1"
    assert setup_run.backend_metadata_json["broken_image_state"] == "available"
    assert setup_run.backend_metadata_json["broken_image_requested_at"]
    assert setup_run.backend_metadata_json["broken_image_last_checked_at"]
    assert setup_run.planner_approved_at is not None


def test_setup_orchestrator_marks_broken_image_timeout_as_failed_infra() -> None:
    store = _build_store()
    backend = FakeBackend(
        broken_image_wait_progress=[
            {
                "broken_image_state": "pending",
                "broken_image_last_checked_at": datetime.now(timezone.utc).isoformat(),
                "broken_image_wait_elapsed_seconds": 0,
                "broken_image_wait_timeout_seconds": 1800,
            }
        ],
        broken_image_wait_exception=TimeoutError("ami wait timed out"),
    )
    controller = FakeController(send_responses=["applied sabotage"])
    orchestrator = ScenarioSetupOrchestrator(
        backend=backend,
        controller_factory=FakeControllerFactory([controller]),
        planner=FakePlanner(),
        store=store,
    )

    with pytest.raises(ScenarioSetupFailedError, match="Timed out waiting for broken image creation"):
        orchestrator.run(
            PlannerScenarioRequest(planning_brief="break nginx", target_image="ami-golden"),
            group_id="group-1",
        )

    with store._session_factory() as session:
        setup_run = session.scalars(select(ScenarioSetupRunRecord)).one()
    assert setup_run.status == ScenarioSetupStatus.FAILED_INFRA.value
    assert setup_run.failure_reason == "broken_image_creation_timeout"
    assert setup_run.broken_image_id == "broken-group-1"
    assert setup_run.backend_metadata_json["broken_image_state"] == "timeout"


def test_judge_job_blinds_subject_identity() -> None:
    store = _build_store()
    scenario = store.create_scenario(title="Nginx recovery", scenario_name_hint="nginx-recovery")
    revision = store.create_scenario_revision(
        scenario_id=scenario.id,
        target_image="ami-golden",
        summary="Recover nginx",
        what_it_tests={"items": ["systemd recovery"]},
        observable_problem_statement="The website is down and nginx will not start.",
        sabotage_plan={"steps": ["Break nginx with a bad unit override."]},
        verification_plan={"probes": [{"name": "broken", "command": "true", "expected_exit_code": 0}]},
        judge_rubric={"items": ["diagnosis", "actionability"]},
        planner_metadata={"repair_checks": [], "turn_budget": 2},
    )
    setup = store.create_setup_run(scenario_revision_id=revision.id, status="running")
    store.update_setup_run_status(
        setup_run_id=setup.id,
        status="verified",
        broken_image_id="ami-broken",
        planner_approved=True,
    )
    subject = store.upsert_subject(
        subject_name="system-a",
        adapter_type="fake_adapter",
        display_name="System A",
        adapter_config={},
    )
    benchmark = store.create_benchmark_run(
        scenario_revision_id=revision.id,
        verified_setup_run_id=setup.id,
        subject_ids=[subject.id],
    )
    evaluation = store.create_evaluation_run(
        benchmark_run_id=benchmark.id,
        subject_id=subject.id,
        clone_handle_id="clone-1",
        status="completed",
    )
    store.append_evaluation_event(
        evaluation_run_id=evaluation.id,
        seq=1,
        actor_role="user_proxy",
        event_kind="message",
        payload={"role": "user", "content": "The website is down."},
    )
    store.append_evaluation_event(
        evaluation_run_id=evaluation.id,
        seq=2,
        actor_role="subject",
        event_kind="message",
        payload={"role": "assistant", "content": "Please run systemctl status nginx", "metadata": {"subject_name": "system-a"}},
    )

    judge = FakeJudge()
    result = JudgeJobOrchestrator(judge=judge, store=store).run(benchmark_run_id=benchmark.id)

    assert len(result.judge_item_ids) == 1
    assert len(judge.requests) == 1
    request = judge.requests[0]
    assert request.blind_label == "candidate-1"
    assert "system-a" not in json.dumps(request.to_dict())


# ---------------------------------------------------------------------------
# Helpers shared by the new benchmark proxy tests
# ---------------------------------------------------------------------------


def _build_benchmark_store_and_revision(
    *,
    observable_problem_statement: str = "The website is down and nginx will not start.",
    turn_budget: int = 3,
    repair_check_command: str = "systemctl is-active nginx",
    repair_check_expected: list[str] | None = None,
    subject_max_turns: int = 3,
) -> tuple[EvalHarnessStore, object, object]:
    """Return (store, revision, setup) wired up and ready for a benchmark run."""
    if repair_check_expected is None:
        repair_check_expected = ["active"]
    store = _build_store()
    scenario = store.create_scenario(title="Test scenario", scenario_name_hint="test-scenario")
    revision = store.create_scenario_revision(
        scenario_id=scenario.id,
        target_image="ami-golden",
        summary="Test",
        what_it_tests={"items": ["recovery"]},
        observable_problem_statement=observable_problem_statement,
        sabotage_plan={"steps": ["Break it."]},
        verification_plan={"probes": [{"name": "broken", "command": "true", "expected_exit_code": 0}]},
        judge_rubric={"items": ["diagnosis"]},
        planner_metadata={
            "repair_checks": [
                {
                    "name": "fixed",
                    "command": repair_check_command,
                    "expected_substrings": repair_check_expected,
                }
            ],
            "turn_budget": turn_budget,
        },
    )
    setup = store.create_setup_run(scenario_revision_id=revision.id, status="running")
    store.update_setup_run_status(
        setup_run_id=setup.id,
        status="verified",
        broken_image_id="ami-broken",
        planner_approved=True,
    )
    store.upsert_subject(
        subject_name="system-a",
        adapter_type="fake_adapter",
        display_name="System A",
        adapter_config={"max_turns": subject_max_turns},
    )
    return store, revision, setup


def _run_benchmark(
    store: EvalHarnessStore,
    revision,
    setup,
    clone_controller: FakeController,
    session: FakeSubjectSession | None = None,
) -> object:
    """Run the benchmark orchestrator and return the result."""
    if session is None:
        session = FakeSubjectSession(
            turn_results=[
                AdapterTurnResult(
                    user_message="",
                    assistant_message="Please run systemctl status nginx.",
                    run_id="run-1",
                    status="completed",
                    terminal_event_type="done",
                    events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
                ),
            ]
        )
    adapter = FakeSubjectAdapter(session=session)
    orchestrator = BenchmarkRunOrchestrator(
        backend=FakeBackend(),
        controller_factory=FakeControllerFactory([clone_controller]),
        subject_adapters={"fake_adapter": adapter},
        store=store,
    )
    return orchestrator.run(
        scenario_revision_id=revision.id,
        verified_setup_run_id=setup.id,
        user_proxy_agent_id="user_proxy_agent",
        verification_agent_id="verification_executor",
    )


# ---------------------------------------------------------------------------
# SSH scenario fixture (non-nginx, Test J)
# ---------------------------------------------------------------------------


@pytest.fixture
def ssh_scenario_spec() -> ScenarioSpec:
    """A non-nginx scenario for testing SSH repair logic."""
    return ScenarioSpec(
        scenario_name="ssh-recovery",
        title="SSH recovery",
        summary="Recover a broken SSH service",
        what_it_tests=("sshd recovery", "config inspection"),
        target_image="ami-ssh-golden",
        observable_problem_statement="I cannot SSH into the server at all.",
        sabotage_procedure=("Break sshd by injecting a bad config option.",),
        verification_probes=(
            VerificationCheck(
                name="sshd-broken",
                command="systemctl is-active sshd",
                expected_substrings=("failed",),
            ),
        ),
        repair_checks=(
            VerificationCheck(
                name="sshd-fixed",
                command="systemctl is-active sshd",
                expected_substrings=("active",),
            ),
        ),
        judge_rubric=("diagnosis", "config-repair"),
        context_seed=(TurnSeed(role="system", content="Use concise shell reasoning."),),
        turn_budget=5,
    )


# ---------------------------------------------------------------------------
# Test C: proxy system prompt is passed to controller.send() on each turn
# ---------------------------------------------------------------------------


def test_benchmark_proxy_sends_system_prompt_on_each_turn() -> None:
    """Test C: Every proxy send() call includes system_prompt containing the problem statement."""
    problem = "The website is down and nginx will not start."
    store, revision, setup = _build_benchmark_store_and_revision(
        observable_problem_statement=problem,
        turn_budget=2,
        subject_max_turns=2,
    )

    # Track system_prompts received by the controller
    received_system_prompts: list[str | None] = []

    class TrackingController(FakeController):
        def send(self, *, agent_id: str, message: str, session_key: str | None = None, system_prompt: str | None = None) -> str:
            received_system_prompts.append(system_prompt)
            return super().send(agent_id=agent_id, message=message, session_key=session_key, system_prompt=system_prompt)

    clone_controller = TrackingController(
        send_responses=[
            "I checked the logs and nginx seems broken.",
            "The service still seems broken.",
        ],
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
        ],
    )
    session = FakeSubjectSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message="Run systemctl status nginx.",
                run_id="run-1",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
            AdapterTurnResult(
                user_message="",
                assistant_message="Run journalctl -xe.",
                run_id="run-2",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )
    _run_benchmark(store, revision, setup, clone_controller, session)

    # Every proxy send() call should have a system_prompt containing the problem statement
    assert len(received_system_prompts) >= 1
    for sp in received_system_prompts:
        assert sp is not None, "system_prompt must not be None for proxy send() calls"
        assert problem in sp


# ---------------------------------------------------------------------------
# Test D: HOST_RUN commands are executed and output appended to user message
# ---------------------------------------------------------------------------


def test_benchmark_host_run_commands_executed_and_appended() -> None:
    """Test D: proxy replies with a host-run block → commands executed, output appended to subject message."""
    problem = "The website is down and nginx will not start."
    store, revision, setup = _build_benchmark_store_and_revision(
        observable_problem_statement=problem,
        turn_budget=2,
        subject_max_turns=2,
    )

    execute_batches_used: list[tuple] = []
    submitted_user_messages: list[str] = []

    class TrackingController(FakeController):
        def execute_commands(
            self,
            commands: tuple[str, ...],
            *,
            agent_id: str,
            session_key: str | None = None,
        ) -> tuple[CommandExecutionResult, ...]:
            result = super().execute_commands(commands, agent_id=agent_id, session_key=session_key)
            execute_batches_used.append((agent_id, commands, result))
            return result

    clone_controller = TrackingController(
        send_responses=[
            # First proxy response contains a HOST_RUN block
            "I see the issue. Can you run:\n```host-run\nsudo systemctl status nginx\n```",
            # Second proxy response (after exec result appended): clean reply
            "I see the service is failed. Please restart it.",
        ],
        execute_batches=[
            # Repair check after first subject turn: not fixed
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            # proxy-exec for host-run command
            (CommandExecutionResult(command="sudo systemctl status nginx", stdout="● nginx.service - NGINX\n   Loaded: loaded", stderr="", exit_code=0),),
            # Repair check after second subject turn: fixed
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),),
        ],
    )
    session = FakeSubjectSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message="Run systemctl status nginx and show me.",
                run_id="run-1",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
            AdapterTurnResult(
                user_message="",
                assistant_message="OK run systemctl restart nginx.",
                run_id="run-2",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )

    # Capture submitted user messages
    original_submit = session.submit_user_message
    def tracking_submit(message: str):
        submitted_user_messages.append(message)
        return original_submit(message)
    session.submit_user_message = tracking_submit  # type: ignore[method-assign]

    result = _run_benchmark(store, revision, setup, clone_controller, session)
    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)
    assert len(evaluation_runs) == 1

    # proxy-exec agent_id must be "proxy-exec"
    proxy_exec_calls = [entry for entry in execute_batches_used if entry[0] == "proxy-exec"]
    assert len(proxy_exec_calls) >= 1, "execute_commands should have been called with agent_id='proxy-exec'"
    assert proxy_exec_calls[0][1] == ("sudo systemctl status nginx",)

    # The evaluation store must have a user_proxy_exec/command_result event
    events = store.list_evaluation_events(evaluation_runs[0].id)
    exec_events = [e for e in events if e.actor_role == "user_proxy_exec" and e.event_kind == "command_result"]
    assert len(exec_events) >= 1

    # The second user message submitted to the subject should contain the rendered output
    assert len(submitted_user_messages) >= 2
    second_message = submitted_user_messages[1]
    assert "nginx.service" in second_message or "● nginx" in second_message or "[exit 0]" in second_message


# ---------------------------------------------------------------------------
# Test E: /approve in proxy reply is blocked and re-prompted, skipped_turn event
# ---------------------------------------------------------------------------


def test_benchmark_proxy_approval_leak_blocked_and_skipped_turn_event() -> None:
    """Test E: proxy returns /approve twice → skipped_turn event, approval not submitted to subject."""
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=3, subject_max_turns=3)

    clone_controller = FakeController(
        send_responses=[
            # First proxy call: approval leak
            "/approve 70068ec3 allow-once",
            # Re-prompt (reminder): still leaking
            "/approve 70068ec3 allow-once",
            # Second turn proxy call: clean reply
            "I can see nginx is still not running.",
        ],
        execute_batches=[
            # Turn 1 repair check: not fixed
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            # Turn 2 repair check: fixed
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),),
            # Turn 3 repair check (if reached): fixed
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),),
        ],
    )
    session = FakeSubjectSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message="Please run systemctl status nginx.",
                run_id="run-1",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
            AdapterTurnResult(
                user_message="",
                assistant_message="Fixed. nginx is active now.",
                run_id="run-2",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )
    submitted_messages: list[str] = []
    original_submit = session.submit_user_message
    def tracking_submit(message: str):
        submitted_messages.append(message)
        return original_submit(message)
    session.submit_user_message = tracking_submit  # type: ignore[method-assign]

    result = _run_benchmark(store, revision, setup, clone_controller, session)
    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)
    assert len(evaluation_runs) == 1

    # A skipped_turn event must have been recorded with the correct reason
    events = store.list_evaluation_events(evaluation_runs[0].id)
    skipped = [e for e in events if e.event_kind == "skipped_turn"]
    assert len(skipped) >= 1
    assert skipped[0].payload_json["reason"] == "proxy_approval_leak_suppressed"

    # The subject session was still called (the loop continued to the next iteration)
    assert len(submitted_messages) >= 1


# ---------------------------------------------------------------------------
# Test F: REPAIR_CONFIRMED triggers early exit with repair_success=True
# ---------------------------------------------------------------------------


def test_benchmark_repair_confirmed_triggers_early_exit() -> None:
    """Test F: proxy replies REPAIR_CONFIRMED → eval completes early, repair_success=True."""
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=5, subject_max_turns=5)

    # We need a session with at least enough turns
    session = FakeSubjectSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message="Run systemctl restart nginx.",
                run_id="run-1",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )
    submitted_messages: list[str] = []
    original_submit = session.submit_user_message
    def tracking_submit(message: str):
        submitted_messages.append(message)
        return original_submit(message)
    session.submit_user_message = tracking_submit  # type: ignore[method-assign]

    clone_controller = FakeController(
        send_responses=[
            # Proxy says REPAIR_CONFIRMED after the first subject turn
            "REPAIR_CONFIRMED",
        ],
        execute_batches=[
            # First repair check (after subject turn): service still failing
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            # REPAIR_CONFIRMED repair check: now passing
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),),
        ],
    )
    result = _run_benchmark(store, revision, setup, clone_controller, session)
    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)
    assert len(evaluation_runs) == 1
    assert evaluation_runs[0].repair_success is True
    assert evaluation_runs[0].status == "completed"

    # After REPAIR_CONFIRMED the subject session should receive no further messages
    assert len(submitted_messages) == 1, (
        f"Subject should only receive one message (before REPAIR_CONFIRMED), got {len(submitted_messages)}"
    )


# ---------------------------------------------------------------------------
# Test G: proxy stall detection → failed with reason=proxy_stalled
# ---------------------------------------------------------------------------


def test_benchmark_proxy_stall_detection_marks_failed() -> None:
    """Test G: proxy returns plain text (no HOST_RUN, no REPAIR_CONFIRMED) 3 times → proxy_stalled."""
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=10, subject_max_turns=10)

    clone_controller = FakeController(
        send_responses=[
            # Three plain non-productive proxy replies
            "I am not sure what to do.",
            "Maybe you should restart the machine?",
            "I don't know, sorry.",
            # Fallback if loop continues (should not be reached)
            "Extra reply",
        ],
        execute_batches=[
            # All repair checks: not fixed
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
        ],
    )
    session = FakeSubjectSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message="Run systemctl status nginx.",
                run_id="run-1",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
            AdapterTurnResult(
                user_message="",
                assistant_message="I think nginx is really broken.",
                run_id="run-2",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
            AdapterTurnResult(
                user_message="",
                assistant_message="Maybe try restarting.",
                run_id="run-3",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )
    result = _run_benchmark(store, revision, setup, clone_controller, session)
    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)
    assert len(evaluation_runs) == 1
    eval_run = evaluation_runs[0]
    assert eval_run.status == "failed"
    assert eval_run.repair_success is False
    resolution = eval_run.resolution_result_json
    assert resolution.get("reason") == "proxy_stalled", (
        f"Expected reason='proxy_stalled', got {resolution!r}"
    )


# ---------------------------------------------------------------------------
# Test H: turn budget is min(scenario.turn_budget, subject max_turns)
# ---------------------------------------------------------------------------


def test_benchmark_turn_budget_uses_scenario_when_smaller() -> None:
    """Test H (part 1): scenario.turn_budget=3, subject max_turns=8 → at most 3 turns."""
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=3, subject_max_turns=8)

    # Proxy always returns non-productive text → loop limited by scenario budget
    clone_controller = FakeController(
        send_responses=["Plain text"] * 10,
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
        ] * 10,
    )
    session = FakeSubjectSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message=f"reply-{i}",
                run_id=f"run-{i}",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            )
            for i in range(10)
        ]
    )
    submitted: list[str] = []
    original = session.submit_user_message
    def track(msg):
        submitted.append(msg)
        return original(msg)
    session.submit_user_message = track  # type: ignore[method-assign]

    _run_benchmark(store, revision, setup, clone_controller, session)

    assert len(submitted) <= 3, f"Expected at most 3 turns (scenario budget), got {len(submitted)}"


def test_benchmark_turn_budget_uses_subject_when_smaller() -> None:
    """Test H (part 2): scenario.turn_budget=10, subject max_turns=5 → at most 5 turns."""
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=10, subject_max_turns=5)

    clone_controller = FakeController(
        send_responses=["Plain text"] * 15,
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
        ] * 15,
    )
    session = FakeSubjectSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message=f"reply-{i}",
                run_id=f"run-{i}",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            )
            for i in range(15)
        ]
    )
    submitted: list[str] = []
    original = session.submit_user_message
    def track(msg):
        submitted.append(msg)
        return original(msg)
    session.submit_user_message = track  # type: ignore[method-assign]

    _run_benchmark(store, revision, setup, clone_controller, session)

    assert len(submitted) <= 5, f"Expected at most 5 turns (subject budget), got {len(submitted)}"


# ---------------------------------------------------------------------------
# Test I: _repair_checks_pass returns True only when all checks pass
# ---------------------------------------------------------------------------


def test_repair_checks_pass_with_all_passing_checks() -> None:
    """Test I: all checks satisfied → True."""
    store = _build_store()
    orchestrator = BenchmarkRunOrchestrator(
        backend=FakeBackend(),
        controller_factory=FakeControllerFactory([]),
        subject_adapters={},
        store=store,
    )
    scenario = _scenario()  # has one repair_check: nginx active
    results = (
        CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),
    )
    assert orchestrator._repair_checks_pass(scenario, results) is True


def test_repair_checks_pass_returns_false_when_one_check_fails() -> None:
    """Test I: one failing check → False."""
    store = _build_store()
    orchestrator = BenchmarkRunOrchestrator(
        backend=FakeBackend(),
        controller_factory=FakeControllerFactory([]),
        subject_adapters={},
        store=store,
    )
    scenario = _scenario()
    # stdout "failed" does not contain expected substring "active"
    results = (
        CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),
    )
    assert orchestrator._repair_checks_pass(scenario, results) is False


def test_repair_checks_pass_supports_regex_and_negative_expectations() -> None:
    store = _build_store()
    orchestrator = BenchmarkRunOrchestrator(
        backend=FakeBackend(),
        controller_factory=FakeControllerFactory([]),
        subject_adapters={},
        store=store,
    )
    scenario = ScenarioSpec(
        scenario_name="http-recovery",
        title="HTTP recovery",
        summary="Recover an HTTP endpoint",
        what_it_tests=("http validation",),
        target_image="ami-http",
        observable_problem_statement="the service is unhealthy",
        sabotage_procedure=("break the service",),
        verification_probes=(
            VerificationCheck(
                name="probe",
                command="curl -si http://localhost/health",
                expected_regexes=(r"HTTP/1\.[01] 5\d\d",),
            ),
        ),
        repair_checks=(
            VerificationCheck(
                name="fixed",
                command="curl -si http://localhost/health",
                expected_regexes=(r"HTTP/1\.[01] 200",),
                unexpected_substrings=("Traceback",),
                unexpected_regexes=(r"error[: ]",),
            ),
        ),
        judge_rubric=("diagnosis",),
        turn_budget=3,
    )

    passing = (
        CommandExecutionResult(
            command="curl -si http://localhost/health",
            stdout="HTTP/1.1 200 OK\ncontent-type: text/plain\n\nhealthy",
            stderr="",
            exit_code=0,
        ),
    )
    failing = (
        CommandExecutionResult(
            command="curl -si http://localhost/health",
            stdout="HTTP/1.1 200 OK\n\nhealthy",
            stderr="error: backend not ready",
            exit_code=0,
        ),
    )

    assert orchestrator._repair_checks_pass(scenario, passing) is True
    assert orchestrator._repair_checks_pass(scenario, failing) is False


def test_benchmark_follow_mode_timeout_is_not_counted_as_progress() -> None:
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=5, subject_max_turns=5)
    clone_controller = FakeController(
        send_responses=[
            "Running it now:\n```host-run\njournalctl -fu nginx\n```",
            "Trying again:\n```host-run\njournalctl -fu nginx\n```",
            "Still watching logs:\n```host-run\njournalctl -fu nginx\n```",
        ],
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="journalctl -fu nginx", stdout="", stderr="", exit_code=124),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="journalctl -fu nginx", stdout="", stderr="", exit_code=124),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="journalctl -fu nginx", stdout="", stderr="", exit_code=124),),
        ],
    )
    session = FakeSubjectSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message="Please run journalctl -fu nginx.",
                run_id="run-1",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
            AdapterTurnResult(
                user_message="",
                assistant_message="Keep following the logs for nginx.",
                run_id="run-2",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
            AdapterTurnResult(
                user_message="",
                assistant_message="Follow the logs a bit longer.",
                run_id="run-3",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )

    result = _run_benchmark(store, revision, setup, clone_controller, session)
    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)
    benchmark_run = store.get_benchmark_run(result.benchmark_run_id)

    assert len(evaluation_runs) == 1
    assert evaluation_runs[0].status == "failed"
    assert evaluation_runs[0].resolution_result_json["reason"] == "proxy_stalled"
    assert benchmark_run is not None
    assert benchmark_run.status == "completed_with_failures"


def test_benchmark_closure_reply_triggers_final_verification() -> None:
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=5, subject_max_turns=5)
    session = FakeSubjectSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message="I restarted nginx and the site should be back now.",
                run_id="run-1",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )
    clone_controller = FakeController(
        send_responses=["Thanks, that fixed it. Everything looks good now."],
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),),
        ],
    )

    result = _run_benchmark(store, revision, setup, clone_controller, session)
    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)
    benchmark_run = store.get_benchmark_run(result.benchmark_run_id)

    assert len(evaluation_runs) == 1
    assert evaluation_runs[0].status == "completed"
    assert evaluation_runs[0].repair_success is True
    assert benchmark_run is not None
    assert benchmark_run.status == "completed"


def test_benchmark_completed_with_failures_when_subjects_do_not_repair() -> None:
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=3, subject_max_turns=3)
    clone_controller = FakeController(
        send_responses=[
            "I am not sure what to do.",
            "Maybe try something else?",
            "Still broken here.",
        ],
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
        ],
    )
    session = FakeSubjectSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message="Run systemctl status nginx.",
                run_id="run-1",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
            AdapterTurnResult(
                user_message="",
                assistant_message="Check the logs.",
                run_id="run-2",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
            AdapterTurnResult(
                user_message="",
                assistant_message="I need more info.",
                run_id="run-3",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )

    result = _run_benchmark(store, revision, setup, clone_controller, session)
    benchmark_run = store.get_benchmark_run(result.benchmark_run_id)
    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)

    assert benchmark_run is not None
    assert benchmark_run.status == "completed_with_failures"
    assert benchmark_run.metadata_json["summary"]["repair_success_count"] == 0
    assert benchmark_run.metadata_json["summary"]["failed_evaluation_count"] == 1
    assert evaluation_runs[0].status == "failed"


def test_repair_checks_pass_returns_false_when_no_checks(ssh_scenario_spec: ScenarioSpec) -> None:
    """Test I: empty checks → False (using ssh_scenario_spec fixture, Test J)."""
    store = _build_store()
    orchestrator = BenchmarkRunOrchestrator(
        backend=FakeBackend(),
        controller_factory=FakeControllerFactory([]),
        subject_adapters={},
        store=store,
    )
    # Build a scenario with zero repair checks
    empty_scenario = ScenarioSpec(
        scenario_name=ssh_scenario_spec.scenario_name,
        title=ssh_scenario_spec.title,
        summary=ssh_scenario_spec.summary,
        what_it_tests=ssh_scenario_spec.what_it_tests,
        target_image=ssh_scenario_spec.target_image,
        observable_problem_statement=ssh_scenario_spec.observable_problem_statement,
        sabotage_procedure=ssh_scenario_spec.sabotage_procedure,
        verification_probes=ssh_scenario_spec.verification_probes,
        repair_checks=(),  # empty
        judge_rubric=ssh_scenario_spec.judge_rubric,
        context_seed=ssh_scenario_spec.context_seed,
        turn_budget=ssh_scenario_spec.turn_budget,
    )
    assert orchestrator._repair_checks_pass(empty_scenario, ()) is False


# ---------------------------------------------------------------------------
# Test J (supplemental): ssh_scenario_spec fixture basic shape sanity
# ---------------------------------------------------------------------------


def test_ssh_scenario_spec_fixture(ssh_scenario_spec: ScenarioSpec) -> None:
    """Test J: the ssh_scenario_spec fixture has the expected shape."""
    assert ssh_scenario_spec.scenario_name == "ssh-recovery"
    assert "ssh" in ssh_scenario_spec.observable_problem_statement.lower()
    assert len(ssh_scenario_spec.repair_checks) == 1
    assert ssh_scenario_spec.repair_checks[0].command == "systemctl is-active sshd"
    assert "active" in ssh_scenario_spec.repair_checks[0].expected_substrings
