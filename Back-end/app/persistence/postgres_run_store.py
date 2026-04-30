from datetime import timedelta

from orchestration.normalized_inputs import empty_normalized_inputs
from utils.time_utils import _iso, _utc_now
from persistence.database import Base, get_engine, get_session_factory

try:
    from sqlalchemy import and_, func, inspect, or_, select
    from sqlalchemy.exc import IntegrityError
except ImportError:  # pragma: no cover - optional until SQLAlchemy is installed
    and_ = None
    func = None
    inspect = None
    or_ = None
    select = None
    IntegrityError = None

from persistence.postgres_models import (
    ChatMessage,
    ChatRun,
    ChatRunEvent,
    ChatSession,
    User,
)


ACTIVE_RUN_STATUSES = {
    "queued",
    "running",
    "cancel_requested",
    "pause_requested",
    "paused",
}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
MESSAGE_RUN_KIND = "message"
AUTO_NAME_RUN_KIND = "auto_name"
PAUSE_REQUESTABLE_MAGI_STATES = {
    "OPENING_ARGUMENTS",
    "ROLE_EAGER",
    "ROLE_SKEPTIC",
    "ROLE_HISTORIAN",
    "DISCUSSION_GATE",
    "DISCUSSION",
    "DISCUSSION_EAGER",
    "DISCUSSION_SKEPTIC",
    "DISCUSSION_HISTORIAN",
}


class ActiveChatRunExistsError(RuntimeError):
    pass


class ActiveRunLimitExceededError(RuntimeError):
    pass


class RunNotFoundError(RuntimeError):
    pass


class RunRequeueError(RuntimeError):
    pass


class RunOwnershipLostError(RuntimeError):
    pass


class RunStateConflictError(RuntimeError):
    pass


