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
    InitialUserMessageDraft,
    InitialUserMessageReview,
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
from eval_harness.orchestration.user_proxy_llm import UserProxyLLMResponse, UserProxyToolCall
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
    rectification_commands: tuple[str, ...] = ("echo fake rectify",)
    rectification_calls: list[dict] = field(default_factory=list)
    name: str = "fake_planner"

    def generate_scenario(self, request: PlannerScenarioRequest) -> ScenarioSpec:
        del request
        return self.generated_scenario

    def generate_initial_user_message(
        self,
        *,
        scenario: ScenarioSpec,
        hidden_context: dict,
    ) -> InitialUserMessageDraft:
        del hidden_context
        return InitialUserMessageDraft(message=scenario.observable_problem_statement)

    def review_initial_user_message(
        self,
        *,
        scenario: ScenarioSpec,
        draft_message: str,
    ) -> InitialUserMessageReview:
        del scenario
        return InitialUserMessageReview(outcome="approve", notes="ok", final_message=draft_message)

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

    def plan_rectification(
        self,
        scenario,
        *,
        failed_command_results,
        correction_instructions,
        round_index: int,
    ) -> tuple[str, ...]:
        self.rectification_calls.append(
            {
                "round_index": round_index,
                "correction_instructions": correction_instructions,
                "failed_count": len(failed_command_results),
            }
        )
        return self.rectification_commands


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
        agent_id: str = "",
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
            "runtime_configured": True,
        }

    def clear_controller_runtime(self, handle: SandboxHandle) -> dict:
        self.cleared_runtime_handles.append(handle.remote_id)
        return {"runtime_cleared": True}


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


