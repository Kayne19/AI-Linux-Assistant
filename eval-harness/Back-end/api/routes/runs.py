"""Run read and control routes."""

from __future__ import annotations

from fastapi import APIRouter

from ..jobs import cancel_run

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
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail="run_type must be 'setup', 'benchmark', or 'evaluation'",
        )
    cancel_run(run_type, run_id)
    return {"ok": True, "run_id": run_id, "run_type": run_type, "action": "cancel"}
