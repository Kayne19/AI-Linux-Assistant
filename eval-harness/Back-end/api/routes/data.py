"""Generic data browser routes — read-only table inspection with pagination."""

from __future__ import annotations

import datetime
from typing import Any

import fastapi
from fastapi import APIRouter, Query
from sqlalchemy import inspect, select
from sqlalchemy.sql import func

from ..deps import StoreDep
from ..schemas import DataTableResponse

router = APIRouter(tags=["data"])

# Map friendly names to SQLAlchemy model classes (lazy imports for safety).
_TABLE_NAMES = frozenset(
    {
        "scenarios",
        "scenario_revisions",
        "scenario_setup_runs",
        "scenario_setup_events",
        "benchmark_subjects",
        "benchmark_runs",
        "evaluation_runs",
        "evaluation_events",
        "judge_jobs",
        "judge_items",
    }
)

# Display names shown in the UI
_TABLE_LABELS: dict[str, str] = {
    "scenarios": "Scenarios",
    "scenario_revisions": "Scenario Revisions",
    "scenario_setup_runs": "Setup Runs",
    "scenario_setup_events": "Setup Events",
    "benchmark_subjects": "Subjects",
    "benchmark_runs": "Benchmark Runs",
    "evaluation_runs": "Evaluation Runs",
    "evaluation_events": "Evaluation Events",
    "judge_jobs": "Judge Jobs",
    "judge_items": "Judge Items",
}


# Direct mapping from table name (snake_case plural) to ORM model class name.
_TABLE_TO_MODEL: dict[str, str] = {
    "scenarios": "ScenarioRecord",
    "scenario_revisions": "ScenarioRevisionRecord",
    "scenario_setup_runs": "ScenarioSetupRunRecord",
    "scenario_setup_events": "ScenarioSetupEventRecord",
    "benchmark_subjects": "BenchmarkSubjectRecord",
    "benchmark_runs": "BenchmarkRunRecord",
    "evaluation_runs": "EvaluationRunRecord",
    "evaluation_events": "EvaluationEventRecord",
    "judge_jobs": "JudgeJobRecord",
    "judge_items": "JudgeItemRecord",
}


def _model_for_table(table_name: str):
    """Return the ORM model class for a table name."""
    if table_name not in _TABLE_NAMES:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f"Unknown table '{table_name}'. Valid: {', '.join(sorted(_TABLE_NAMES))}",
        )
    from eval_harness.persistence import postgres_models as pm

    return getattr(pm, _TABLE_TO_MODEL[table_name])


def _serialize_value(value: Any) -> Any:
    """Convert SQLAlchemy column values to JSON-safe types."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)):
        return value
    if isinstance(value, (list, dict)):
        return value
    return str(value)


def _row_to_dict(row, column_names: list[str]) -> dict[str, Any]:
    """Convert an ORM row to a plain dict with JSON-safe values."""
    result: dict[str, Any] = {}
    for col in column_names:
        result[col] = _serialize_value(getattr(row, col, None))
    return result


@router.get("/data/tables")
def list_tables() -> list[dict[str, str]]:
    """Return metadata about all available data tables."""
    return [
        {"name": name, "label": _TABLE_LABELS.get(name, name)}
        for name in sorted(_TABLE_NAMES)
    ]


@router.get("/data/{table_name}")
def browse_table(
    table_name: str,
    store: StoreDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    sort_by: str = Query(default=""),
    sort_dir: str = Query(default="asc"),
) -> DataTableResponse:
    """Paginated read-only browse for any eval harness table."""
    model = _model_for_table(table_name)
    mapper = inspect(model)
    column_names = [c.key for c in mapper.columns]

    with store._session_factory() as session:
        # Count
        count_q = select(func.count()).select_from(model)
        total = session.scalar(count_q) or 0

        # Build query with optional sort
        q = select(model)
        if sort_by and sort_by in column_names:
            col = getattr(model, sort_by)
            q = q.order_by(col.asc() if sort_dir == "asc" else col.desc())
        else:
            # Default sort by first column
            q = q.order_by(getattr(model, column_names[0]).asc())

        q = q.offset((page - 1) * page_size).limit(page_size)
        rows = list(session.scalars(q))

    return DataTableResponse(
        table=table_name,
        columns=column_names,
        rows=[_row_to_dict(r, column_names) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