@dataclass
class FakeUserProxyLLM:
    """Scripted user-proxy LLM for tests — returns canned UserProxyLLMResponse objects."""

    responses: list[UserProxyLLMResponse] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def __init__(self, responses: list[UserProxyLLMResponse] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls = []

    def _default_response(self) -> UserProxyLLMResponse:
        return UserProxyLLMResponse(
            content="I see, let me keep looking.",
            tool_calls=(),
            finish_reason="stop",
            response_id=f"resp-{len(self.calls) + 1}",
        )

    def start_turn(self, *, system_prompt, transcript, assistant_reply, tools):
        self.calls.append(
            {
                "phase": "start",
                "system_prompt": system_prompt,
                "transcript": list(transcript),
                "assistant_reply": assistant_reply,
                "tools": tools,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return self._default_response()

    def continue_turn(self, *, system_prompt, previous_response_id, tool_outputs, tools):
        self.calls.append(
            {
                "phase": "continue",
                "system_prompt": system_prompt,
                "previous_response_id": previous_response_id,
                "tool_outputs": list(tool_outputs),
                "tools": tools,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return self._default_response()


def _make_proxy_llm(text_responses: list[str] | None = None) -> FakeUserProxyLLM:
    """Build a FakeUserProxyLLM from a list of plain text replies (no tool calls)."""
    responses = [
        UserProxyLLMResponse(content=txt, tool_calls=(), finish_reason="stop", response_id=f"resp-{index + 1}")
        for index, txt in enumerate(text_responses or [])
    ]
    return FakeUserProxyLLM(responses=responses)


def test_setup_orchestrator_kills_after_corrections_exhausted() -> None:
    """With max_corrections=1 and scrap_budget=0, 2 CORRECT reviews → FAILED."""
    store = _build_store()
    backend = FakeBackend()
    # FSM uses execute_commands for BUILD and VERIFY; no controller.send() is called.
    # Flow: BUILD → VERIFY → REVIEW(correct, cc=0 < 1) → FIX_PLAN → FIX_EXECUTE
    #       → VERIFY → REVIEW(correct, cc=1 >= 1) → SCRAP → FAILED (scrap_budget=0)
    controller = FakeController(
        execute_batches=[
            # BUILD round 0
            (),
            # VERIFY round 0
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),),
            # FIX_EXECUTE round 0
            (),
            # VERIFY round 1
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="activating", stderr="", exit_code=0),),
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
    from eval_harness.orchestration.scenario_fsm import ScenarioBuilderFSM

    fsm = ScenarioBuilderFSM(
        backend=backend,
        controller_factory=FakeControllerFactory([controller]),
        planner=planner,
        store=store,
        request=PlannerScenarioRequest(planning_brief="break nginx", target_image="ami-golden"),
        group_id="group-1",
        max_corrections=1,
        scrap_budget=0,
    )

    with pytest.raises(ScenarioSetupFailedError):
        fsm.run()

    scenario_row = store.get_scenario_by_name("nginx-recovery")
    assert scenario_row is not None
    assert scenario_row.lifecycle_status == "failed_setup"
    verified_revision = store.get_current_verified_revision(scenario_row.id)
    assert verified_revision is None
    assert backend.created_broken_images == []
    assert backend.requested_target_images == ["ami-golden"]
    # FSM skips configure_controller_runtime for SSM path
    assert backend.configured_runtime_handles == []
    assert backend.cleared_runtime_handles == []
    # review_calls: (round_index, correction_count): (0,0) → fix → (1,1) → scrap → fail
    assert planner.review_calls == [(0, 0), (1, 1)]
    assert "staging-nginx-recovery" in backend.destroyed_handles
    with store._session_factory() as session:
        failed_setup_run = session.scalars(select(ScenarioSetupRunRecord)).one()
    assert failed_setup_run.backend_metadata_json["resolved_golden_ami_id"] == "ami-resolved"


def test_setup_orchestrator_collects_failure_diagnostics_before_teardown() -> None:
    store = _build_store()
    backend = FakeBackend(diagnostics={"journal": "gateway crashed", "status": "failed"})

    class RaisingController(FakeController):
        def execute_commands(self, commands, *, agent_id="", session_key=None):
            raise ConnectionError("gateway closed connection")

    controller = RaisingController()
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
    assert backend.collected_failure_handles == ["staging-nginx-recovery"]
    assert "staging-nginx-recovery" in backend.destroyed_handles


def test_setup_orchestrator_runs_sabotage_commands_via_execute_commands() -> None:
    """FSM executes sabotage_procedure via execute_commands, not controller.send()."""
    store = _build_store()
    backend = FakeBackend()
    executed_commands: list[tuple[str, ...]] = []

    class TrackingController(FakeController):
        def execute_commands(self, commands, *, agent_id="", session_key=None):
            executed_commands.append(commands)
            return ()

    controller = TrackingController()
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

    # BUILD: sabotage_procedure; VERIFY: verification_probes
    assert len(executed_commands) >= 2
    # First batch is the sabotage commands from sabotage_procedure
    assert executed_commands[0] == ("Break nginx with a bad unit override.",)
    # controller.send was never used
    assert controller.sent_messages == []


def test_setup_orchestrator_fails_when_execute_commands_raises() -> None:
    """FSM propagates exceptions from execute_commands and marks run as failed_infra."""
    store = _build_store()
    backend = FakeBackend(diagnostics={"journal": "instance unreachable", "status": "failed"})

    class RaisingController(FakeController):
        def execute_commands(self, commands, *, agent_id="", session_key=None):
            raise RuntimeError("SSM command timed out")

    controller = RaisingController()
    orchestrator = ScenarioSetupOrchestrator(
        backend=backend,
        controller_factory=FakeControllerFactory([controller]),
        planner=FakePlanner(),
        store=store,
    )

    with pytest.raises(RuntimeError, match="SSM command timed out"):
        orchestrator.run(
            PlannerScenarioRequest(planning_brief="break nginx", target_image="ami-golden"),
            group_id="group-1",
        )

    with store._session_factory() as session:
        failed_setup_run = session.scalars(select(ScenarioSetupRunRecord)).one()
    assert failed_setup_run.status == "failed_infra"
    assert "staging-nginx-recovery" in backend.destroyed_handles


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
        verification_plan={
            "probes": [
                {
                    "name": "broken",
                    "command": "systemctl is-active nginx",
                    "expected_substrings": ["failed"],
                    "expected_exit_code": 3,
                }
            ]
        },
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
        execute_batches=[
            (
                CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),
            ),
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
    # Proxy LLM: plain text reply (no tool calls) — repair check passes on turn 2
    proxy_llm = _make_proxy_llm(["I checked and nginx shows failure."])
    orchestrator = BenchmarkRunOrchestrator(
        backend=backend,
        controller_factory=FakeControllerFactory([clone_controller]),
        subject_adapters={"fake_adapter": adapter},
        store=store,
        user_proxy_llm=proxy_llm,
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
    assert backend.configured_runtime_handles == ["clone-system-a"]


def test_benchmark_orchestrator_proxy_fsm_drives_turns_and_records_repair() -> None:
    """UserProxyFSM drives proxy turns; repair checks determine success."""
    store = _build_store()
    scenario = store.create_scenario(title="Nginx recovery", scenario_name_hint="nginx-recovery")
    revision = store.create_scenario_revision(
        scenario_id=scenario.id,
        target_image="ami-golden",
        summary="Recover nginx",
        what_it_tests={"items": ["systemd recovery"]},
        observable_problem_statement="The website is down and nginx will not start.",
        sabotage_plan={"steps": ["Break nginx with a bad unit override."]},
        verification_plan={
            "probes": [
                {
                    "name": "broken",
                    "command": "systemctl is-active nginx",
                    "expected_substrings": ["failed"],
                    "expected_exit_code": 3,
                }
            ]
        },
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
        execute_batches=[
            # Preflight verification: clone is still broken
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            # Turn 1 repair check: not fixed
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            # Turn 2 repair check: fixed
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),),
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
    # Proxy LLM returns plain text — repair check passes on turn 2
    proxy_llm = _make_proxy_llm(["I can see nginx is still not running."])
    orchestrator = BenchmarkRunOrchestrator(
        backend=FakeBackend(),
        controller_factory=FakeControllerFactory([clone_controller]),
        subject_adapters={"fake_adapter": adapter},
        store=store,
        user_proxy_llm=proxy_llm,
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
        verification_plan={
            "probes": [
                {
                    "name": "broken",
                    "command": "systemctl is-active nginx",
                    "expected_substrings": ["failed"],
                    "expected_exit_code": 3,
                }
            ]
        },
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
        user_proxy_llm=FakeUserProxyLLM(),
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
        verification_plan={
            "probes": [
                {
                    "name": "broken",
                    "command": "systemctl is-active nginx",
                    "expected_substrings": ["failed"],
                    "expected_exit_code": 3,
                }
            ]
        },
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
        controller_factory=FakeControllerFactory(
            [
                FakeController(
                    execute_batches=[
                        (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
                    ],
                )
            ]
        ),
        subject_adapters={"fake_adapter": adapter},
        store=store,
        user_proxy_llm=FakeUserProxyLLM(),
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
    # FSM calls clear_controller_runtime (no-op for SSM) before requesting broken image
    controller = FakeController()
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
    # FSM skips configure_controller_runtime but still calls clear_controller_runtime
    assert backend.configured_runtime_handles == []
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
    controller = FakeController()
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
    controller = FakeController()
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
    initial_user_message: str = "",
    turn_budget: int = 3,
    verification_checks: list[dict] | None = None,
    repair_check_command: str = "systemctl is-active nginx",
    repair_check_expected: list[str] | None = None,
    subject_max_turns: int | None = 3,
    repair_checks: list[dict] | None = None,
) -> tuple[EvalHarnessStore, object, object]:
    """Return (store, revision, setup) wired up and ready for a benchmark run."""
    if repair_check_expected is None:
        repair_check_expected = ["active"]
    if repair_checks is None:
        repair_checks = [
            {
                "name": "fixed",
                "command": repair_check_command,
                "expected_substrings": repair_check_expected,
            }
        ]
    if verification_checks is None:
        verification_checks = [
            {
                "name": "broken",
                "command": "systemctl is-active nginx",
                "expected_substrings": ["failed"],
                "expected_exit_code": 3,
            }
        ]
    store = _build_store()
    scenario = store.create_scenario(title="Test scenario", scenario_name_hint="test-scenario")
    revision = store.create_scenario_revision(
        scenario_id=scenario.id,
        target_image="ami-golden",
        summary="Test",
        what_it_tests={"items": ["recovery"]},
        observable_problem_statement=observable_problem_statement,
        initial_user_message=initial_user_message,
        sabotage_plan={"steps": ["Break it."]},
        verification_plan={"probes": verification_checks},
        judge_rubric={"items": ["diagnosis"]},
        planner_metadata={
            "repair_checks": repair_checks,
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
    adapter_config = {} if subject_max_turns is None else {"max_turns": subject_max_turns}
    store.upsert_subject(
        subject_name="system-a",
        adapter_type="fake_adapter",
        display_name="System A",
        adapter_config=adapter_config,
    )
    return store, revision, setup


def _run_benchmark(
    store: EvalHarnessStore,
    revision,
    setup,
    clone_controller: FakeController,
    session: FakeSubjectSession | None = None,
    proxy_llm: FakeUserProxyLLM | None = None,
    *,
    user_proxy_mode: str = "strict_relay",
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
    if proxy_llm is None:
        proxy_llm = FakeUserProxyLLM()
    adapter = FakeSubjectAdapter(session=session)
    orchestrator = BenchmarkRunOrchestrator(
        backend=FakeBackend(),
        controller_factory=FakeControllerFactory([clone_controller]),
        subject_adapters={"fake_adapter": adapter},
        store=store,
        user_proxy_llm=proxy_llm,
        user_proxy_mode=user_proxy_mode,
    )
    return orchestrator.run(
        scenario_revision_id=revision.id,
        verified_setup_run_id=setup.id,
        user_proxy_agent_id="user_proxy_agent",
        verification_agent_id="verification_executor",
    )


# ---------------------------------------------------------------------------
# Task 4: benchmark opener comes from stored initial_user_message
# ---------------------------------------------------------------------------


def test_benchmark_uses_initial_user_message_as_first_turn() -> None:
    opener = "My website is down. I tried restarting nginx and it still failed."
    store, revision, setup = _build_benchmark_store_and_revision(
        observable_problem_statement="website is down",
        initial_user_message=opener,
        turn_budget=1,
        subject_max_turns=1,
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
        ]
    )
    clone_controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
        ],
    )

    _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=_make_proxy_llm(["Still broken."]))

    assert session.submitted_messages[0] == opener


def test_benchmark_proxy_prompt_uses_stored_opener_not_more_revealing_problem_statement() -> None:
    opener = "My website is down."
    revealed_problem = "The nginx override file contains a missing semicolon."
    store, revision, setup = _build_benchmark_store_and_revision(
        observable_problem_statement=revealed_problem,
        initial_user_message=opener,
        turn_budget=1,
        subject_max_turns=1,
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
        ]
    )
    clone_controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
        ],
    )
    proxy_llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="I only know it is still broken.", tool_calls=(), finish_reason="stop", response_id="resp-1"),
        ]
    )

    _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=proxy_llm)

    start_call = next(call for call in proxy_llm.calls if call["phase"] == "start")
    assert opener in start_call["system_prompt"]
    assert revealed_problem not in start_call["system_prompt"]
    assert session.submitted_messages[0] == opener


