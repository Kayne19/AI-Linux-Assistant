"""Scenario read/write and control routes."""

from __future__ import annotations

import fastapi
from fastapi import APIRouter, BackgroundTasks

from ..deps import StoreDep
from ..jobs import dispatcher
from ..schemas import (
    BenchmarkRequest,
    RunAllRequest,
    ScenarioCreateRequest,
    ScenarioCreateResponse,
    ScenarioDetail,
    ScenarioListItem,
    ScenarioRevisionCreateRequest,
    ScenarioRevisionCreateResponse,
    ScenarioRevisionItem,
    VerifyRequest,
)

router = APIRouter(tags=["scenarios"])


@router.get("/scenarios")
def list_scenarios(store: StoreDep) -> list[ScenarioListItem]:
    from eval_harness.persistence.postgres_models import ScenarioRecord
    from sqlalchemy import select

    with store._session_factory() as session:
        rows = session.scalars(
            select(ScenarioRecord).order_by(ScenarioRecord.created_at.desc())
        )
        return [
            ScenarioListItem(
                id=r.id,
                scenario_name=r.scenario_name,
                title=r.title,
                lifecycle_status=r.lifecycle_status,
                verification_status=r.verification_status,
                benchmark_run_count=r.benchmark_run_count,
                created_at=r.created_at,
                current_verified_revision_id=r.current_verified_revision_id,
                last_verified_at=r.last_verified_at,
            )
            for r in rows
        ]


@router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, store: StoreDep) -> ScenarioDetail:
    from eval_harness.persistence.postgres_models import ScenarioRevisionRecord
    from sqlalchemy import select

    row = store.get_scenario(scenario_id)
    if row is None:
        raise fastapi.HTTPException(status_code=404, detail="Scenario not found")

    with store._session_factory() as session:
        rev_rows = session.scalars(
            select(ScenarioRevisionRecord)
            .where(ScenarioRevisionRecord.scenario_id == scenario_id)
            .order_by(ScenarioRevisionRecord.revision_number.desc())
        )
        revisions = [
            ScenarioRevisionItem(
                id=r.id,
                revision_number=r.revision_number,
                target_image=r.target_image,
                summary=r.summary,
                created_at=r.created_at,
            )
            for r in rev_rows
        ]

    return ScenarioDetail(
        id=row.id,
        scenario_name=row.scenario_name,
        title=row.title,
        lifecycle_status=row.lifecycle_status,
        verification_status=row.verification_status,
        benchmark_run_count=row.benchmark_run_count,
        created_at=row.created_at,
        last_verified_at=row.last_verified_at,
        current_verified_revision_id=row.current_verified_revision_id,
        revisions=revisions,
    )


@router.post("/scenarios")
def create_scenario(
    body: ScenarioCreateRequest, store: StoreDep
) -> ScenarioCreateResponse:
    """Create a new scenario."""
    row = store.create_scenario(
        title=body.title,
        scenario_name_hint=body.scenario_name_hint,
    )
    return ScenarioCreateResponse(
        id=row.id,
        scenario_name=row.scenario_name,
        title=row.title,
        lifecycle_status=row.lifecycle_status,
        verification_status=row.verification_status,
        created_at=row.created_at,
    )


@router.post("/scenarios/{scenario_id}/revisions")
def create_scenario_revision(
    scenario_id: str,
    body: ScenarioRevisionCreateRequest,
    store: StoreDep,
) -> ScenarioRevisionCreateResponse:
    """Create a new revision for an existing scenario."""
    # Verify the scenario exists
    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise fastapi.HTTPException(status_code=404, detail="Scenario not found")

    row = store.create_scenario_revision(
        scenario_id=scenario_id,
        target_image=body.target_image,
        summary=body.summary,
        what_it_tests=body.what_it_tests,
        observable_problem_statement=body.observable_problem_statement,
        initial_user_message=body.initial_user_message,
        sabotage_plan=body.sabotage_plan,
        verification_plan=body.verification_plan,
        judge_rubric=body.judge_rubric,
        planner_metadata=body.planner_metadata,
    )
    return ScenarioRevisionCreateResponse(
        id=row.id,
        revision_number=row.revision_number,
        target_image=row.target_image,
        summary=row.summary,
        created_at=row.created_at,
    )


