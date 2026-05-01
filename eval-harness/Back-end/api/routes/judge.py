"""Judge job control and read routes."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from ..deps import StoreDep
from ..jobs import dispatcher
from ..schemas import JudgeJobDetail, JudgeJobItem, JudgeItemDetail, JudgeRequest

router = APIRouter(tags=["judge"])


@router.get("/judge-jobs")
def list_judge_jobs(store: StoreDep) -> list[JudgeJobItem]:
    """List all judge jobs."""
    from eval_harness.persistence.postgres_models import JudgeJobRecord
    from sqlalchemy import select

    with store._session_factory() as session:
        rows = session.scalars(
            select(JudgeJobRecord).order_by(JudgeJobRecord.created_at.desc())
        )
        return [
            JudgeJobItem(
                id=r.id,
                benchmark_run_id=r.benchmark_run_id,
                status=r.status,
                judge_adapter_type=r.judge_adapter_type,
                started_at=r.started_at,
                finished_at=r.finished_at,
                created_at=r.created_at,
            )
            for r in rows
        ]


@router.get("/judge-jobs/{judge_job_id}")
def get_judge_job(judge_job_id: str, store: StoreDep) -> JudgeJobDetail:
    """Get a judge job with its judge items."""
    row = store.get_judge_job(judge_job_id)
    if row is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Judge job not found")

    items = store.list_judge_items(judge_job_id)
    return JudgeJobDetail(
        id=row.id,
        benchmark_run_id=row.benchmark_run_id,
        status=row.status,
        judge_adapter_type=row.judge_adapter_type,
        rubric=row.rubric_json if row.rubric_json else None,
        metadata=row.judge_metadata_json if row.judge_metadata_json else None,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        judge_items=[
            JudgeItemDetail(
                id=i.id,
                evaluation_run_id=i.evaluation_run_id,
                blind_label=i.blind_label,
                parsed_scores=i.parsed_scores_json if i.parsed_scores_json else None,
                summary=i.summary,
                kind=i.kind,
                judge_name=i.judge_name,
            )
            for i in items
        ],
    )


@router.post("/benchmarks/{benchmark_run_id}/judge")
def trigger_judge(
    benchmark_run_id: str,
    body: JudgeRequest,
    background: BackgroundTasks,
    store: StoreDep,
) -> dict:
    """Kick off a judge job for a completed benchmark run."""
    benchmark_run = store.get_benchmark_run(benchmark_run_id)
    if benchmark_run is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404, detail=f"Benchmark run {benchmark_run_id} not found"
        )

    # Determine scenario_id for the semaphore key
    revision = store.get_scenario_revision(benchmark_run.scenario_revision_id)
    scenario_id = (
        revision.scenario_id if revision else benchmark_run.scenario_revision_id
    )

    dispatcher.dispatch_judge(
        background,
        scenario_id=scenario_id,
        benchmark_run_id=benchmark_run_id,
        mode=body.mode,
        anchor_subject=body.anchor_subject,
    )
    return {"ok": True, "benchmark_run_id": benchmark_run_id, "action": "judge"}
