from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

try:
    from sqlalchemy import (
        JSON,
        Boolean,
        DateTime,
        Float,
        ForeignKey,
        Integer,
        String,
        Text,
        UniqueConstraint,
    )
    from sqlalchemy.orm import Mapped, mapped_column, relationship
except ImportError:  # pragma: no cover - optional until SQLAlchemy is installed
    class _TypeStub:
        def __init__(self, *args, **kwargs):
            pass

    class _MappedStub:
        def __class_getitem__(cls, item):
            return Any

    def ForeignKey(*args, **kwargs):  # type: ignore[override]
        return None

    class UniqueConstraint:  # type: ignore[override]
        def __init__(self, *args, **kwargs):
            pass

    JSON = Boolean = DateTime = Float = Integer = String = Text = _TypeStub  # type: ignore[assignment]
    Mapped = _MappedStub  # type: ignore[assignment]

    def mapped_column(*args, **kwargs):  # type: ignore[override]
        return None

    def relationship(*args, **kwargs):  # type: ignore[override]
        return None

from persistence.database import Base


def _utc_now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid4())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("auth_provider", "auth_subject", name="uq_users_auth_provider_subject"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str | None] = mapped_column(String(120), unique=True, index=True, nullable=True)
    auth_provider: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    auth_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    avatar_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )

    projects = relationship("Project", back_populates="user", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_projects_user_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )

    user = relationship("User", back_populates="projects")
    chat_sessions = relationship("ChatSession", back_populates="project", cascade="all, delete-orphan")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )

    project = relationship("Project", back_populates="chat_sessions")
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")
    chat_runs = relationship("ChatRun", back_populates="chat_session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_sessions.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    council_entries: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    session = relationship("ChatSession", back_populates="messages")


class ChatRun(Base):
    __tablename__ = "chat_runs"
    __table_args__ = (
        UniqueConstraint("chat_session_id", "client_request_id", name="uq_chat_runs_chat_client_request"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    chat_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_sessions.id"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    run_kind: Mapped[str] = mapped_column(String(32), default="message", nullable=False, index=True)
    request_content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    magi: Mapped[str] = mapped_column(String(16), default="off", nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(120), nullable=False)
    latest_state_code: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    latest_event_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    partial_assistant_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    pause_state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    worker_id: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    final_user_message_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("chat_messages.id"))
    final_assistant_message_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("chat_messages.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )

    chat_session = relationship("ChatSession", back_populates="chat_runs")
    events = relationship("ChatRunEvent", back_populates="run", cascade="all, delete-orphan")


class ChatRunEvent(Base):
    __tablename__ = "chat_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_chat_run_events_run_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_runs.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    run = relationship("ChatRun", back_populates="events")


class ProjectFact(Base):
    __tablename__ = "project_facts"
    __table_args__ = (UniqueConstraint("project_id", "fact_key", name="uq_project_fact_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    fact_key: Mapped[str] = mapped_column(String(255), nullable=False)
    fact_value: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )


class ProjectIssue(Base):
    __tablename__ = "project_issues"
    __table_args__ = (UniqueConstraint("project_id", "normalized_title", name="uq_project_issue_title"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(120), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectAttempt(Base):
    __tablename__ = "project_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    issue_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("project_issues.id"))
    action: Mapped[str] = mapped_column(Text, default="", nullable=False)
    command: Mapped[str] = mapped_column(Text, default="", nullable=False)
    outcome: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class ProjectConstraint(Base):
    __tablename__ = "project_constraints"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "constraint_key",
            "constraint_value",
            name="uq_project_constraint_value",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    constraint_key: Mapped[str] = mapped_column(String(255), nullable=False)
    constraint_value: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class ProjectPreference(Base):
    __tablename__ = "project_preferences"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "preference_key",
            "preference_value",
            name="uq_project_preference_value",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    preference_key: Mapped[str] = mapped_column(String(255), nullable=False)
    preference_value: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class ProjectMemoryCandidate(Base):
    __tablename__ = "project_memory_candidates"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "item_type",
            "item_key",
            "status",
            name="uq_project_memory_candidate_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    item_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    value_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )


class ProjectState(Base):
    __tablename__ = "project_state"
    __table_args__ = (UniqueConstraint("project_id", "state_key", name="uq_project_state_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    state_key: Mapped[str] = mapped_column(String(120), nullable=False)
    state_value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )
