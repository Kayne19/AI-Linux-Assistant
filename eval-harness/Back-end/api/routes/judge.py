"""Judge job control routes."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from ..deps import StoreDep
from ..jobs import dispatcher
from ..schemas import JudgeRequest

router = APIRouter(tags=["judge"])


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