def test_benchmark_preflight_verification_probes_allow_first_subject_turn() -> None:
    opener = "My website is down. I tried restarting nginx and it still failed."
    store, revision, setup = _build_benchmark_store_and_revision(
        observable_problem_statement="website is down",
        initial_user_message=opener,
        turn_budget=1,
        subject_max_turns=1,
        verification_checks=[
            {
                "name": "nginx-still-broken",
                "command": "systemctl is-active nginx",
                "expected_substrings": ["failed"],
                "expected_exit_code": 3,
            }
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
        ]
    )
    clone_controller = FakeController(
        execute_batches=[
            (
                CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),
            ),
            (
                CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),
            ),
        ],
    )

    result = _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=_make_proxy_llm(["Still broken."]))

    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)
    assert len(evaluation_runs) == 1
    assert session.submitted_messages[0] == opener


def test_benchmark_preflight_verification_failure_stops_before_subject_turn() -> None:
    store, revision, setup = _build_benchmark_store_and_revision(
        turn_budget=1,
        subject_max_turns=1,
        verification_checks=[
            {
                "name": "nginx-still-broken",
                "command": "systemctl is-active nginx",
                "expected_substrings": ["failed"],
                "expected_exit_code": 3,
            },
            {
                "name": "journal-shows-crash",
                "command": "journalctl -u nginx -n 1 --no-pager",
                "expected_substrings": ["failed"],
            },
        ],
    )

    class FailIfSubmittedSession(FakeSubjectSession):
        def submit_user_message(self, message: str) -> AdapterTurnResult:
            raise AssertionError(f"subject turn should not start, got: {message!r}")

    session = FailIfSubmittedSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message="Run systemctl status nginx.",
                run_id="run-1",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )
    clone_controller = FakeController(
        execute_batches=[
            (
                CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),
                CommandExecutionResult(command="journalctl -u nginx -n 1 --no-pager", stdout="nginx started", stderr="", exit_code=0),
            ),
        ],
    )

    result = _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=_make_proxy_llm(["Still broken."]))

    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)
    assert len(evaluation_runs) == 1
    eval_run = evaluation_runs[0]
    assert eval_run.status == "failed"
    assert eval_run.repair_success is False
    resolution = eval_run.resolution_result_json
    assert resolution.get("reason") == "scenario_fidelity_failed"
    assert resolution.get("failed_verification_probe_names") == ["nginx-still-broken", "journal-shows-crash"]
    assert len(resolution.get("verification_probe_results", [])) == 2
    assert all(result_entry["passed"] is False for result_entry in resolution["verification_probe_results"])

    events = store.list_evaluation_events(eval_run.id)
    command_events = [event for event in events if event.actor_role == "controller" and event.event_kind == "command_result"]
    assert len(command_events) == 2
    assert command_events[0].payload_json["command"] == "systemctl is-active nginx"
    assert command_events[1].payload_json["command"] == "journalctl -u nginx -n 1 --no-pager"
    assert all(event.actor_role != "user_proxy" for event in events)


