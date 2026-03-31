from datetime import datetime, timedelta, timezone

from persistence.database import get_session_factory

try:
    from sqlalchemy import and_, func, or_, select
except ImportError:  # pragma: no cover - optional until SQLAlchemy is installed
    and_ = None
    func = None
    or_ = None
    select = None

from persistence.postgres_models import ChatMessage, ChatRun, ChatRunEvent, ChatSession


ACTIVE_RUN_STATUSES = {"queued", "running", "cancel_requested"}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}


def _utc_now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat() if value is not None else ""


class ActiveChatRunExistsError(RuntimeError):
    pass


class ActiveRunLimitExceededError(RuntimeError):
    pass


class RunNotFoundError(RuntimeError):
    pass


class RunRequeueError(RuntimeError):
    pass


class PostgresRunStore:
    def __init__(self, session_factory=None):
        if select is None or func is None or and_ is None or or_ is None:
            raise ImportError(
                "SQLAlchemy is required for PostgresRunStore. "
                "Install sqlalchemy and alembic in the AI-Linux-Assistant environment."
            )
        self.session_factory = session_factory or get_session_factory()

    def _session(self):
        return self.session_factory()

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
    ):
        with self._session() as session:
            existing = session.scalar(
                select(ChatRun).where(
                    ChatRun.chat_session_id == chat_session_id,
                    ChatRun.client_request_id == client_request_id,
                )
            )
            if existing is not None:
                return existing

            active_chat_run = session.scalar(
                select(ChatRun)
                .where(
                    ChatRun.chat_session_id == chat_session_id,
                    ChatRun.status.in_(ACTIVE_RUN_STATUSES),
                )
                .order_by(ChatRun.created_at.desc())
                .limit(1)
            )
            if active_chat_run is not None:
                raise ActiveChatRunExistsError(f"Chat '{chat_session_id}' already has an active run.")

            active_count = session.scalar(
                select(func.count())
                .select_from(ChatRun)
                .where(
                    ChatRun.user_id == user_id,
                    ChatRun.status.in_(ACTIVE_RUN_STATUSES),
                )
            ) or 0
            if int(active_count) >= max(1, int(max_active_runs_per_user)):
                raise ActiveRunLimitExceededError(f"User '{user_id}' exceeded the active run limit.")

            run = ChatRun(
                chat_session_id=chat_session_id,
                project_id=project_id,
                user_id=user_id,
                status="queued",
                request_content=request_content,
                magi=(magi or "off").strip() or "off",
                client_request_id=client_request_id,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            return run

    def get_run(self, run_id):
        with self._session() as session:
            return session.scalar(select(ChatRun).where(ChatRun.id == run_id))

    def get_active_run_for_chat(self, chat_session_id):
        with self._session() as session:
            return session.scalar(
                select(ChatRun)
                .where(
                    ChatRun.chat_session_id == chat_session_id,
                    ChatRun.status.in_(ACTIVE_RUN_STATUSES),
                )
                .order_by(ChatRun.created_at.desc())
                .limit(1)
            )

    def get_active_runs_for_chat_ids(self, chat_session_ids):
        chat_session_ids = [str(chat_id) for chat_id in chat_session_ids or [] if chat_id]
        if not chat_session_ids:
            return {}
        with self._session() as session:
            rows = list(
                session.scalars(
                    select(ChatRun)
                    .where(
                        ChatRun.chat_session_id.in_(chat_session_ids),
                        ChatRun.status.in_(ACTIVE_RUN_STATUSES),
                    )
                    .order_by(ChatRun.created_at.desc())
                )
            )
        results = {}
        for row in rows:
            results.setdefault(row.chat_session_id, row)
        return results

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
            run = session.scalar(
                select(ChatRun).where(ChatRun.id == run_id).with_for_update()
            )
            if run is None:
                raise RunNotFoundError(f"Unknown run '{run_id}'")

            next_seq = int(run.latest_event_seq or 0) + 1
            run.latest_event_seq = next_seq
            if event_type == "state":
                run.latest_state_code = code or ""
            elif event_type == "event" and code == "text_delta":
                delta = str((payload or {}).get("delta", "") or "")
                if delta:
                    run.partial_assistant_text = (run.partial_assistant_text or "") + delta
            elif event_type == "error":
                run.error_message = str((payload or {}).get("message", "") or "")

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
            return event

    def claim_next_run(self, worker_id, lease_seconds):
        now = _utc_now()
        with self._session() as session:
            candidate = session.scalar(
                select(ChatRun)
                .where(
                    or_(
                        ChatRun.status == "queued",
                        and_(
                            ChatRun.status.in_({"running", "cancel_requested"}),
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
            candidate.lease_expires_at = now + timedelta(seconds=max(1, int(lease_seconds)))
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
            run.lease_expires_at = _utc_now() + timedelta(seconds=max(1, int(lease_seconds)))
            session.commit()
            return True

    def request_cancel(self, run_id):
        with self._session() as session:
            run = session.scalar(select(ChatRun).where(ChatRun.id == run_id).with_for_update())
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

    def is_cancel_requested(self, run_id):
        with self._session() as session:
            run = session.scalar(
                select(ChatRun).where(ChatRun.id == run_id)
            )
            if run is None:
                raise RunNotFoundError(f"Unknown run '{run_id}'")
            return bool(run.cancel_requested)

    def mark_failed(self, run_id, *, worker_id="", error_message="", event_payload=None):
        with self._session() as session:
            run = session.scalar(select(ChatRun).where(ChatRun.id == run_id).with_for_update())
            if run is None:
                raise RunNotFoundError(f"Unknown run '{run_id}'")
            if run.status == "completed":
                session.commit()
                return run
            run.status = "failed"
            run.error_message = error_message or ""
            if worker_id:
                run.worker_id = worker_id
            run.finished_at = _utc_now()
            run.lease_expires_at = None
            next_seq = int(run.latest_event_seq or 0) + 1
            run.latest_event_seq = next_seq
            session.add(
                ChatRunEvent(
                    run_id=run_id,
                    seq=next_seq,
                    type="error",
                    code="error",
                    payload_json=event_payload or {"message": run.error_message or "Run failed."},
                )
            )
            session.commit()
            session.refresh(run)
            return run

    def mark_cancelled(self, run_id, *, worker_id="", error_message="Run cancelled.", event_payload=None):
        with self._session() as session:
            run = session.scalar(select(ChatRun).where(ChatRun.id == run_id).with_for_update())
            if run is None:
                raise RunNotFoundError(f"Unknown run '{run_id}'")
            if run.status == "completed":
                session.commit()
                return run
            run.status = "cancelled"
            run.cancel_requested = True
            run.error_message = error_message or ""
            if worker_id:
                run.worker_id = worker_id
            run.finished_at = _utc_now()
            run.lease_expires_at = None
            next_seq = int(run.latest_event_seq or 0) + 1
            run.latest_event_seq = next_seq
            session.add(
                ChatRunEvent(
                    run_id=run_id,
                    seq=next_seq,
                    type="cancelled",
                    code="cancelled",
                    payload_json=event_payload or {"message": run.error_message or "Run cancelled."},
                )
            )
            session.commit()
            session.refresh(run)
            return run

    def requeue_run(self, run_id):
        with self._session() as session:
            run = session.scalar(select(ChatRun).where(ChatRun.id == run_id).with_for_update())
            if run is None:
                raise RunNotFoundError(f"Unknown run '{run_id}'")
            if run.status != "queued":
                raise RunRequeueError("Only queued runs can be requeued.")
            if int(run.latest_event_seq or 0) > 0:
                raise RunRequeueError("Queued run already has durable events and cannot be blindly requeued.")
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
        with self._session() as session:
            run = session.scalar(select(ChatRun).where(ChatRun.id == run_id).with_for_update())
            if run is None:
                raise RunNotFoundError(f"Unknown run '{run_id}'")

            if run.final_user_message_id and run.final_assistant_message_id:
                user_message = session.scalar(select(ChatMessage).where(ChatMessage.id == run.final_user_message_id))
                assistant_message = session.scalar(select(ChatMessage).where(ChatMessage.id == run.final_assistant_message_id))
                if run.status != "completed":
                    run.status = "completed"
                    run.finished_at = _utc_now()
                    run.lease_expires_at = None
                    run.worker_id = worker_id or run.worker_id
                    session.commit()
                return user_message, assistant_message

            chat_session = session.scalar(
                select(ChatSession).where(ChatSession.id == run.chat_session_id).with_for_update()
            )
            if chat_session is None:
                raise RunNotFoundError(f"Chat session '{run.chat_session_id}' not found for run '{run_id}'")

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
            session.add(
                ChatRunEvent(
                    run_id=run_id,
                    seq=next_seq,
                    type="done",
                    code="done",
                    payload_json=payload or None,
                )
            )
            session.commit()
            session.refresh(user_message)
            session.refresh(assistant_message)
            session.refresh(run)
            return user_message, assistant_message
