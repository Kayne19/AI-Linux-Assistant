from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class ComputeJob(Base):
    __tablename__ = "compute_jobs"
    __table_args__ = (
        UniqueConstraint("scope_key", "client_token", name="uq_compute_jobs_scope_client_token"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    queue_name: Mapped[str] = mapped_column(String(120), default="default", nullable=False, index=True)
    scope_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    owner_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    job_kind: Mapped[str] = mapped_column(String(64), default="job", nullable=False, index=True)
    client_token: Mapped[str] = mapped_column(String(120), nullable=False)
    blocks_scope: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    blocks_owner: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    latest_state_code: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    latest_event_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    partial_output_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    checkpoint_state_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    worker_id: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    events = relationship("ComputeJobEvent", back_populates="job", cascade="all, delete-orphan")


class ComputeJobEvent(Base):
    __tablename__ = "compute_job_events"
    __table_args__ = (
        UniqueConstraint("job_id", "seq", name="uq_compute_job_events_job_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("compute_jobs.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    job = relationship("ComputeJob", back_populates="events")


__all__ = [
    "Base",
    "ComputeJob",
    "ComputeJobEvent",
    "new_uuid",
    "utc_now",
]