def test_benchmark_falls_back_to_observable_problem_statement_when_initial_message_blank() -> None:
    problem = "The website is down and nginx will not start."
    store, revision, setup = _build_benchmark_store_and_revision(
        observable_problem_statement=problem,
        initial_user_message="   ",
        turn_budget=1,
        subject_max_turns=1,
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
        ]
    )
    clone_controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
        ],
    )

    _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=_make_proxy_llm(["Still broken."]))

    assert session.submitted_messages[0] == problem


def test_proxy_transcript_retains_stored_initial_user_message_on_later_turns() -> None:
    opener = "My website is down. I tried restarting nginx and it still failed."
    store, revision, setup = _build_benchmark_store_and_revision(
        observable_problem_statement="website is down",
        initial_user_message=opener,
        turn_budget=2,
        subject_max_turns=2,
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
                assistant_message="Check the error log too.",
                run_id="run-2",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )
    clone_controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
        ],
    )
    proxy_llm = _make_proxy_llm([
        "I already tried restarting it. What does systemctl status show?",
        "I still need the error logs.",
    ])

    _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=proxy_llm)

    start_calls = [call for call in proxy_llm.calls if call["phase"] == "start"]
    assert len(start_calls) == 2
    assert start_calls[1]["transcript"][0] == ("user", opener)


