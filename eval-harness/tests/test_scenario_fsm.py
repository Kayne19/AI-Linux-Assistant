"""Tests for ScenarioBuilderFSM (Phase 2)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from eval_harness.backends.base import SandboxBackend, SandboxHandle
from eval_harness.controllers.base import SandboxController, SandboxControllerFactory
from eval_harness.models import (
    CommandExecutionResult,
    InitialUserMessageDraft,
    InitialUserMessageReview,
    PlannerReviewDecision,
    PlannerReviewOutcome,
    PlannerScenarioRequest,
    ScenarioSpec,
    VerificationCheck,
)
from eval_harness.mapping import scenario_spec_from_records
from eval_harness.orchestration.scenario_fsm import (
    ScenarioBuilderFSM,
    ScenarioBuilderState,
    ScenarioSetupFailedError,
)
from eval_harness.persistence.database import build_engine, build_session_factory, create_all_tables
from eval_harness.persistence.store import EvalHarnessStore
from eval_harness.planners.base import ScenarioPlanner

# ---------------------------------------------------------------------------
# Import shared fakes from test_orchestrator where possible
# ---------------------------------------------------------------------------

from test_orchestrator import (
    FakeBackend,
    FakeController,
    FakeControllerFactory,
    FakePlanner as SharedFakePlanner,
)


@dataclass
class FakePlanner(SharedFakePlanner):
    initial_user_message_draft: str = "My website is down."
    initial_user_message_review: dict[str, str] = field(
        default_factory=lambda: {
            "outcome": "approve",
            "notes": "Looks realistic.",
            "final_message": "My website is down.",
        }
    )
    initial_user_message_review_sequence: list[dict[str, str]] | None = None
    initial_user_message_review_failure_at: int | None = None
    initial_user_message_exception: Exception | None = None
    initial_user_message_review_exception: Exception | None = None
    initial_user_message_call_order: list[str] = field(default_factory=list)
    initial_user_message_generation_calls: list[dict] = field(default_factory=list)
    initial_user_message_review_calls: list[dict] = field(default_factory=list)
    initial_user_message_review_call_count: int = field(default=0, init=False)

    def generate_initial_user_message(
        self,
        *,
        scenario: ScenarioSpec,
        hidden_context: dict[str, object],
    ) -> InitialUserMessageDraft:
        self.initial_user_message_call_order.append("generate")
        self.initial_user_message_generation_calls.append(
            {
                "scenario_name": scenario.scenario_name,
                "hidden_context": hidden_context,
            }
        )
        if self.initial_user_message_exception is not None:
            raise self.initial_user_message_exception
        return InitialUserMessageDraft(message=self.initial_user_message_draft)

    def review_initial_user_message(
        self,
        *,
        scenario: ScenarioSpec,
        draft_message: str,
    ) -> InitialUserMessageReview:
        self.initial_user_message_call_order.append("review")
        self.initial_user_message_review_call_count += 1
        self.initial_user_message_review_calls.append(
            {
                "scenario_name": scenario.scenario_name,
                "draft_message": draft_message,
            }
        )
        if self.initial_user_message_review_failure_at == self.initial_user_message_review_call_count:
            raise RuntimeError("review failed")
        if self.initial_user_message_review_exception is not None:
            raise self.initial_user_message_review_exception
        review_payload = self.initial_user_message_review
        if self.initial_user_message_review_sequence is not None:
            review_payload = self.initial_user_message_review_sequence[
                min(self.initial_user_message_review_call_count - 1, len(self.initial_user_message_review_sequence) - 1)
            ]
        return InitialUserMessageReview(
            outcome=review_payload["outcome"],
            notes=review_payload["notes"],
            final_message=review_payload["final_message"],
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_store() -> EvalHarnessStore:
    engine = build_engine("sqlite+pysqlite:///:memory:")
    create_all_tables(engine)
    return EvalHarnessStore(build_session_factory(engine))


def _request() -> PlannerScenarioRequest:
    return PlannerScenarioRequest(planning_brief="break nginx", target_image="ami-golden")


def _build_fsm(
    *,
    store: EvalHarnessStore,
    backend: FakeBackend | None = None,
    controller: FakeController | None = None,
    planner: FakePlanner | None = None,
    max_corrections: int = 2,
    scrap_budget: int = 1,
    progress=None,
) -> ScenarioBuilderFSM:
    if backend is None:
        backend = FakeBackend()
    if controller is None:
        controller = FakeController()
    if planner is None:
        planner = FakePlanner()
    return ScenarioBuilderFSM(
        backend=backend,
        controller_factory=FakeControllerFactory([controller]),
        planner=planner,
        store=store,
        request=_request(),
        group_id="group-test",
        max_corrections=max_corrections,
        scrap_budget=scrap_budget,
        progress=progress,
    )


def _run_setup(*, planner: FakePlanner | None = None) -> tuple[object, EvalHarnessStore]:
    store = _build_store()
    fsm = _build_fsm(store=store, planner=planner)
    return fsm.run(), store


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_scenario_fsm_persists_generated_initial_user_message() -> None:
    planner = FakePlanner(
        initial_user_message_draft="My website is down. I tried restarting nginx and it still failed.",
        initial_user_message_review={
            "outcome": "approve",
            "notes": "Looks realistic.",
            "final_message": "My website is down. I tried restarting nginx and it still failed.",
        },
    )

    result, store = _run_setup(planner=planner)
    revision = store.get_scenario_revision(result.scenario_revision_id)

    assert revision is not None
    assert revision.initial_user_message.startswith("My website is down.")
    assert revision.planner_metadata_json["initial_user_message_generation"] == {
        "draft": "My website is down. I tried restarting nginx and it still failed.",
        "review_outcome": "approve",
        "review_notes": "Looks realistic.",
        "final_message": "My website is down. I tried restarting nginx and it still failed.",
        "used_fallback": False,
    }


def test_scenario_fsm_persists_rewritten_initial_user_message() -> None:
    planner = FakePlanner(
        initial_user_message_draft="The nginx config is broken and the missing semicolon is in server_name.",
        initial_user_message_review={
            "outcome": "rewrite",
            "notes": "The draft reveals hidden root cause details.",
            "final_message": "My website is down. I tried restarting nginx and it still failed.",
        },
    )

    result, store = _run_setup(planner=planner)
    revision = store.get_scenario_revision(result.scenario_revision_id)

    assert revision is not None
    assert revision.initial_user_message == "My website is down. I tried restarting nginx and it still failed."
    assert revision.planner_metadata_json["initial_user_message_generation"] == {
        "draft": "The nginx config is broken and the missing semicolon is in server_name.",
        "review_outcome": "rewrite",
        "review_notes": "The draft reveals hidden root cause details.",
        "final_message": "My website is down. I tried restarting nginx and it still failed.",
        "used_fallback": False,
    }


def test_scenario_fsm_syncs_updated_observable_problem_statement_into_stored_opener() -> None:
    planner = FakePlanner(
        initial_user_message_draft="My website is down. I tried restarting nginx and it still failed.",
        initial_user_message_review_sequence=[
            {
                "outcome": "approve",
                "notes": "Looks realistic.",
                "final_message": "My website is down. I tried restarting nginx and it still failed.",
            },
            {
                "outcome": "approve",
                "notes": "Looks realistic.",
                "final_message": "My website is down. I tried restarting nginx and it still failed.",
            },
        ],
    )
    planner.review_decisions = [
        PlannerReviewDecision(
            outcome=PlannerReviewOutcome.APPROVE,
            summary="Planner updates the visible problem statement.",
            updated_observable_problem_statement="My website is down. I tried restarting nginx and it still failed.",
        )
    ]

    result, store = _run_setup(planner=planner)
    scenario_row = store.get_scenario(result.scenario_id)
    revision = store.get_scenario_revision(result.scenario_revision_id)
    assert scenario_row is not None
    assert revision is not None

    assert revision.observable_problem_statement == "My website is down. I tried restarting nginx and it still failed."
    assert revision.initial_user_message == "My website is down. I tried restarting nginx and it still failed."

    loaded_scenario = scenario_spec_from_records(scenario_row, revision)
    assert loaded_scenario.initial_user_message == "My website is down. I tried restarting nginx and it still failed."
    assert planner.initial_user_message_review_call_count == 2
    assert planner.initial_user_message_review_calls[1]["draft_message"] == "My website is down. I tried restarting nginx and it still failed."


def test_scenario_fsm_rewrites_updated_observable_problem_statement_before_storing_opener() -> None:
    planner = FakePlanner(
        initial_user_message_draft="My website is down. I tried restarting nginx and it still failed.",
        initial_user_message_review_sequence=[
            {
                "outcome": "approve",
                "notes": "Looks realistic.",
                "final_message": "My website is down. I tried restarting nginx and it still failed.",
            },
            {
                "outcome": "rewrite",
                "notes": "Remove setup detail from the opener.",
                "final_message": "My website is having issues after a recent change.",
            },
        ],
    )
    planner.review_decisions = [
        PlannerReviewDecision(
            outcome=PlannerReviewOutcome.APPROVE,
            summary="Planner updates the visible problem statement with revealing detail.",
            updated_observable_problem_statement="The nginx config is broken because server_name is missing a semicolon.",
        )
    ]

    result, store = _run_setup(planner=planner)
    revision = store.get_scenario_revision(result.scenario_revision_id)

    assert revision is not None
    assert revision.observable_problem_statement == "The nginx config is broken because server_name is missing a semicolon."
    assert revision.initial_user_message == "My website is having issues after a recent change."
    assert planner.initial_user_message_review_call_count == 2
    assert planner.initial_user_message_review_calls[1]["draft_message"] == "The nginx config is broken because server_name is missing a semicolon."


def test_scenario_fsm_retains_previous_opener_when_updated_problem_statement_review_fails() -> None:
    planner = FakePlanner(
        initial_user_message_draft="My website is down. I tried restarting nginx and it still failed.",
        initial_user_message_review_sequence=[
            {
                "outcome": "approve",
                "notes": "Looks realistic.",
                "final_message": "My website is down. I tried restarting nginx and it still failed.",
            }
        ],
        initial_user_message_review_failure_at=2,
    )
    planner.review_decisions = [
        PlannerReviewDecision(
            outcome=PlannerReviewOutcome.APPROVE,
            summary="Planner updates the visible problem statement with revealing detail.",
            updated_observable_problem_statement="The nginx config is broken because server_name is missing a semicolon.",
        )
    ]

    result, store = _run_setup(planner=planner)
    revision = store.get_scenario_revision(result.scenario_revision_id)

    assert revision is not None
    assert revision.observable_problem_statement == "The nginx config is broken because server_name is missing a semicolon."
    assert revision.initial_user_message == "My website is down. I tried restarting nginx and it still failed."
    assert planner.initial_user_message_review_call_count == 2
    assert planner.initial_user_message_review_calls[1]["draft_message"] == "The nginx config is broken because server_name is missing a semicolon."


def test_scenario_fsm_calls_generate_then_review_once_on_happy_path() -> None:
    planner = FakePlanner(
        initial_user_message_draft="My website is down. I tried restarting nginx and it still failed.",
        initial_user_message_review={
            "outcome": "approve",
            "notes": "Looks realistic.",
            "final_message": "My website is down. I tried restarting nginx and it still failed.",
        },
    )

    _run_setup(planner=planner)

    assert planner.initial_user_message_call_order == ["generate", "review"]
    assert len(planner.initial_user_message_generation_calls) == 1
    assert len(planner.initial_user_message_review_calls) == 1
    assert planner.initial_user_message_review_calls[0]["draft_message"] == planner.initial_user_message_draft


def test_scenario_fsm_falls_back_to_observable_problem_statement_when_generation_fails() -> None:
    planner = FakePlanner(
        initial_user_message_exception=RuntimeError("draft failed"),
    )

    result, store = _run_setup(planner=planner)
    revision = store.get_scenario_revision(result.scenario_revision_id)

    assert revision is not None
    assert revision.initial_user_message == planner.generated_scenario.observable_problem_statement
    assert revision.planner_metadata_json["initial_user_message_generation"] == {
        "draft": "",
        "review_outcome": "fallback",
        "review_notes": "RuntimeError: draft failed",
        "final_message": planner.generated_scenario.observable_problem_statement,
        "used_fallback": True,
    }
    assert planner.initial_user_message_call_order == ["generate"]
    assert len(planner.initial_user_message_generation_calls) == 1
    assert planner.initial_user_message_review_calls == []


def test_scenario_fsm_falls_back_and_records_draft_when_review_fails() -> None:
    planner = FakePlanner(
        initial_user_message_draft="My website is down. I tried restarting nginx and it still failed.",
        initial_user_message_review_exception=RuntimeError("review failed"),
    )

    result, store = _run_setup(planner=planner)
    revision = store.get_scenario_revision(result.scenario_revision_id)

    assert revision is not None
    assert revision.initial_user_message == planner.generated_scenario.observable_problem_statement
    assert revision.planner_metadata_json["initial_user_message_generation"] == {
        "draft": "My website is down. I tried restarting nginx and it still failed.",
        "review_outcome": "fallback",
        "review_notes": "RuntimeError: review failed",
        "final_message": planner.generated_scenario.observable_problem_statement,
        "used_fallback": True,
    }


def test_happy_path_approve_on_first_review() -> None:
    """DESIGN→LAUNCH→BUILD→VERIFY→REVIEW(approve)→CREATE_IMAGE→DONE."""
    store = _build_store()
    transitions: list[tuple[str, str]] = []

    def progress(fsm_name, scenario_name, details):
        if "from" not in details:
            return
        transitions.append((details["from"], details["to"]))

    fsm = _build_fsm(store=store, progress=progress)
    result = fsm.run()

    assert result.broken_image_id == "broken-group-test"
    assert result.scenario_name == "nginx-recovery"

    # Verify all expected state transitions fired
    transition_map = {(f, t) for f, t in transitions}
    assert ("DESIGN", "LAUNCH_STAGING") in transition_map
    assert ("LAUNCH_STAGING", "BUILD") in transition_map
    assert ("BUILD", "VERIFY") in transition_map
    assert ("VERIFY", "REVIEW") in transition_map
    assert ("REVIEW", "CREATE_IMAGE") in transition_map
    assert ("CREATE_IMAGE", "DONE") in transition_map

    # Verify state_transition events were persisted to store
    from eval_harness.persistence.postgres_models import ScenarioSetupEventRecord
    from sqlalchemy import select

    with store._session_factory() as session:
        events = session.scalars(
            select(ScenarioSetupEventRecord).where(
                ScenarioSetupEventRecord.event_kind == "state_transition"
            )
        ).all()
    assert len(events) >= 6

    # Final setup run status should be verified
    setup_run = store.get_setup_run(result.setup_run_id)
    assert setup_run is not None
    assert setup_run.status == "verified"
    assert setup_run.broken_image_id == "broken-group-test"


# ---------------------------------------------------------------------------
# Fix path (CORRECT → FIX_PLAN → FIX_EXECUTE → VERIFY → APPROVE)
# ---------------------------------------------------------------------------


def test_correct_then_fix_then_approve() -> None:
    """First review returns CORRECT; FIX_PLAN generates commands; second review APPROVEs."""
    store = _build_store()
    planner = FakePlanner(
        review_decisions=[
            PlannerReviewDecision(
                outcome=PlannerReviewOutcome.CORRECT,
                summary="still running",
                correction_instructions=("hard-stop the unit",),
            ),
            # second review → approve (default)
        ],
        rectification_commands=("systemctl stop nginx", "rm /etc/nginx/nginx.conf"),
    )
    fsm = _build_fsm(store=store, planner=planner, max_corrections=2)
    result = fsm.run()

    assert result.broken_image_id == "broken-group-test"

    # plan_rectification was called once with the right args
    assert len(planner.rectification_calls) == 1
    call = planner.rectification_calls[0]
    assert call["round_index"] == 0
    assert call["correction_instructions"] == ("hard-stop the unit",)

    # review_calls: (round_index, correction_count)
    # first review: (0, 0), second review after FIX_EXECUTE: (1, 1)
    assert planner.review_calls == [(0, 0), (1, 1)]


def test_scenario_fsm_persists_updated_verification_probes_on_approve() -> None:
    store = _build_store()
    controller = FakeController(
        execute_batches=[
            (
                CommandExecutionResult(
                    command="nginx -t",
                    stdout="",
                    stderr='2026/04/18 09:18:51 [emerg] unknown directive "invalid_directive"',
                    exit_code=1,
                ),
            ),
        ]
    )
    planner = FakePlanner(
        review_decisions=[
            PlannerReviewDecision(
                outcome=PlannerReviewOutcome.APPROVE,
                summary="Broken state is correct; normalize the matcher.",
                updated_verification_probes=(
                    VerificationCheck(
                        name="nginx-config-broken",
                        command="nginx -t",
                        intent="Verify nginx config test fails.",
                        expected_regexes=("(?i)(unknown|invalid) directive",),
                        expected_exit_code=1,
                    ),
                ),
                metadata={"probe_normalization": "rewrote brittle matcher"},
            )
        ],
    )

    fsm = _build_fsm(store=store, controller=controller, planner=planner)
    result = fsm.run()
    revision = store.get_scenario_revision(result.scenario_revision_id)

    assert revision is not None
    assert revision.verification_plan_json == {
        "probes": [
            {
                "name": "nginx-config-broken",
                "command": "nginx -t",
                "intent": "Verify nginx config test fails.",
                "expected_substrings": [],
                "expected_regexes": ["(?i)(unknown|invalid) directive"],
                "unexpected_substrings": [],
                "unexpected_regexes": [],
                "expected_exact_match": None,
                "expected_exit_code": 1,
                "match_mode": "all",
                "timeout_seconds": 60,
            }
        ]
    }
    assert planner.review_snapshots[0]["verification_passed"] is False
    assert planner.review_snapshots[0]["failed_verification_probe_names"] == ["nginx-broken"]


def test_scenario_fsm_does_not_persist_updated_verification_probes_on_correct() -> None:
    store = _build_store()
    planner = FakePlanner(
        review_decisions=[
            PlannerReviewDecision(
                outcome=PlannerReviewOutcome.CORRECT,
                summary="Broken state is still wrong; do not rewrite probes yet.",
                correction_instructions=("re-run sabotage",),
                updated_verification_probes=(
                    VerificationCheck(
                        name="should-not-persist",
                        command="nginx -t",
                        intent="This should be ignored until approval.",
                        expected_exit_code=1,
                    ),
                ),
            ),
        ],
        rectification_commands=("echo fix",),
    )
    controller = FakeController(
        execute_batches=[
            (),  # BUILD
            (),  # VERIFY
            (),  # FIX_EXECUTE
            (),  # VERIFY after rectification
        ]
    )

    fsm = _build_fsm(store=store, controller=controller, planner=planner, max_corrections=2)
    result = fsm.run()
    revision = store.get_scenario_revision(result.scenario_revision_id)

    assert revision is not None
    assert revision.verification_plan_json["probes"][0]["name"] == "nginx-broken"


def test_scenario_fsm_rejects_invalid_updated_verification_probes() -> None:
    store = _build_store()
    planner = FakePlanner(
        review_decisions=[
            PlannerReviewDecision(
                outcome=PlannerReviewOutcome.APPROVE,
                summary="Approve with malformed probe rewrite.",
                updated_verification_probes=(
                    VerificationCheck(
                        name="broken-probe",
                        command="nginx -t",
                        intent="Missing machine-checkable expectation should fail validation.",
                    ),
                ),
            ),
        ],
    )
    controller = FakeController(
        execute_batches=[
            (),  # BUILD
            (),  # VERIFY
        ]
    )

    fsm = _build_fsm(store=store, controller=controller, planner=planner, max_corrections=2)

    with pytest.raises(ScenarioSetupFailedError) as exc_info:
        fsm.run()

    assert "invalid_updated_verification_probes" in str(exc_info.value)


def test_invalid_rectification_commands_fail_before_execution() -> None:
    store = _build_store()

    @dataclass
    class TrackingController(FakeController):
        execute_calls: list[tuple[str, ...]] = field(default_factory=list)

        def execute_commands(self, commands, *, agent_id="", session_key=None):
            self.execute_calls.append(commands)
            return super().execute_commands(commands, agent_id=agent_id, session_key=session_key)

    controller = TrackingController(
        execute_batches=[
            (),  # BUILD
            (),  # VERIFY
        ]
    )
    planner = FakePlanner(
        review_decisions=[
            PlannerReviewDecision(
                outcome=PlannerReviewOutcome.CORRECT,
                summary="Need a proper rectification command.",
                correction_instructions=("restore the nginx override",),
            ),
        ],
        rectification_commands=("Delete the nginx override",),
    )
    fsm = _build_fsm(store=store, controller=controller, planner=planner, max_corrections=2)

    with pytest.raises(ScenarioSetupFailedError) as exc_info:
        fsm.run()

    message = str(exc_info.value)
    assert "invalid_rectification_commands" in message
    assert len(controller.execute_calls) == 2
    assert all("Delete the nginx override" not in call for batch in controller.execute_calls for call in batch)

    from sqlalchemy import select
    from eval_harness.persistence.postgres_models import ScenarioSetupRunRecord

    with store._session_factory() as session:
        run = session.scalars(select(ScenarioSetupRunRecord)).one()
    assert run.status == "failed_infra"
    assert run.failure_reason == "invalid_rectification_commands"
    assert run.backend_metadata_json["invalid_rectification_commands"] == ["Delete the nginx override"]


# ---------------------------------------------------------------------------
# Max corrections exhausted → SCRAP → fresh DESIGN → APPROVE → DONE
# ---------------------------------------------------------------------------


def test_max_corrections_exhausted_scrap_then_approve() -> None:
    """Two CORRECTs exhaust max_corrections=1 → SCRAP → new DESIGN → APPROVE → DONE."""
    store = _build_store()
    first_backend = FakeBackend()

    # Round 0: BUILD + VERIFY + FIX_EXECUTE + VERIFY (two reviews)
    # After scrap: new DESIGN/LAUNCH/BUILD/VERIFY/REVIEW (approve)
    controller_round0 = FakeController(
        execute_batches=[
            (),   # BUILD round 0
            (),   # VERIFY round 0
            (),   # FIX_EXECUTE
            (),   # VERIFY round 1
        ]
    )
    controller_round1 = FakeController(
        execute_batches=[
            (),   # BUILD (scrap restart)
            (),   # VERIFY (scrap restart)
        ]
    )

    planner = FakePlanner(
        review_decisions=[
            PlannerReviewDecision(
                outcome=PlannerReviewOutcome.CORRECT,
                summary="not broken",
                correction_instructions=("redo sabotage",),
            ),
            PlannerReviewDecision(
                outcome=PlannerReviewOutcome.CORRECT,
                summary="still not broken",
                correction_instructions=("redo again",),
            ),
            # third review → approve (default FakePlanner)
        ],
        rectification_commands=("echo fix",),
    )

    fsm = ScenarioBuilderFSM(
        backend=first_backend,
        controller_factory=FakeControllerFactory([controller_round0, controller_round1]),
        planner=planner,
        store=store,
        request=_request(),
        group_id="group-test",
        max_corrections=1,
        scrap_budget=1,
    )
    result = fsm.run()

    assert result.broken_image_id == "broken-group-test"

    # destroy_handle was called for the first staging instance (during scrap)
    assert len(first_backend.destroyed_handles) >= 1
    # Two launch calls (first staging + scrap restart)
    assert len(first_backend.requested_target_images) == 2


# ---------------------------------------------------------------------------
# Scrap budget exhausted → FAILED
# ---------------------------------------------------------------------------


def test_scrap_budget_exhausted_fails() -> None:
    """scrap_budget=0 → SCRAP state immediately fails."""
    store = _build_store()
    controller = FakeController(
        execute_batches=[
            (),  # BUILD
            (),  # VERIFY
            (),  # FIX_EXECUTE
            (),  # VERIFY round 1
        ]
    )
    planner = FakePlanner(
        review_decisions=[
            PlannerReviewDecision(
                outcome=PlannerReviewOutcome.CORRECT,
                summary="broken",
                correction_instructions=("try again",),
            ),
            PlannerReviewDecision(
                outcome=PlannerReviewOutcome.CORRECT,
                summary="still broken",
                correction_instructions=("and again",),
            ),
        ],
        rectification_commands=("echo fix",),
    )
    fsm = _build_fsm(store=store, controller=controller, planner=planner, max_corrections=1, scrap_budget=0)

    with pytest.raises(ScenarioSetupFailedError) as exc_info:
        fsm.run()

    assert "scrap" in str(exc_info.value).lower() or "exhausted" in str(exc_info.value).lower()

    # Setup run should be in a terminal failed status
    from sqlalchemy import select
    from eval_harness.persistence.postgres_models import ScenarioSetupRunRecord

    with store._session_factory() as session:
        run = session.scalars(select(ScenarioSetupRunRecord)).one()
    assert "failed" in run.status


# ---------------------------------------------------------------------------
# Empty rectification → FAILED
# ---------------------------------------------------------------------------


def test_empty_rectification_fails() -> None:
    """FIX_PLAN returning empty commands → FAILED with planner_returned_empty_rectification."""
    store = _build_store()
    planner = FakePlanner(
        review_decisions=[
            PlannerReviewDecision(
                outcome=PlannerReviewOutcome.CORRECT,
                summary="nope",
                correction_instructions=("do something",),
            ),
        ],
        rectification_commands=(),  # empty!
    )
    controller = FakeController(
        execute_batches=[
            (),  # BUILD
            (),  # VERIFY
        ]
    )
    fsm = _build_fsm(store=store, controller=controller, planner=planner, max_corrections=2)

    with pytest.raises(ScenarioSetupFailedError) as exc_info:
        fsm.run()

    assert "empty_rectification" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Progress callback receives correct state sequence
# ---------------------------------------------------------------------------


def test_progress_callback_receives_state_transitions() -> None:
    """Progress callback is called at every state transition with from/to info."""
    store = _build_store()
    calls: list[tuple[str, str, str, dict]] = []

    def progress(fsm_name, scenario_name, details):
        if "from" not in details:
            return
        calls.append((fsm_name, scenario_name, details["from"], details))

    fsm = _build_fsm(store=store, progress=progress)
    fsm.run()

    fsm_names = {c[0] for c in calls}
    assert "scenario-builder" in fsm_names

    from_states = [c[2] for c in calls]
    assert "DESIGN" in from_states
    assert "LAUNCH_STAGING" in from_states
    assert "BUILD" in from_states
    assert "VERIFY" in from_states
    assert "REVIEW" in from_states
    assert "CREATE_IMAGE" in from_states

    # Each call includes round_index and correction_count
    for _, _, _, details in calls:
        assert "round_index" in details
        assert "correction_count" in details
        assert "scrap_count" in details


def test_progress_callback_reports_planner_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _build_store()
    calls: list[tuple[str, str, dict]] = []

    class FakeEvent:
        def __init__(self) -> None:
            self._wait_results = iter([False, True])

        def wait(self, timeout: float | None = None) -> bool:
            del timeout
            return next(self._wait_results)

        def set(self) -> None:
            return None

    class FakeThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            del name, daemon
            self._target = target

        def start(self) -> None:
            self._target()

        def join(self, timeout: float | None = None) -> None:
            del timeout
            return None

    ticks = iter([10.0, 40.0, 42.5, 50.0, 80.0, 80.75])
    monkeypatch.setattr("eval_harness.orchestration.scenario_fsm.monotonic", lambda: next(ticks))
    monkeypatch.setattr("eval_harness.orchestration.scenario_fsm.Event", FakeEvent)
    monkeypatch.setattr("eval_harness.orchestration.scenario_fsm.Thread", FakeThread)

    def progress(fsm_name, scenario_name, details):
        calls.append((fsm_name, scenario_name, details))

    fsm = _build_fsm(store=store, progress=progress)
    fsm.run()

    planner_events = [details for fsm_name, _, details in calls if fsm_name == "scenario-builder" and details.get("event")]

    assert planner_events == [
        {"event": "planner_thinking_start", "phase": "generate_scenario"},
        {"event": "planner_thinking_heartbeat", "phase": "generate_scenario", "elapsed_seconds": 30.0},
        {"event": "planner_thinking_done", "phase": "generate_scenario", "elapsed_seconds": 32.5},
        {"event": "planner_thinking_start", "phase": "review_sabotage"},
        {"event": "planner_thinking_heartbeat", "phase": "review_sabotage", "elapsed_seconds": 30.0},
        {"event": "planner_thinking_done", "phase": "review_sabotage", "elapsed_seconds": 30.75},
    ]


# ---------------------------------------------------------------------------
# Broken image timeout handled
# ---------------------------------------------------------------------------


def test_broken_image_timeout_raises_setup_failed_error() -> None:
    store = _build_store()
    backend = FakeBackend(
        broken_image_wait_exception=TimeoutError("ami wait timed out"),
    )
    fsm = _build_fsm(store=store, backend=backend)

    with pytest.raises(ScenarioSetupFailedError, match="Timed out waiting"):
        fsm.run()

    from sqlalchemy import select
    from eval_harness.persistence.postgres_models import ScenarioSetupRunRecord

    with store._session_factory() as session:
        run = session.scalars(select(ScenarioSetupRunRecord)).one()
    assert run.status == "failed_infra"
    assert run.failure_reason == "broken_image_creation_timeout"


# ---------------------------------------------------------------------------
# Infrastructure exception propagates and run ends in failed_infra
# ---------------------------------------------------------------------------


def test_infra_exception_propagates_and_marks_failed_infra() -> None:
    store = _build_store()
    backend = FakeBackend(diagnostics={"error": "instance gone"})

    class RaisingController(FakeController):
        def execute_commands(self, commands, *, agent_id="", session_key=None):
            raise ConnectionError("SSM not available")

    controller = RaisingController()
    fsm = ScenarioBuilderFSM(
        backend=backend,
        controller_factory=FakeControllerFactory([controller]),
        planner=FakePlanner(),
        store=store,
        request=_request(),
        group_id="group-test",
        max_corrections=2,
        scrap_budget=1,
    )

    with pytest.raises(ConnectionError, match="SSM not available"):
        fsm.run()

    from sqlalchemy import select
    from eval_harness.persistence.postgres_models import ScenarioSetupRunRecord

    with store._session_factory() as session:
        run = session.scalars(select(ScenarioSetupRunRecord)).one()
    assert run.status == "failed_infra"
    assert "staging-nginx-recovery" in backend.destroyed_handles
