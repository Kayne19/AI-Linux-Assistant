"""Run read and control routes."""

from __future__ import annotations

import fastapi
from fastapi import APIRouter, Query

from ..deps import StoreDep
from ..jobs import cancel_run
from ..schemas import (
    BenchmarkRunItem,
    EvaluationRunItem,
    RunEventItem,
    RunListItem,
    RunListResponse,
    SetupRunEventItem,
    SetupRunItem,
)

router = APIRouter(tags=["runs"])


@router.post("/runs/{run_id}/cancel")
def cancel_run_endpoint(
    run_id: str,
    run_type: str = "benchmark",
) -> dict:
    """Cancel a running benchmark or setup run.

    Query params:
        run_type: 'setup', 'benchmark', or 'evaluation' (default: benchmark)
    """
    if run_type not in ("setup", "benchmark", "evaluation"):
        raise fastapi.HTTPException(
            status_code=400,
            detail="run_type must be 'setup', 'benchmark', or 'evaluation'",
        )
    cancel_run(run_type, run_id)
    return {"ok": True, "run_id": run_id, "run_type": run_type, "action": "cancel"}


# ── Combined run listing ──────────────────────────────────────────────


@router.get("/runs")
def list_runs(
    store: StoreDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> RunListResponse:
    """Paginated combined list of setup and benchmark runs."""
    from eval_harness.persistence.postgres_models import (
        BenchmarkRunRecord,
        ScenarioSetupRunRecord,
    )
    from sqlalchemy import func, select

    with store._session_factory() as session:
        # Count total
        total_setup = (
            session.scalar(select(func.count()).select_from(ScenarioSetupRunRecord))
            or 0
        )
        total_bench = (
            session.scalar(select(func.count()).select_from(BenchmarkRunRecord)) or 0
        )
        total = total_setup + total_bench

        page_start = (page - 1) * page_size
        fetch_limit = page_start + page_size

        # Fetch enough from each source to build the requested merged page.
        setup_rows = session.scalars(
            select(ScenarioSetupRunRecord)
            .order_by(ScenarioSetupRunRecord.created_at.desc())
            .limit(fetch_limit)
        )
        bench_rows = session.scalars(
            select(BenchmarkRunRecord)
            .order_by(BenchmarkRunRecord.created_at.desc())
            .limit(fetch_limit)
        )

        items: list[RunListItem] = []
        for r in setup_rows:
            items.append(
                RunListItem(
                    id=r.id,
                    kind="setup",
                    scenario_revision_id=r.scenario_revision_id,
                    status=r.status,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                    correction_count=r.correction_count,
                    max_corrections=r.max_corrections,
                    staging_handle_id=r.staging_handle_id,
                    broken_image_id=r.broken_image_id,
                    failure_reason=r.failure_reason,
                )
            )
        for r in bench_rows:
            items.append(
                RunListItem(
                    id=r.id,
                    kind="benchmark",
                    scenario_revision_id=r.scenario_revision_id,
                    status=r.status,
                    created_at=r.created_at,
                    verified_setup_run_id=r.verified_setup_run_id,
                    subject_count=r.subject_count,
                    started_at=r.started_at,
                    finished_at=r.finished_at,
                )
            )

        # Sort merged and paginate in-memory
        items.sort(key=lambda i: i.created_at, reverse=True)
        items = items[page_start : page_start + page_size]

    return RunListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ── Setup run read routes ──────────────────────────────────────────────


@router.get("/setup-runs/{setup_run_id}")
def get_setup_run(setup_run_id: str, store: StoreDep) -> SetupRunItem:
    row = store.get_setup_run(setup_run_id)
    if row is None:
        raise fastapi.HTTPException(status_code=404, detail="Setup run not found")
    return SetupRunItem(
        id=row.id,
        scenario_revision_id=row.scenario_revision_id,
        status=row.status,
        staging_handle_id=row.staging_handle_id,
        correction_count=row.correction_count,
        max_corrections=row.max_corrections,
        broken_image_id=row.broken_image_id,
        failure_reason=row.failure_reason,
        planner_approved_at=row.planner_approved_at,
        backend_metadata=row.backend_metadata_json,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/setup-runs/{setup_run_id}/events")
def list_setup_run_events(
    setup_run_id: str,
    store: StoreDep,
    after_round_index: int | None = Query(default=None, ge=0),
    after_seq: int | None = Query(default=None, ge=0),
) -> list[SetupRunEventItem]:
    """List setup run events with optional cursor pagination."""
    from eval_harness.persistence.postgres_models import ScenarioSetupEventRecord
    from sqlalchemy import select

    with store._session_factory() as session:
        q = select(ScenarioSetupEventRecord).where(
            ScenarioSetupEventRecord.setup_run_id == setup_run_id
        )
        if after_round_index is not None:
            q = q.where(
                (ScenarioSetupEventRecord.round_index > after_round_index)
                | (
                    (ScenarioSetupEventRecord.round_index == after_round_index)
                    & (ScenarioSetupEventRecord.seq > (after_seq or -1))
                )
            )
        q = q.order_by(
            ScenarioSetupEventRecord.round_index.asc(),
            ScenarioSetupEventRecord.seq.asc(),
        )
        rows = session.scalars(q).all()

    return [
        SetupRunEventItem(
            id=r.id,
            round_index=r.round_index,
            seq=r.seq,
            actor_role=r.actor_role,
            event_kind=r.event_kind,
            payload=r.payload_json if r.payload_json else None,
            created_at=r.created_at,
        )
        for r in rows
    ]


# ── Benchmark run read routes ──────────────────────────────────────────


@router.get("/benchmarks/{benchmark_run_id}")
def get_benchmark_run(benchmark_run_id: str, store: StoreDep) -> BenchmarkRunItem:
    row = store.get_benchmark_run(benchmark_run_id)
    if row is None:
        raise fastapi.HTTPException(status_code=404, detail="Benchmark run not found")
    return BenchmarkRunItem(
        id=row.id,
        scenario_revision_id=row.scenario_revision_id,
        verified_setup_run_id=row.verified_setup_run_id,
        status=row.status,
        subject_count=row.subject_count,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        metadata_json=row.metadata_json,
    )


@router.get("/benchmarks/{benchmark_run_id}/evaluations")
def list_benchmark_evaluations(
    benchmark_run_id: str, store: StoreDep
) -> list[EvaluationRunItem]:
    rows = store.list_evaluation_runs(benchmark_run_id)
    return [
        EvaluationRunItem(
            id=r.id,
            benchmark_run_id=r.benchmark_run_id,
            subject_id=r.subject_id,
            clone_handle_id=r.clone_handle_id,
            status=r.status,
            repair_success=r.repair_success,
            resolution_result=r.resolution_result_json,
            subject_metadata=r.subject_metadata_json,
            started_at=r.started_at,
            finished_at=r.finished_at,
        )
        for r in rows
    ]


# ── Evaluation run read routes ─────────────────────────────────────────


@router.get("/evaluations/{evaluation_run_id}")
def get_evaluation(evaluation_run_id: str, store: StoreDep) -> EvaluationRunItem:
    row = store.get_evaluation_run(evaluation_run_id)
    if row is None:
        raise fastapi.HTTPException(status_code=404, detail="Evaluation run not found")
    return EvaluationRunItem(
        id=row.id,
        benchmark_run_id=row.benchmark_run_id,
        subject_id=row.subject_id,
        clone_handle_id=row.clone_handle_id,
        status=row.status,
        repair_success=row.repair_success,
        resolution_result=row.resolution_result_json,
        subject_metadata=row.subject_metadata_json,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


@router.get("/evaluations/{evaluation_run_id}/events")
def list_evaluation_events(
    evaluation_run_id: str,
    store: StoreDep,
    after_seq: int | None = Query(default=None, ge=0),
) -> list[RunEventItem]:
    """List evaluation run events with optional cursor pagination."""
    from eval_harness.persistence.postgres_models import EvaluationEventRecord
    from sqlalchemy import select

    with store._session_factory() as session:
        q = select(EvaluationEventRecord).where(
            EvaluationEventRecord.evaluation_run_id == evaluation_run_id
        )
        if after_seq is not None:
            q = q.where(EvaluationEventRecord.seq > after_seq)
        q = q.order_by(EvaluationEventRecord.seq.asc())
        rows = session.scalars(q).all()

    return [
        RunEventItem(
            id=r.id,
            seq=r.seq,
            actor_role=r.actor_role,
            event_kind=r.event_kind,
            payload=r.payload_json if r.payload_json else None,
            created_at=r.created_at,
        )
        for r in rows
    ]