def test_benchmark_stalled_proxy_preserves_established_opening_message() -> None:
    opener = "My website is down. I tried restarting nginx and it still failed."
    store, revision, setup = _build_benchmark_store_and_revision(
        observable_problem_statement="website is down",
        initial_user_message=opener,
        turn_budget=2,
        subject_max_turns=2,
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
                assistant_message="Check the error log too.",
                run_id="run-2",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )
    clone_controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
        ],
    )

    class AlwaysStallProxyLLM(FakeUserProxyLLM):
        def start_turn(self, *, system_prompt, transcript, assistant_reply, tools):
            return UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-stall-start")

        def continue_turn(self, *, system_prompt, previous_response_id, tool_outputs, tools):
            return UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-stall-continue")

    _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=AlwaysStallProxyLLM())

    assert session.submitted_messages == [opener, opener]


def test_benchmark_preserves_stored_opener_with_initial_diagnostics_on_stall(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = "My website is down. I tried restarting nginx and it still failed."
    store, revision, setup = _build_benchmark_store_and_revision(
        observable_problem_statement="website is down",
        initial_user_message=opener,
        turn_budget=2,
        subject_max_turns=2,
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
                assistant_message="Check the error log too.",
                run_id="run-2",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )
    clone_controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (
                CommandExecutionResult(
                    command="systemctl status nginx --no-pager",
                    stdout="nginx.service - failed",
                    stderr="",
                    exit_code=3,
                ),
            ),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
        ],
    )

    class AlwaysStallProxyLLM(FakeUserProxyLLM):
        def start_turn(self, *, system_prompt, transcript, assistant_reply, tools):
            return UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-stall-start")

        def continue_turn(self, *, system_prompt, previous_response_id, tool_outputs, tools):
            return UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-stall-continue")

    custom_scenario = ScenarioSpec(
        scenario_name="nginx-recovery",
        title="Nginx recovery",
        summary="Recover a broken nginx service",
        what_it_tests=("systemd recovery", "log inspection"),
        target_image="ami-golden",
        observable_problem_statement="website is down",
        initial_user_message=opener,
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
        initial_diagnostic_commands=("systemctl status nginx --no-pager",),
        turn_budget=2,
    )

    import eval_harness.orchestration.benchmark as benchmark_module

    monkeypatch.setattr(benchmark_module, "scenario_spec_from_records", lambda scenario_row, revision_row: custom_scenario)

    _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=AlwaysStallProxyLLM())

    expected_message = (
        opener
        + "\n\nHere's what I'm seeing on the machine:"
        + "\n\n$ systemctl status nginx --no-pager"
        + "\nnginx.service - failed"
    )
    assert session.submitted_messages == [expected_message, expected_message]


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
    """Test C: Every proxy LLM call includes the system prompt with the problem statement."""
    problem = "The website is down and nginx will not start."
    store, revision, setup = _build_benchmark_store_and_revision(
        observable_problem_statement=problem,
        turn_budget=2,
        subject_max_turns=2,
    )

    received_system_prompts: list[str] = []

    class TrackingProxyLLM(FakeUserProxyLLM):
        def start_turn(self, *, system_prompt, transcript, assistant_reply, tools):
            received_system_prompts.append(system_prompt)
            return super().start_turn(
                system_prompt=system_prompt,
                transcript=transcript,
                assistant_reply=assistant_reply,
                tools=tools,
            )

        def continue_turn(self, *, system_prompt, previous_response_id, tool_outputs, tools):
            received_system_prompts.append(system_prompt)
            return super().continue_turn(
                system_prompt=system_prompt,
                previous_response_id=previous_response_id,
                tool_outputs=tool_outputs,
                tools=tools,
            )

    proxy_llm = TrackingProxyLLM(responses=[
        UserProxyLLMResponse(content="I checked the logs and nginx seems broken.", tool_calls=(), finish_reason="stop", response_id="resp-1"),
        UserProxyLLMResponse(content="The service still seems broken.", tool_calls=(), finish_reason="stop", response_id="resp-2"),
    ])

    clone_controller = FakeController(
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
    _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=proxy_llm)

    # Every proxy LLM call should include the system prompt containing the problem statement
    assert len(received_system_prompts) >= 1
    for sp in received_system_prompts:
        assert problem in sp
        assert "ask for clarification instead of guessing" in sp.lower()
        assert "do not add sudo" in sp.lower()