# ── Control endpoints (M3) ────────────────────────────────────────────


class ControlResponse(dict):
    pass


def _latest_revision_id_for_scenario(scenario_id: str, store: StoreDep) -> str | None:
    from eval_harness.persistence.postgres_models import ScenarioRevisionRecord
    from sqlalchemy import select

    with store._session_factory() as session:
        return session.scalar(
            select(ScenarioRevisionRecord.id)
            .where(ScenarioRevisionRecord.scenario_id == scenario_id)
            .order_by(ScenarioRevisionRecord.revision_number.desc())
            .limit(1)
        )


def _resolve_revision_id_for_control(
    scenario_id: str,
    store: StoreDep,
    requested_revision_id: str | None,
) -> str:
    revision_id = requested_revision_id or _latest_revision_id_for_scenario(
        scenario_id, store
    )
    if revision_id is None:
        raise fastapi.HTTPException(
            status_code=400, detail="Scenario has no revisions yet"
        )
    revision = store.get_scenario_revision(revision_id)
    if revision is None:
        raise fastapi.HTTPException(status_code=404, detail="Revision not found")
    if revision.scenario_id != scenario_id:
        raise fastapi.HTTPException(
            status_code=400, detail="Revision does not belong to this scenario"
        )
    return revision_id


@router.post("/scenarios/{scenario_id}/verify")
def verify_scenario(
    scenario_id: str,
    body: VerifyRequest,
    background: BackgroundTasks,
    store: StoreDep,
) -> dict:
    """Kick off a scenario verification run."""
    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise fastapi.HTTPException(status_code=404, detail="Scenario not found")

    # Resolve the revision to verify: explicit body.revision_id, or the latest
    # draft revision for this scenario.
    revision_id = _resolve_revision_id_for_control(
        scenario_id,
        store,
        body.revision_id,
    )

    dispatcher.dispatch_verify(
        background,
        scenario_id=scenario_id,
        revision_id=revision_id,
        group_id=body.group_id,
    )
    return {"ok": True, "scenario_id": scenario_id, "action": "verify"}


@router.post("/scenarios/{scenario_id}/benchmark")
def benchmark_scenario(
    scenario_id: str,
    body: BenchmarkRequest,
    background: BackgroundTasks,
    store: StoreDep,
) -> dict:
    """Kick off a benchmark run against a verified setup."""
    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise fastapi.HTTPException(status_code=404, detail="Scenario not found")
    if not scenario.current_verified_revision_id:
        raise fastapi.HTTPException(
            status_code=400, detail="Scenario has no verified revision"
        )

    dispatcher.dispatch_benchmark(
        background,
        scenario_id=scenario_id,
        revision_id=scenario.current_verified_revision_id,
        setup_run_id=body.setup_run_id,
        subject_ids=body.subject_ids,
    )
    return {"ok": True, "scenario_id": scenario_id, "action": "benchmark"}


@router.post("/scenarios/{scenario_id}/run-all")
def run_all_scenario(
    scenario_id: str,
    body: RunAllRequest,
    background: BackgroundTasks,
    store: StoreDep,
) -> dict:
    """Chain verify -> benchmark -> judge in one background task."""
    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise fastapi.HTTPException(status_code=404, detail="Scenario not found")
    revision_id = _resolve_revision_id_for_control(
        scenario_id,
        store,
        body.revision_id,
    )

    dispatcher.dispatch_run_all(
        background,
        scenario_id=scenario_id,
        revision_id=revision_id,
        group_id=body.group_id,
        subject_ids=body.subject_ids,
        judge_mode=body.judge_mode,
        judge_anchor_subject=body.judge_anchor_subject,
    )
    return {"ok": True, "scenario_id": scenario_id, "action": "run_all"}
