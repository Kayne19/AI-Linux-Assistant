"""Subject CRUD routes."""

from __future__ import annotations

import fastapi
from fastapi import APIRouter
from sqlalchemy import select

from ..deps import StoreDep
from ..schemas import (
    SubjectCreateRequest,
    SubjectItem,
    SubjectPatchRequest,
)

router = APIRouter(tags=["subjects"])

VALID_ADAPTER_TYPES = frozenset(
    {
        "anthropic",
        "openai",
        "google",
        "anthropic-magi-lite",
        "anthropic-magi-full",
    }
)


def _subject_item_from_row(r) -> SubjectItem:
    return SubjectItem(
        id=r.id,
        subject_name=r.subject_name,
        adapter_type=r.adapter_type,
        display_name=r.display_name,
        adapter_config=r.adapter_config_json if r.adapter_config_json else None,
        is_active=r.is_active,
        created_at=r.created_at,
    )


@router.get("/subjects")
def list_subjects(store: StoreDep) -> list[SubjectItem]:
    from eval_harness.persistence.postgres_models import BenchmarkSubjectRecord

    with store._session_factory() as session:
        rows = session.scalars(
            select(BenchmarkSubjectRecord).order_by(
                BenchmarkSubjectRecord.subject_name.asc()
            )
        )
        return [_subject_item_from_row(r) for r in rows]


@router.get("/subjects/{subject_id}")
def get_subject(subject_id: str, store: StoreDep) -> SubjectItem:
    row = store.get_subject(subject_id)
    if row is None:
        raise fastapi.HTTPException(status_code=404, detail="Subject not found")
    return _subject_item_from_row(row)


@router.post("/subjects", status_code=201)
def create_subject(body: SubjectCreateRequest, store: StoreDep) -> SubjectItem:
    if body.adapter_type not in VALID_ADAPTER_TYPES:
        raise fastapi.HTTPException(
            status_code=422,
            detail=f"Invalid adapter_type. Must be one of: {', '.join(sorted(VALID_ADAPTER_TYPES))}",
        )
    row = store.upsert_subject(
        subject_name=body.subject_name,
        adapter_type=body.adapter_type,
        display_name=body.display_name,
        adapter_config=dict(body.adapter_config or {}),
        is_active=body.is_active,
    )
    return _subject_item_from_row(row)


@router.patch("/subjects/{subject_id}")
def update_subject(
    subject_id: str, body: SubjectPatchRequest, store: StoreDep
) -> SubjectItem:
    from eval_harness.persistence.postgres_models import BenchmarkSubjectRecord

    with store._session_factory() as session:
        row = session.get(BenchmarkSubjectRecord, subject_id)
        if row is None:
            raise fastapi.HTTPException(status_code=404, detail="Subject not found")

        if body.adapter_type is not None:
            if body.adapter_type not in VALID_ADAPTER_TYPES:
                raise fastapi.HTTPException(
                    status_code=422,
                    detail=f"Invalid adapter_type. Must be one of: {', '.join(sorted(VALID_ADAPTER_TYPES))}",
                )
            row.adapter_type = body.adapter_type
        if body.display_name is not None:
            row.display_name = body.display_name
        if body.adapter_config is not None:
            row.adapter_config_json = dict(body.adapter_config)
        if body.is_active is not None:
            row.is_active = body.is_active

        session.commit()
        return _subject_item_from_row(row)


@router.delete("/subjects/{subject_id}")
def delete_subject(subject_id: str, store: StoreDep) -> dict:
    """Soft-delete a subject by setting is_active=false."""
    from eval_harness.persistence.postgres_models import BenchmarkSubjectRecord

    with store._session_factory() as session:
        row = session.get(BenchmarkSubjectRecord, subject_id)
        if row is None:
            raise fastapi.HTTPException(status_code=404, detail="Subject not found")
        row.is_active = False
        session.commit()
        return {"ok": True, "id": subject_id}