# ---------------------------------------------------------------------------
# Test D: HOST_RUN commands are executed and output appended to user message
# ---------------------------------------------------------------------------


def test_benchmark_tool_call_commands_executed_and_recorded() -> None:
    """Test D: proxy uses run_command tool → commands executed via controller, results recorded."""
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
            agent_id: str = "",
            session_key: str | None = None,
        ) -> tuple[CommandExecutionResult, ...]:
            result = super().execute_commands(commands, agent_id=agent_id, session_key=session_key)
            execute_batches_used.append((session_key or "", commands, result))
            return result

    clone_controller = TrackingController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            # Repair check after first subject turn: not fixed
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            # proxy run_command tool execution
            (CommandExecutionResult(command="systemctl status nginx", stdout="● nginx.service - NGINX\n   Loaded: loaded", stderr="", exit_code=0),),
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

    # Proxy LLM: turn 1 emits a tool call, turn 2 responds with the narrated result
    tc = UserProxyToolCall(id="call-1", name="run_command", arguments={"command": "systemctl status nginx"})
    proxy_llm = FakeUserProxyLLM(responses=[
        UserProxyLLMResponse(content="", tool_calls=(tc,), finish_reason="tool_calls", response_id="resp-1"),
        UserProxyLLMResponse(content="I see the service is failed. Please restart it.", tool_calls=(), finish_reason="stop", response_id="resp-2"),
    ])

    # Capture submitted user messages
    original_submit = session.submit_user_message
    def tracking_submit(message: str):
        submitted_user_messages.append(message)
        return original_submit(message)
    session.submit_user_message = tracking_submit  # type: ignore[method-assign]

    result = _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=proxy_llm)
    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)
    assert len(evaluation_runs) == 1

    # The evaluation store must have a user_proxy_exec/command_result event
    events = store.list_evaluation_events(evaluation_runs[0].id)
    exec_events = [e for e in events if e.actor_role == "user_proxy_exec" and e.event_kind == "command_result"]
    assert len(exec_events) >= 1

    # Check the command was dispatched via the session key (contains eval run id)
    proxy_exec_calls = [entry for entry in execute_batches_used if "proxy" in (entry[0] or "")]
    assert len(proxy_exec_calls) >= 1
    assert proxy_exec_calls[0][1] == ("systemctl status nginx",)


# ---------------------------------------------------------------------------
# Test E: /approve in proxy reply is blocked and re-prompted, skipped_turn event
# ---------------------------------------------------------------------------


def test_benchmark_proxy_stall_increments_counter_and_continues() -> None:
    """Test E: stalled FSM turn increments consecutive_stalled_turns; stall limit → failed."""
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=5, subject_max_turns=5)

    # Proxy LLM always returns empty content → FSM stalls every turn
    proxy_llm = FakeUserProxyLLM(responses=[])  # default fallback returns non-empty, so override

    class AlwaysStallProxyLLM(FakeUserProxyLLM):
        def start_turn(self, *, system_prompt, transcript, assistant_reply, tools):
            return UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-stall-start")

        def continue_turn(self, *, system_prompt, previous_response_id, tool_outputs, tools):
            return UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-stall-continue")

    proxy_llm = AlwaysStallProxyLLM()

    clone_controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
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
                assistant_message=f"reply-{i}",
                run_id=f"run-{i}",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            )
            for i in range(5)
        ]
    )

    result = _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=proxy_llm)
    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)
    assert len(evaluation_runs) == 1
    # After 3 stalled turns, the benchmark marks as failed with proxy_stalled
    eval_run = evaluation_runs[0]
    assert eval_run.status == "failed"
    assert eval_run.resolution_result_json.get("reason") == "proxy_stalled"