class PostgresRunStore:
    def __init__(self, session_factory=None, redis_client=None):
        if select is None or func is None or and_ is None or or_ is None:
            raise ImportError(
                "SQLAlchemy is required for PostgresRunStore. "
                "Install sqlalchemy and alembic in the AI-Linux-Assistant environment."
            )
        self.session_factory = session_factory or get_session_factory()
        self._redis_client = redis_client
        self._ensure_run_schema()

    def _publish(self, run_id, seq, event_type, code, payload_json, created_at=None):
        """Publish a serialized event to the Redis fanout channel. Never raises."""
        if self._redis_client is None:
            return
        try:
            from streaming.event_serializer import serialize_run_event
            from streaming.redis_events import publish_event

            publish_event(
                self._redis_client,
                run_id,
                serialize_run_event(
                    seq, event_type, code, payload_json, created_at=created_at
                ),
            )
        except Exception:
            pass

    def _get_bound_engine(self):
        bind = getattr(self.session_factory, "kw", {}).get("bind")
        if bind is not None:
            return bind

        with self.session_factory() as session:
            bind = session.get_bind()
        return bind or get_engine()

    def _ensure_run_schema(self):
        engine = self._get_bound_engine()
        Base.metadata.create_all(
            bind=engine,
            tables=[ChatRun.__table__, ChatRunEvent.__table__],
            checkfirst=True,
        )
        if inspect is None:
            return
        inspector = inspect(engine)
        chat_run_columns = {
            column["name"] for column in inspector.get_columns("chat_runs")
        }
        with engine.begin() as connection:
            if "run_kind" not in chat_run_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE chat_runs ADD COLUMN run_kind VARCHAR(32) NOT NULL DEFAULT 'message'"
                )
            if "normalized_inputs_json" not in chat_run_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE chat_runs ADD COLUMN normalized_inputs_json JSON NULL"
                )
            if "pause_state_json" not in chat_run_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE chat_runs ADD COLUMN pause_state_json JSON NULL"
                )
        if hasattr(inspector, "clear_cache"):
            inspector.clear_cache()

    def _active_run_predicates(self, include_background=False):
        predicates = [ChatRun.status.in_(ACTIVE_RUN_STATUSES)]
        if not include_background:
            predicates.append(ChatRun.run_kind == MESSAGE_RUN_KIND)
        return predicates

    def _session(self):
        return self.session_factory()

    def _load_run_for_update(
        self, session, run_id, *, worker_id="", require_active_owner=False
    ):
        if require_active_owner and worker_id:
            run = session.scalar(
                select(ChatRun)
                .where(
                    ChatRun.id == run_id,
                    ChatRun.worker_id == worker_id,
                    ChatRun.status.in_(ACTIVE_RUN_STATUSES),
                    ChatRun.lease_expires_at.is_not(None),
                    ChatRun.lease_expires_at > _utc_now(),
                )
                .with_for_update()
            )
            if run is None:
                raise RunOwnershipLostError(
                    f"Worker '{worker_id}' no longer owns run '{run_id}'."
                )
            return run

        run = session.scalar(
            select(ChatRun).where(ChatRun.id == run_id).with_for_update()
        )
        if run is None:
            raise RunNotFoundError(f"Unknown run '{run_id}'")
        return run

    def create_or_reuse_run(
        self,
        *,
        chat_session_id,
        project_id,
        user_id,
        request_content,
        magi,
        client_request_id,
        max_active_runs_per_user,
        run_kind=MESSAGE_RUN_KIND,
    ):
        run_kind = (run_kind or MESSAGE_RUN_KIND).strip() or MESSAGE_RUN_KIND
        with self._session() as session:
            session.scalar(select(User).where(User.id == user_id).with_for_update())
            session.scalar(
                select(ChatSession)
                .where(ChatSession.id == chat_session_id)
                .with_for_update()
            )

            existing = session.scalar(
                select(ChatRun).where(
                    ChatRun.chat_session_id == chat_session_id,
                    ChatRun.client_request_id == client_request_id,
                )
            )
            if existing is not None:
                return existing

            if run_kind == MESSAGE_RUN_KIND:
                active_chat_run = session.scalar(
                    select(ChatRun)
                    .where(
                        ChatRun.chat_session_id == chat_session_id,
                        *self._active_run_predicates(),
                    )
                    .order_by(ChatRun.created_at.desc())
                    .limit(1)
                )
                if active_chat_run is not None:
                    raise ActiveChatRunExistsError(
                        f"Chat '{chat_session_id}' already has an active run."
                    )

                active_count = (
                    session.scalar(
                        select(func.count())
                        .select_from(ChatRun)
                        .where(
                            ChatRun.user_id == user_id,
                            *self._active_run_predicates(),
                        )
                    )
                    or 0
                )
                if int(active_count) >= max(1, int(max_active_runs_per_user)):
                    raise ActiveRunLimitExceededError(
                        f"User '{user_id}' exceeded the active run limit."
                    )

            run = ChatRun(
                chat_session_id=chat_session_id,
                project_id=project_id,
                user_id=user_id,
                status="queued",
                run_kind=run_kind,
                request_content=request_content,
                magi=(magi or "off").strip() or "off",
                client_request_id=client_request_id,
                normalized_inputs_json=empty_normalized_inputs(request_content),
            )
            session.add(run)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(ChatRun).where(
                        ChatRun.chat_session_id == chat_session_id,
                        ChatRun.client_request_id == client_request_id,
                    )
                )
                if existing is not None:
                    return existing
                if run_kind == MESSAGE_RUN_KIND:
                    active_chat_run = session.scalar(
                        select(ChatRun)
                        .where(
                            ChatRun.chat_session_id == chat_session_id,
                            *self._active_run_predicates(),
                        )
                        .order_by(ChatRun.created_at.desc())
                        .limit(1)
                    )
                    if active_chat_run is not None:
                        raise ActiveChatRunExistsError(
                            f"Chat '{chat_session_id}' already has an active run."
                        )
                    active_count = (
                        session.scalar(
                            select(func.count())
                            .select_from(ChatRun)
                            .where(
                                ChatRun.user_id == user_id,
                                *self._active_run_predicates(),
                            )
                        )
                        or 0
                    )
                    if int(active_count) >= max(1, int(max_active_runs_per_user)):
                        raise ActiveRunLimitExceededError(
                            f"User '{user_id}' exceeded the active run limit."
                        )
                raise
            session.refresh(run)
        if self._redis_client is not None:
            try:
                from streaming.redis_events import publish_wakeup

                publish_wakeup(self._redis_client)
            except Exception:
                pass
        return run

    def _get_run(self, run_id):
        with self._session() as session:
            return session.scalar(select(ChatRun).where(ChatRun.id == run_id))

    def get_run_for_user(self, run_id, user_id):
        with self._session() as session:
            return session.scalar(
                select(ChatRun).where(ChatRun.id == run_id, ChatRun.user_id == user_id)
            )

    def get_active_run_for_chat(self, chat_session_id):
        with self._session() as session:
            return session.scalar(
                select(ChatRun)
                .where(
                    ChatRun.chat_session_id == chat_session_id,
                    *self._active_run_predicates(),
                )
                .order_by(ChatRun.created_at.desc())
                .limit(1)
            )

    def get_active_run_for_chat_for_user(self, chat_session_id, user_id):
        with self._session() as session:
            return session.scalar(
                select(ChatRun)
                .where(
                    ChatRun.chat_session_id == chat_session_id,
                    ChatRun.user_id == user_id,
                    *self._active_run_predicates(),
                )
                .order_by(ChatRun.created_at.desc())
                .limit(1)
            )

    def get_active_runs_for_chat_ids(self, chat_session_ids, *, user_id=None):
        chat_session_ids = [
            str(chat_id) for chat_id in chat_session_ids or [] if chat_id
        ]
        if not chat_session_ids:
            return {}
        with self._session() as session:
            filters = [
                ChatRun.chat_session_id.in_(chat_session_ids),
                *self._active_run_predicates(),
            ]
            if user_id is not None:
                filters.append(ChatRun.user_id == user_id)
            rows = list(
                session.scalars(
                    select(ChatRun).where(*filters).order_by(ChatRun.created_at.desc())
                )
            )
        results = {}
        for row in rows:
            results.setdefault(row.chat_session_id, row)
        return results

    def list_runs_for_chat(self, chat_session_id, page=1, page_size=20, status=None):
        page = max(1, int(page))
        page_size = max(1, int(page_size))
        offset = (page - 1) * page_size
        with self._session() as session:
            filters = [ChatRun.chat_session_id == chat_session_id]
            normalized_status = (status or "").strip()
            if normalized_status:
                filters.append(ChatRun.status == normalized_status)

            total = (
                session.scalar(
                    select(func.count()).select_from(ChatRun).where(*filters)
                )
                or 0
            )
            runs = list(
                session.scalars(
                    select(ChatRun)
                    .where(*filters)
                    .order_by(ChatRun.created_at.desc(), ChatRun.id.desc())
                    .offset(offset)
                    .limit(page_size)
                )
            )
        return runs, int(total)

    def list_runs_for_chat_for_user(
        self, chat_session_id, user_id, page=1, page_size=20, status=None
    ):
        page = max(1, int(page))
        page_size = max(1, int(page_size))
        offset = (page - 1) * page_size
        with self._session() as session:
            filters = [
                ChatRun.chat_session_id == chat_session_id,
                ChatRun.user_id == user_id,
            ]
            normalized_status = (status or "").strip()
            if normalized_status:
                filters.append(ChatRun.status == normalized_status)
            total = (
                session.scalar(
                    select(func.count()).select_from(ChatRun).where(*filters)
                )
                or 0
            )
            runs = list(
                session.scalars(
                    select(ChatRun)
                    .where(*filters)
                    .order_by(ChatRun.created_at.desc(), ChatRun.id.desc())
                    .offset(offset)
                    .limit(page_size)
                )
            )
        return runs, int(total)

    def list_events_after(self, run_id, after_seq=0, limit=200):
        with self._session() as session:
            stmt = (
                select(ChatRunEvent)
                .where(
                    ChatRunEvent.run_id == run_id,
                    ChatRunEvent.seq > max(0, int(after_seq)),
                )
                .order_by(ChatRunEvent.seq.asc())
                .limit(max(1, int(limit)))
            )
            return list(session.scalars(stmt))

    def append_event(self, run_id, event_type, code="", payload=None):
        with self._session() as session:
            run = self._load_run_for_update(session, run_id)

            next_seq = int(run.latest_event_seq or 0) + 1
            run.latest_event_seq = next_seq
            if event_type == "state":
                run.latest_state_code = code or ""
            elif event_type == "event" and code == "text_delta":
                delta = str((payload or {}).get("delta", "") or "")
                if delta:
                    run.partial_assistant_text = (
                        run.partial_assistant_text or ""
                    ) + delta
            elif event_type == "error":
                run.error_message = str((payload or {}).get("message", "") or "")
            elif event_type == "paused":
                run.pause_state_json = (
                    payload.get("pause_state") if isinstance(payload, dict) else None
                )

            event = ChatRunEvent(
                run_id=run_id,
                seq=next_seq,
                type=event_type,
                code=code or "",
                payload_json=payload or None,
            )
            session.add(event)
            session.commit()
            session.refresh(event)
        self._publish(
            run_id,
            next_seq,
            event_type,
            code or "",
            payload,
            created_at=event.created_at,
        )
        return event

    def append_event_for_worker(
        self, run_id, worker_id, event_type, code="", payload=None
    ):
        with self._session() as session:
            run = self._load_run_for_update(
                session,
                run_id,
                worker_id=worker_id,
                require_active_owner=True,
            )

            next_seq = int(run.latest_event_seq or 0) + 1
            run.latest_event_seq = next_seq
            if event_type == "state":
                run.latest_state_code = code or ""
            elif event_type == "event" and code == "text_delta":
                delta = str((payload or {}).get("delta", "") or "")
                if delta:
                    run.partial_assistant_text = (
                        run.partial_assistant_text or ""
                    ) + delta
            elif event_type == "error":
                run.error_message = str((payload or {}).get("message", "") or "")
            elif event_type == "paused":
                run.pause_state_json = (
                    payload.get("pause_state") if isinstance(payload, dict) else None
                )

            event = ChatRunEvent(
                run_id=run_id,
                seq=next_seq,
                type=event_type,
                code=code or "",
                payload_json=payload or None,
            )
            session.add(event)
            session.commit()
            session.refresh(event)
        self._publish(
            run_id,
            next_seq,
            event_type,
            code or "",
            payload,
            created_at=event.created_at,
        )
        return event

    def replace_normalized_inputs_for_worker(
        self, run_id, worker_id, normalized_inputs
    ):
        with self._session() as session:
            run = self._load_run_for_update(
                session,
                run_id,
                worker_id=worker_id,
                require_active_owner=True,
            )
            normalized_inputs = dict(normalized_inputs or {})
            if (run.normalized_inputs_json or {}) == normalized_inputs:
                return dict(run.normalized_inputs_json or {})
            run.normalized_inputs_json = normalized_inputs
            session.commit()
            session.refresh(run)
            return dict(run.normalized_inputs_json or {})

    def _append_checkpoint_event(
        self,
        session,
        run,
        code,
        payload,
        *,
        partial_assistant_delta="",
    ):
        partial_assistant_delta = str(partial_assistant_delta or "")
        if partial_assistant_delta:
            run.partial_assistant_text = (
                run.partial_assistant_text or ""
            ) + partial_assistant_delta
        next_seq = int(run.latest_event_seq or 0) + 1
        run.latest_event_seq = next_seq
        event = ChatRunEvent(
            run_id=run.id,
            seq=next_seq,
            type="event",
            code=code,
            payload_json=payload,
        )
        session.add(event)
        session.commit()
        session.refresh(event)
        self._publish(
            run.id, next_seq, "event", code, payload, created_at=event.created_at
        )
        return event

    def append_text_checkpoint(self, run_id, delta_chunk, window):
        """Persist buffered text deltas as one replayable checkpoint event."""
        delta_chunk = str(delta_chunk or "")
        if not delta_chunk:
            return None
        with self._session() as session:
            run = self._load_run_for_update(session, run_id)
            payload = {
                "text": (run.partial_assistant_text or "") + delta_chunk,
                "window": int(window),
            }
            return self._append_checkpoint_event(
                session,
                run,
                "text_checkpoint",
                payload,
                partial_assistant_delta=delta_chunk,
            )

    def append_text_checkpoint_for_worker(self, run_id, worker_id, delta_chunk, window):
        delta_chunk = str(delta_chunk or "")
        if not delta_chunk:
            return None
        with self._session() as session:
            run = self._load_run_for_update(
                session,
                run_id,
                worker_id=worker_id,
                require_active_owner=True,
            )
            payload = {
                "text": (run.partial_assistant_text or "") + delta_chunk,
                "window": int(window),
            }
            return self._append_checkpoint_event(
                session,
                run,
                "text_checkpoint",
                payload,
                partial_assistant_delta=delta_chunk,
            )

    def append_magi_role_text_checkpoint(
        self, run_id, role, phase, round_number, text, window
    ):
        text = str(text or "")
        if not text:
            return None
        with self._session() as session:
            run = self._load_run_for_update(session, run_id)
            payload = {
                "role": str(role or ""),
                "phase": str(phase or ""),
                "round": round_number,
                "text": text,
                "window": int(window),
            }
            return self._append_checkpoint_event(
                session, run, "magi_role_text_checkpoint", payload
            )

    def append_magi_role_text_checkpoint_for_worker(
        self,
        run_id,
        worker_id,
        role,
        phase,
        round_number,
        text,
        window,
    ):
        text = str(text or "")
        if not text:
            return None
        with self._session() as session:
            run = self._load_run_for_update(
                session,
                run_id,
                worker_id=worker_id,
                require_active_owner=True,
            )
            payload = {
                "role": str(role or ""),
                "phase": str(phase or ""),
                "round": round_number,
                "text": text,
                "window": int(window),
            }
            return self._append_checkpoint_event(
                session, run, "magi_role_text_checkpoint", payload
            )

    def claim_next_run(self, worker_id, lease_seconds):
        now = _utc_now()
        with self._session() as session:
            candidate = session.scalar(
                select(ChatRun)
                .where(
                    or_(
                        ChatRun.status == "queued",
                        and_(
                            ChatRun.status.in_(
                                {"running", "cancel_requested", "pause_requested"}
                            ),
                            ChatRun.lease_expires_at.is_not(None),
                            ChatRun.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(ChatRun.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if candidate is None:
                return None

            candidate.worker_id = worker_id
            candidate.lease_expires_at = now + timedelta(
                seconds=max(1, int(lease_seconds))
            )
            if candidate.started_at is None:
                candidate.started_at = now
            if candidate.status == "queued":
                candidate.status = "running"
            session.commit()
            session.refresh(candidate)
            return candidate

    def heartbeat(self, run_id, worker_id, lease_seconds):
        with self._session() as session:
            run = session.scalar(
                select(ChatRun)
                .where(
                    ChatRun.id == run_id,
                    ChatRun.worker_id == worker_id,
                    ChatRun.status.in_(ACTIVE_RUN_STATUSES),
                )
                .with_for_update()
            )
            if run is None:
                return False
            run.lease_expires_at = _utc_now() + timedelta(
                seconds=max(1, int(lease_seconds))
            )
            session.commit()
            return True

    def request_cancel(self, run_id):
        with self._session() as session:
            run = self._load_run_for_update(session, run_id)
            if run is None:
                raise RunNotFoundError(f"Unknown run '{run_id}'")
            if run.status in TERMINAL_RUN_STATUSES:
                session.commit()
                return run
            run.cancel_requested = True
            run.status = "cancel_requested"
            session.commit()
            session.refresh(run)
            return run

    def request_running_cancel(self, run_id):
        with self._session() as session:
            run = self._load_run_for_update(session, run_id)
            if run.status in TERMINAL_RUN_STATUSES:
                session.commit()
                return run
            if run.status not in {"running", "cancel_requested", "pause_requested"}:
                raise RunStateConflictError(
                    f"Run '{run_id}' is '{run.status}' and cannot be marked cancel_requested for worker handling."
                )
            run.cancel_requested = True
            run.status = "cancel_requested"
            session.commit()
            session.refresh(run)
            return run

    def request_pause(self, run_id):
        with self._session() as session:
            run = self._load_run_for_update(session, run_id)
            if run.status in TERMINAL_RUN_STATUSES:
                session.commit()
                return run
            if run.status in {"pause_requested", "paused"}:
                session.commit()
                session.refresh(run)
                return run
            if run.status != "running":
                raise RunStateConflictError(
                    f"Run '{run_id}' is '{run.status}' and cannot be paused."
                )
            run.status = "pause_requested"
            next_seq = int(run.latest_event_seq or 0) + 1
            run.latest_event_seq = next_seq
            payload = {"message": "Pause requested."}
            event = ChatRunEvent(
                run_id=run_id,
                seq=next_seq,
                type="event",
                code="magi_pause_requested",
                payload_json=payload,
            )
            session.add(event)
            session.flush()
            created_at = event.created_at
            session.commit()
            session.refresh(run)
        self._publish(
            run_id,
            next_seq,
            "event",
            "magi_pause_requested",
            payload,
            created_at=created_at,
        )
        return run

    def cancel_queued_run(
        self, run_id, *, error_message="Run cancelled.", event_payload=None
    ):
        created_at = None
        with self._session() as session:
            run = self._load_run_for_update(session, run_id)
            if run.status in TERMINAL_RUN_STATUSES:
                session.commit()
                return run
            if run.status != "queued":
                raise RunStateConflictError(
                    f"Run '{run_id}' is '{run.status}' and cannot be terminalized as a queued cancellation."
                )
            run.status = "cancelled"
            run.pause_state_json = None
            run.cancel_requested = True
            run.error_message = error_message or ""
            run.finished_at = _utc_now()
            run.lease_expires_at = None
            run.worker_id = ""
            next_seq = int(run.latest_event_seq or 0) + 1
            run.latest_event_seq = next_seq
            cancelled_payload = event_payload or {
                "message": run.error_message or "Run cancelled."
            }
            event = ChatRunEvent(
                run_id=run_id,
                seq=next_seq,
                type="cancelled",
                code="cancelled",
                payload_json=cancelled_payload,
            )
            session.add(event)
            session.flush()
            created_at = event.created_at
            session.commit()
            session.refresh(run)
        self._publish(
            run_id,
            next_seq,
            "cancelled",
            "cancelled",
            cancelled_payload,
            created_at=created_at,
        )
        return run

    def is_cancel_requested(self, run_id):
        with self._session() as session:
            run = session.scalar(select(ChatRun).where(ChatRun.id == run_id))
            if run is None:
                raise RunNotFoundError(f"Unknown run '{run_id}'")
            return bool(run.cancel_requested)

    def is_pause_requested(self, run_id):
        with self._session() as session:
            run = session.scalar(select(ChatRun).where(ChatRun.id == run_id))
            if run is None:
                raise RunNotFoundError(f"Unknown run '{run_id}'")
            return (run.status or "") == "pause_requested"

    def get_latest_event_by_code(self, run_id, code):
        with self._session() as session:
            return session.scalar(
                select(ChatRunEvent)
                .where(
                    ChatRunEvent.run_id == run_id,
                    ChatRunEvent.code == (code or ""),
                )
                .order_by(ChatRunEvent.seq.desc())
                .limit(1)
            )

    def mark_paused(self, run_id, *, worker_id, pause_state=None, event_payload=None):
        created_at = None
        with self._session() as session:
            run = self._load_run_for_update(
                session,
                run_id,
                worker_id=worker_id,
                require_active_owner=bool(worker_id),
            )
            if run.status == "completed":
                session.commit()
                return run
            run.status = "paused"
            run.pause_state_json = dict(pause_state or {})
            run.lease_expires_at = None
            run.worker_id = ""
            next_seq = int(run.latest_event_seq or 0) + 1
            run.latest_event_seq = next_seq
            payload = dict(event_payload or {})
            if run.pause_state_json and "pause_state" not in payload:
                payload["pause_state"] = run.pause_state_json
            if "message" not in payload:
                payload["message"] = "Run paused."
            event = ChatRunEvent(
                run_id=run_id,
                seq=next_seq,
                type="paused",
                code="paused",
                payload_json=payload,
            )
            session.add(event)
            session.flush()
            created_at = event.created_at
            session.commit()
            session.refresh(run)
        self._publish(
            run_id, next_seq, "paused", "paused", payload, created_at=created_at
        )
        return run

    def resume_paused_run(self, run_id, *, input_text="", input_kind="fact"):
        intervention_text = str(input_text or "").strip()
        normalized_kind = str(input_kind or "fact").strip() or "fact"
        publish_rows = []
        with self._session() as session:
            run = self._load_run_for_update(session, run_id)
            if run.status != "paused":
                raise RunStateConflictError(
                    f"Run '{run_id}' is '{run.status}' and cannot be resumed."
                )
            pause_state = dict(run.pause_state_json or {})
            interventions = list(pause_state.get("interventions") or [])
            if intervention_text:
                checkpoint = dict(pause_state.get("resume_checkpoint") or {})
                intervention = {
                    "entry_kind": "user_intervention",
                    "role": "user",
                    "phase": "discussion",
                    "round": checkpoint.get("round"),
                    "after_role_count": int(checkpoint.get("after_role_count", 0) or 0),
                    "input_kind": normalized_kind,
                    "text": intervention_text,
                }
                interventions.append(intervention)
                pause_state["interventions"] = interventions
                next_seq = int(run.latest_event_seq or 0) + 1
                run.latest_event_seq = next_seq
                event = ChatRunEvent(
                    run_id=run_id,
                    seq=next_seq,
                    type="event",
                    code="magi_intervention_added",
                    payload_json=intervention,
                )
                session.add(event)
                session.flush()
                publish_rows.append(
                    (
                        next_seq,
                        "event",
                        "magi_intervention_added",
                        intervention,
                        event.created_at,
                    )
                )
            run.pause_state_json = pause_state or None
            run.status = "queued"
            run.worker_id = ""
            run.lease_expires_at = None
            next_seq = int(run.latest_event_seq or 0) + 1
            run.latest_event_seq = next_seq
            resumed_payload = {
                "message": "Run resumed.",
                "has_input": bool(intervention_text),
                "input_kind": normalized_kind if intervention_text else "",
            }
            resumed_event = ChatRunEvent(
                run_id=run_id,
                seq=next_seq,
                type="event",
                code="magi_resumed",
                payload_json=resumed_payload,
            )
            session.add(resumed_event)
            session.flush()
            publish_rows.append(
                (
                    next_seq,
                    "event",
                    "magi_resumed",
                    resumed_payload,
                    resumed_event.created_at,
                )
            )
            session.commit()
            session.refresh(run)
        for next_seq, event_type, code, payload, created_at in publish_rows:
            self._publish(
                run_id, next_seq, event_type, code, payload, created_at=created_at
            )
        if self._redis_client is not None:
            try:
                from streaming.redis_events import publish_wakeup

                publish_wakeup(self._redis_client)
            except Exception:
                pass
        return run

    def mark_failed(
        self, run_id, *, worker_id="", error_message="", event_payload=None
    ):
        created_at = None
        with self._session() as session:
            run = self._load_run_for_update(
                session,
                run_id,
                worker_id=worker_id,
                require_active_owner=bool(worker_id),
            )
            if run.status == "completed":
                session.commit()
                return run
            run.status = "failed"
            run.pause_state_json = None
            run.error_message = error_message or ""
            if worker_id:
                run.worker_id = worker_id
            run.finished_at = _utc_now()
            run.lease_expires_at = None
            next_seq = int(run.latest_event_seq or 0) + 1
            run.latest_event_seq = next_seq
            error_payload = event_payload or {
                "message": run.error_message or "Run failed."
            }
            event = ChatRunEvent(
                run_id=run_id,
                seq=next_seq,
                type="error",
                code="error",
                payload_json=error_payload,
            )
            session.add(event)
            session.flush()
            created_at = event.created_at
            session.commit()
            session.refresh(run)
        self._publish(
            run_id, next_seq, "error", "error", error_payload, created_at=created_at
        )
        return run

    def mark_cancelled(
        self,
        run_id,
        *,
        worker_id="",
        error_message="Run cancelled.",
        event_payload=None,
    ):
        created_at = None
        with self._session() as session:
            run = self._load_run_for_update(
                session,
                run_id,
                worker_id=worker_id,
                require_active_owner=bool(worker_id),
            )
            if run.status == "completed":
                session.commit()
                return run
            run.status = "cancelled"
            run.pause_state_json = None
            run.cancel_requested = True
            run.error_message = error_message or ""
            if worker_id:
                run.worker_id = worker_id
            run.finished_at = _utc_now()
            run.lease_expires_at = None
            next_seq = int(run.latest_event_seq or 0) + 1
            run.latest_event_seq = next_seq
            cancelled_payload = event_payload or {
                "message": run.error_message or "Run cancelled."
            }
            event = ChatRunEvent(
                run_id=run_id,
                seq=next_seq,
                type="cancelled",
                code="cancelled",
                payload_json=cancelled_payload,
            )
            session.add(event)
            session.flush()
            created_at = event.created_at
            session.commit()
            session.refresh(run)
        self._publish(
            run_id,
            next_seq,
            "cancelled",
            "cancelled",
            cancelled_payload,
            created_at=created_at,
        )
        return run

    def requeue_run(self, run_id):
        with self._session() as session:
            run = session.scalar(
                select(ChatRun).where(ChatRun.id == run_id).with_for_update()
            )
            if run is None:
                raise RunNotFoundError(f"Unknown run '{run_id}'")
            if run.status != "queued":
                raise RunRequeueError("Only queued runs can be requeued.")
            if int(run.latest_event_seq or 0) > 0:
                raise RunRequeueError(
                    "Queued run already has durable events and cannot be blindly requeued."
                )
            run.worker_id = ""
            run.lease_expires_at = None
            run.cancel_requested = False
            run.error_message = ""
            session.commit()
            session.refresh(run)
            return run

    def complete_run_with_messages(
        self,
        run_id,
        *,
        worker_id,
        user_role,
        user_content,
        assistant_role,
        assistant_content,
        council_entries=None,
        done_payload=None,
    ):
        created_at = None
        with self._session() as session:
            run = self._load_run_for_update(
                session,
                run_id,
                worker_id=worker_id,
                require_active_owner=bool(worker_id),
            )

            if run.final_user_message_id and run.final_assistant_message_id:
                user_message = session.scalar(
                    select(ChatMessage).where(
                        ChatMessage.id == run.final_user_message_id
                    )
                )
                assistant_message = session.scalar(
                    select(ChatMessage).where(
                        ChatMessage.id == run.final_assistant_message_id
                    )
                )
                if run.status != "completed":
                    run.status = "completed"
                    run.finished_at = _utc_now()
                    run.lease_expires_at = None
                    run.worker_id = worker_id or run.worker_id
                    session.commit()
                return user_message, assistant_message

            chat_session = session.scalar(
                select(ChatSession)
                .where(ChatSession.id == run.chat_session_id)
                .with_for_update()
            )
            if chat_session is None:
                raise RunNotFoundError(
                    f"Chat session '{run.chat_session_id}' not found for run '{run_id}'"
                )

            user_message = ChatMessage(
                session_id=run.chat_session_id,
                role=user_role,
                content=user_content,
            )
            assistant_message = ChatMessage(
                session_id=run.chat_session_id,
                role=assistant_role,
                content=assistant_content,
                council_entries=council_entries or None,
            )
            session.add(user_message)
            session.add(assistant_message)
            session.flush()

            chat_session.updated_at = _utc_now()
            run.status = "completed"
            run.pause_state_json = None
            run.finished_at = _utc_now()
            run.lease_expires_at = None
            run.worker_id = worker_id or run.worker_id
            run.final_user_message_id = user_message.id
            run.final_assistant_message_id = assistant_message.id
            next_seq = int(run.latest_event_seq or 0) + 1
            run.latest_event_seq = next_seq
            payload = dict(done_payload or {})
            if not payload.get("user_message"):
                payload["user_message"] = {
                    "id": user_message.id,
                    "session_id": user_message.session_id,
                    "role": user_message.role,
                    "content": user_message.content,
                    "created_at": _iso(user_message.created_at),
                    "council_entries": None,
                }
            if not payload.get("assistant_message"):
                payload["assistant_message"] = {
                    "id": assistant_message.id,
                    "session_id": assistant_message.session_id,
                    "role": assistant_message.role,
                    "content": assistant_message.content,
                    "created_at": _iso(assistant_message.created_at),
                    "council_entries": assistant_message.council_entries or None,
                }
            event = ChatRunEvent(
                run_id=run_id,
                seq=next_seq,
                type="done",
                code="done",
                payload_json=payload or None,
            )
            session.add(event)
            session.flush()
            created_at = event.created_at
            session.commit()
            session.refresh(user_message)
            session.refresh(assistant_message)
            session.refresh(run)
        self._publish(
            run_id, next_seq, "done", "done", payload or None, created_at=created_at
        )
        return user_message, assistant_message

    def complete_run_without_messages(self, run_id, *, worker_id, done_payload=None):
        created_at = None
        with self._session() as session:
            run = self._load_run_for_update(
                session,
                run_id,
                worker_id=worker_id,
                require_active_owner=bool(worker_id),
            )
            run.status = "completed"
            run.pause_state_json = None
            run.finished_at = _utc_now()
            run.lease_expires_at = None
            run.worker_id = worker_id or run.worker_id
            next_seq = int(run.latest_event_seq or 0) + 1
            run.latest_event_seq = next_seq
            payload = dict(done_payload or {})
            event = ChatRunEvent(
                run_id=run_id,
                seq=next_seq,
                type="done",
                code="done",
                payload_json=payload or None,
            )
            session.add(event)
            session.flush()
            created_at = event.created_at
            session.commit()
            session.refresh(run)
        self._publish(
            run_id, next_seq, "done", "done", payload or None, created_at=created_at
        )
        return run

    def get_terminal_event(self, run_id):
        with self._session() as session:
            return session.scalar(
                select(ChatRunEvent)
                .where(
                    ChatRunEvent.run_id == run_id,
                    ChatRunEvent.type.in_(("done", "error", "cancelled")),
                )
                .order_by(ChatRunEvent.seq.desc())
                .limit(1)
            )
