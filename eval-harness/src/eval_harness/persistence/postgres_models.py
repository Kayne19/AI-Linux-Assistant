from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def _uuid_str() -> str:
    return str(uuid4())


class ScenarioRecord(Base):
    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    scenario_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    current_verified_revision_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scenario_revisions.id", ondelete="SET NULL"), nullable=True
    )
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unverified")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    benchmark_run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ScenarioRevisionRecord(Base):
    __tablename__ = "scenario_revisions"
    __table_args__ = (UniqueConstraint("scenario_id", "revision_number", name="uq_scenario_revision_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    scenario_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    target_image: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    what_it_tests_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    observable_problem_statement: Mapped[str] = mapped_column(Text, nullable=False, default="")
    initial_user_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sabotage_plan_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    verification_plan_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    judge_rubric_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    planner_metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ScenarioSetupRunRecord(Base):
    __tablename__ = "scenario_setup_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    scenario_revision_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="running")
    staging_handle_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    broken_image_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    correction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_corrections: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    planner_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    backend_metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ScenarioSetupEventRecord(Base):
    __tablename__ = "scenario_setup_events"
    __table_args__ = (UniqueConstraint("setup_run_id", "round_index", "seq", name="uq_setup_event_round_seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    setup_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario_setup_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    round_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_role: Mapped[str] = mapped_column(String(64), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class BenchmarkSubjectRecord(Base):
    __tablename__ = "benchmark_subjects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    subject_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    adapter_type: Mapped[str] = mapped_column(String(120), nullable=False)
    adapter_config_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BenchmarkRunRecord(Base):
    __tablename__ = "benchmark_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    scenario_revision_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    verified_setup_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario_setup_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="running")
    subject_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class EvaluationRunRecord(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (UniqueConstraint("benchmark_run_id", "subject_id", name="uq_evaluation_run_subject"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    benchmark_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("benchmark_subjects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    clone_handle_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="running")
    repair_success: Mapped[bool | None] = mapped_column(nullable=True)
    resolution_result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    subject_metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    adapter_session_metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvaluationEventRecord(Base):
    __tablename__ = "evaluation_events"
    __table_args__ = (UniqueConstraint("evaluation_run_id", "seq", name="uq_evaluation_event_seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    evaluation_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_role: Mapped[str] = mapped_column(String(64), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class JudgeJobRecord(Base):
    __tablename__ = "judge_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    benchmark_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    rubric_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    judge_adapter_type: Mapped[str] = mapped_column(String(120), nullable=False)
    judge_metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JudgeItemRecord(Base):
    __tablename__ = "judge_items"
    __table_args__ = (
        UniqueConstraint("judge_job_id", "blind_label", name="uq_judge_item_blind_label"),
        UniqueConstraint("judge_job_id", "evaluation_run_id", name="uq_judge_item_eval_run"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    judge_job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("judge_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evaluation_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blind_label: Mapped[str] = mapped_column(String(120), nullable=False)
    blinded_transcript_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_judge_response_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    parsed_scores_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