def test_benchmark_proxy_runs_only_explicit_tool_calls() -> None:
    """Proxy FSM only runs commands via the run_command tool; no spontaneous exec."""
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=1, subject_max_turns=1)

    executed_commands: list[tuple] = []

    class TrackingController(FakeController):
        def execute_commands(
            self,
            commands: tuple[str, ...],
            *,
            agent_id: str = "",
            session_key: str | None = None,
        ) -> tuple[CommandExecutionResult, ...]:
            result = super().execute_commands(commands, agent_id=agent_id, session_key=session_key)
            executed_commands.append((session_key or "", commands))
            return result

    clone_controller = TrackingController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active exampled", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active exampled", stdout="failed", stderr="", exit_code=3),),
        ],
    )
    session = FakeSubjectSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message="Run `cat /etc/example.conf` and show me the contents.",
                run_id="run-1",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )

    # Proxy LLM returns a plain text reply — no tool calls
    proxy_llm = _make_proxy_llm(["I see, the file contents were fine."])

    result = _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=proxy_llm)
    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)
    assert len(evaluation_runs) == 1

    # Only the repair check should have been called, not any spontaneous proxy exec
    proxy_exec_calls = [entry for entry in executed_commands if "proxy" in (entry[0] or "")]
    assert proxy_exec_calls == [], f"Unexpected proxy exec calls: {proxy_exec_calls}"


def test_benchmark_proxy_stall_detection_marks_failed() -> None:
    """Test G: proxy FSM stalls 3 times in a row → failed with reason=proxy_stalled."""
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=10, subject_max_turns=10)

    # Proxy LLM always returns empty content → FSM stalls every turn
    class AlwaysStallProxyLLM(FakeUserProxyLLM):
        def start_turn(self, *, system_prompt, transcript, assistant_reply, tools):
            return UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-stall-start")

        def continue_turn(self, *, system_prompt, previous_response_id, tool_outputs, tools):
            return UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-stall-continue")

    clone_controller = FakeController(
        execute_batches=[
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
    result = _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=AlwaysStallProxyLLM())
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

    # Proxy LLM returns plain text (no tool calls) → loop limited by scenario budget
    proxy_llm = _make_proxy_llm(["Plain text"] * 10)
    clone_controller = FakeController(
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

    _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=proxy_llm)

    assert len(submitted) <= 3, f"Expected at most 3 turns (scenario budget), got {len(submitted)}"


def test_benchmark_turn_budget_uses_subject_when_smaller() -> None:
    """Test H (part 2): scenario.turn_budget=10, subject max_turns=5 → at most 5 turns."""
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=10, subject_max_turns=5)

    proxy_llm = _make_proxy_llm(["Plain text"] * 15)
    clone_controller = FakeController(
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

    _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=proxy_llm)

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
        user_proxy_llm=FakeUserProxyLLM(),
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
        user_proxy_llm=FakeUserProxyLLM(),
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
        user_proxy_llm=FakeUserProxyLLM(),
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


def test_benchmark_stall_detection_marks_benchmark_completed_with_failures() -> None:
    """Proxy FSM stalls 3 turns → evaluation failed, benchmark completed_with_failures."""
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=5, subject_max_turns=5)

    class AlwaysStallProxyLLM(FakeUserProxyLLM):
        def start_turn(self, *, system_prompt, transcript, assistant_reply, tools):
            return UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-stall-start")

        def continue_turn(self, *, system_prompt, previous_response_id, tool_outputs, tools):
            return UserProxyLLMResponse(content="", tool_calls=(), finish_reason="stop", response_id="resp-stall-continue")

    clone_controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
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
                assistant_message=f"reply-{i}",
                run_id=f"run-{i}",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            )
            for i in range(5)
        ]
    )

    result = _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=AlwaysStallProxyLLM())
    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)
    benchmark_run = store.get_benchmark_run(result.benchmark_run_id)

    assert len(evaluation_runs) == 1
    assert evaluation_runs[0].status == "failed"
    assert evaluation_runs[0].resolution_result_json["reason"] == "proxy_stalled"
    assert benchmark_run is not None
    assert benchmark_run.status == "completed_with_failures"


def test_benchmark_polite_phrase_triggers_final_verification() -> None:
    """Soft closure phrase triggers one final verification before another subject turn."""
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=2, subject_max_turns=2)
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
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),),
        ],
    )
    proxy_llm = _make_proxy_llm(["looks good now, thanks"])

    result = _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=proxy_llm)
    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)

    assert len(evaluation_runs) == 1
    assert evaluation_runs[0].status == "completed"
    assert evaluation_runs[0].repair_success is True
    assert len(session.submitted_messages) == 1


