"""Tests for ScenarioBuilderFSM (Phase 2)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from eval_harness.backends.base import SandboxBackend, SandboxHandle
from eval_harness.controllers.base import SandboxController, SandboxControllerFactory
from eval_harness.models import (
    CommandExecutionResult,
    PlannerReviewDecision,
    PlannerReviewOutcome,
    PlannerScenarioRequest,
    ScenarioSpec,
    VerificationCheck,
)
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
    FakePlanner,
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


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_approve_on_first_review() -> None:
    """DESIGN→LAUNCH→BUILD→VERIFY→REVIEW(approve)→CREATE_IMAGE→DONE."""
    store = _build_store()
    transitions: list[tuple[str, str]] = []

    def progress(fsm_name, scenario_name, details):
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