def test_benchmark_completed_with_failures_when_subjects_do_not_repair() -> None:
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=3, subject_max_turns=3)
    proxy_llm = _make_proxy_llm([
        "I am not sure what to do.",
        "Maybe try something else?",
        "Still broken here.",
    ])
    clone_controller = FakeController(
        execute_batches=[
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

    result = _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=proxy_llm)
    benchmark_run = store.get_benchmark_run(result.benchmark_run_id)
    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)

    assert benchmark_run is not None
    assert benchmark_run.status == "completed_with_failures"
    assert benchmark_run.metadata_json["summary"]["repair_success_count"] == 0
    assert benchmark_run.metadata_json["summary"]["failed_evaluation_count"] == 1
    assert evaluation_runs[0].status == "failed"


def test_benchmark_immediate_post_action_verification_completes_without_extra_subject_turn() -> None:
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=3, subject_max_turns=3)

    clone_controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (
                CommandExecutionResult(
                    command="sudo systemctl restart nginx && sudo systemctl status nginx --no-pager",
                    stdout="nginx is active",
                    stderr="",
                    exit_code=0,
                ),
            ),
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),),
        ],
    )
    session = FakeSubjectSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message="Run `sudo systemctl restart nginx && sudo systemctl status nginx --no-pager`.",
                run_id="run-1",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )
    tool_call = UserProxyToolCall(
        id="call-1",
        name="run_command",
        arguments={"command": "sudo systemctl restart nginx && sudo systemctl status nginx --no-pager"},
    )
    proxy_llm = FakeUserProxyLLM(
        responses=[
            UserProxyLLMResponse(content="", tool_calls=(tool_call,), finish_reason="tool_calls", response_id="resp-1"),
            UserProxyLLMResponse(content="Okay, it actually started.", tool_calls=(), finish_reason="stop", response_id="resp-2"),
        ]
    )

    result = _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=proxy_llm)
    evaluation_runs = store.list_evaluation_runs(result.benchmark_run_id)

    assert len(evaluation_runs) == 1
    assert evaluation_runs[0].status == "completed"
    assert evaluation_runs[0].repair_success is True
    assert len(session.submitted_messages) == 1


def test_benchmark_failure_payload_records_partial_repair_snapshot() -> None:
    store, revision, setup = _build_benchmark_store_and_revision(
        turn_budget=1,
        subject_max_turns=1,
        repair_checks=[
            {
                "name": "service-active",
                "command": "systemctl is-active nginx",
                "expected_substrings": ["active"],
            },
            {
                "name": "homepage-ok",
                "command": "curl -sS --max-time 5 http://127.0.0.1/",
                "expected_substrings": ["nginx is working"],
                "expected_exit_code": 0,
            },
        ],
    )
    clone_controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
            (
                CommandExecutionResult(command="systemctl is-active nginx", stdout="active", stderr="", exit_code=0),
                CommandExecutionResult(command="curl -sS --max-time 5 http://127.0.0.1/", stdout="", stderr="", exit_code=0),
            ),
        ],
    )
    session = FakeSubjectSession(
        turn_results=[
            AdapterTurnResult(
                user_message="",
                assistant_message="Try checking the homepage.",
                run_id="run-1",
                status="completed",
                terminal_event_type="done",
                events=(RunEvent(seq=1, event_type=RunEventType.DONE, code="done", payload={}),),
            ),
        ]
    )

    result = _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=_make_proxy_llm(["still weird here"]))
    evaluation_run = store.list_evaluation_runs(result.benchmark_run_id)[0]

    assert evaluation_run.status == "failed"
    assert evaluation_run.resolution_result_json["passed_check_count"] == 1
    assert evaluation_run.resolution_result_json["failed_check_names"] == ["homepage-ok"]
    assert len(evaluation_run.resolution_result_json["last_repair_check_results"]) == 2


def test_benchmark_turn_budget_uses_scenario_when_subject_cap_omitted() -> None:
    store, revision, setup = _build_benchmark_store_and_revision(turn_budget=10, subject_max_turns=None)

    proxy_llm = _make_proxy_llm(["Plain text"] * 12)
    clone_controller = FakeController(
        execute_batches=[
            (CommandExecutionResult(command="systemctl is-active nginx", stdout="failed", stderr="", exit_code=3),),
        ] * 12,
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
            for i in range(12)
        ]
    )

    _run_benchmark(store, revision, setup, clone_controller, session, proxy_llm=proxy_llm)

    assert len(session.submitted_messages) == 10


def test_repair_checks_pass_returns_false_when_no_checks(ssh_scenario_spec: ScenarioSpec) -> None:
    """Test I: empty checks → False (using ssh_scenario_spec fixture, Test J)."""
    store = _build_store()
    orchestrator = BenchmarkRunOrchestrator(
        backend=FakeBackend(),
        controller_factory=FakeControllerFactory([]),
        subject_adapters={},
        store=store,
        user_proxy_llm=FakeUserProxyLLM(),
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
