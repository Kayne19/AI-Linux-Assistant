from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, inspect, or_, select
from sqlalchemy.exc import IntegrityError

from .errors import (
    ActiveJobLimitExceededError,
    ActiveScopeJobExistsError,
    JobNotFoundError,
    JobOwnershipLostError,
    JobRequeueError,
    JobStateConflictError,
)
from .event_serializer import serialize_job_event
from .models import Base, ComputeJob, ComputeJobEvent, utc_now
from .redis import publish_event, publish_wakeup


ACTIVE_JOB_STATUSES = {"queued", "running", "cancel_requested", "pause_requested", "paused"}
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
DEFAULT_JOB_KIND = "job"


class ComputePlaneStore:
    def __init__(self, *, session_factory, redis_client=None):
        self.session_factory = session_factory
        self.redis_client = redis_client
        self._ensure_schema()

    def _get_bound_engine(self):
        bind = getattr(self.session_factory, "kw", {}).get("bind")
        if bind is not None:
            return bind
        with self.session_factory() as session:
            return session.get_bind()

    def _ensure_schema(self):
        engine = self._get_bound_engine()
        Base.metadata.create_all(
            bind=engine,
            tables=[ComputeJob.__table__, ComputeJobEvent.__table__],
            checkfirst=True,
        )
        if inspect is not None and hasattr(inspect(engine), "clear_cache"):
            inspect(engine).clear_cache()

    def _session(self):
        return self.session_factory()

    def _publish(self, job_id, seq, event_type, code, payload_json, created_at=None):
        if self.redis_client is None:
            return
        publish_event(
            self.redis_client,
            job_id,
            serialize_job_event(seq, event_type, code, payload_json, created_at=created_at),
        )

    def publish_ephemeral_event(self, job_id, code, payload=None, *, event_type="event"):
        self._publish(job_id, 0, event_type, code, payload or None)

    def _load_job_for_update(self, session, job_id, *, worker_id="", require_active_owner=False):
        if require_active_owner and worker_id:
            job = session.scalar(
                select(ComputeJob)
                .where(
                    ComputeJob.id == job_id,
                    ComputeJob.worker_id == worker_id,
                    ComputeJob.status.in_(ACTIVE_JOB_STATUSES),
                    ComputeJob.lease_expires_at.is_not(None),
                    ComputeJob.lease_expires_at > utc_now(),
                )
                .with_for_update()
            )
            if job is None:
                raise JobOwnershipLostError(f"Worker '{worker_id}' no longer owns job '{job_id}'.")
            return job

        job = session.scalar(select(ComputeJob).where(ComputeJob.id == job_id).with_for_update())
        if job is None:
            raise JobNotFoundError(f"Unknown job '{job_id}'.")
        return job

    def _active_scope_predicates(self):
        return [ComputeJob.status.in_(ACTIVE_JOB_STATUSES), ComputeJob.blocks_scope.is_(True)]

    def _active_owner_predicates(self):
        return [ComputeJob.status.in_(ACTIVE_JOB_STATUSES), ComputeJob.blocks_owner.is_(True)]

    def create_or_reuse_job(
        self,
        *,
        scope_key,
        owner_key,
        client_token,
        payload=None,
        queue_name="default",
        job_kind=DEFAULT_JOB_KIND,
        metadata=None,
        max_active_jobs_per_owner=3,
        blocks_scope=True,
        blocks_owner=True,
    ):
        normalized_queue = str(queue_name or "default").strip() or "default"
        normalized_kind = str(job_kind or DEFAULT_JOB_KIND).strip() or DEFAULT_JOB_KIND
        with self._session() as session:
            existing = session.scalar(
                select(ComputeJob).where(
                    ComputeJob.scope_key == scope_key,
                    ComputeJob.client_token == client_token,
                )
            )
            if existing is not None:
                return existing

            if blocks_scope:
                active_scope_job = session.scalar(
                    select(ComputeJob)
                    .where(
                        ComputeJob.scope_key == scope_key,
                        *self._active_scope_predicates(),
                    )
                    .order_by(ComputeJob.created_at.desc())
                    .limit(1)
                )
                if active_scope_job is not None:
                    raise ActiveScopeJobExistsError(f"Scope '{scope_key}' already has an active job.")

            if blocks_owner:
                active_count = session.scalar(
                    select(func.count())
                    .select_from(ComputeJob)
                    .where(
                        ComputeJob.owner_key == owner_key,
                        *self._active_owner_predicates(),
                    )
                ) or 0
                if int(active_count) >= max(1, int(max_active_jobs_per_owner)):
                    raise ActiveJobLimitExceededError(f"Owner '{owner_key}' exceeded the active job limit.")

            job = ComputeJob(
                scope_key=str(scope_key),
                owner_key=str(owner_key),
                queue_name=normalized_queue,
                status="queued",
                job_kind=normalized_kind,
                client_token=str(client_token),
                blocks_scope=bool(blocks_scope),
                blocks_owner=bool(blocks_owner),
                payload_json=dict(payload or {}) or None,
                metadata_json=dict(metadata or {}) or None,
            )
            session.add(job)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(ComputeJob).where(
                        ComputeJob.scope_key == scope_key,
                        ComputeJob.client_token == client_token,
                    )
                )
                if existing is not None:
                    return existing
                if blocks_scope:
                    active_scope_job = session.scalar(
                        select(ComputeJob)
                        .where(
                            ComputeJob.scope_key == scope_key,
                            *self._active_scope_predicates(),
                        )
                        .order_by(ComputeJob.created_at.desc())
                        .limit(1)
                    )
                    if active_scope_job is not None:
                        raise ActiveScopeJobExistsError(f"Scope '{scope_key}' already has an active job.")
                if blocks_owner:
                    active_count = session.scalar(
                        select(func.count())
                        .select_from(ComputeJob)
                        .where(
                            ComputeJob.owner_key == owner_key,
                            *self._active_owner_predicates(),
                        )
                    ) or 0
                    if int(active_count) >= max(1, int(max_active_jobs_per_owner)):
                        raise ActiveJobLimitExceededError(f"Owner '{owner_key}' exceeded the active job limit.")
                raise
            session.refresh(job)

        if self.redis_client is not None:
            publish_wakeup(self.redis_client)
        return job

    def get_job(self, job_id):
        with self._session() as session:
            return session.scalar(select(ComputeJob).where(ComputeJob.id == job_id))

    def get_active_job_for_scope(self, scope_key):
        with self._session() as session:
            return session.scalar(
                select(ComputeJob)
                .where(
                    ComputeJob.scope_key == scope_key,
                    *self._active_scope_predicates(),
                )
                .order_by(ComputeJob.created_at.desc())
                .limit(1)
            )

    def list_jobs_for_scope(self, scope_key, *, page=1, page_size=20, status=None):
        page = max(1, int(page))
        page_size = max(1, int(page_size))
        offset = (page - 1) * page_size
        normalized_status = (status or "").strip()
        with self._session() as session:
            filters = [ComputeJob.scope_key == scope_key]
            if normalized_status:
                filters.append(ComputeJob.status == normalized_status)
            total = session.scalar(select(func.count()).select_from(ComputeJob).where(*filters)) or 0
            jobs = list(
                session.scalars(
                    select(ComputeJob)
                    .where(*filters)
                    .order_by(ComputeJob.created_at.desc(), ComputeJob.id.desc())
                    .offset(offset)
                    .limit(page_size)
                )
            )
        return jobs, int(total)

    def list_events_after(self, job_id, *, after_seq=0, limit=200):
        with self._session() as session:
            stmt = (
                select(ComputeJobEvent)
                .where(
                    ComputeJobEvent.job_id == job_id,
                    ComputeJobEvent.seq > max(0, int(after_seq)),
                )
                .order_by(ComputeJobEvent.seq.asc())
                .limit(max(1, int(limit)))
            )
            return list(session.scalars(stmt))

    def append_event(self, job_id, event_type, code="", payload=None):
        with self._session() as session:
            job = self._load_job_for_update(session, job_id)
            event = self._append_event_row(session, job, event_type, code=code, payload=payload)
        self._publish(job_id, event.seq, event.type, event.code, event.payload_json, created_at=event.created_at)
        return event

    def append_event_for_worker(self, job_id, worker_id, event_type, code="", payload=None):
        with self._session() as session:
            job = self._load_job_for_update(
                session,
                job_id,
                worker_id=worker_id,
                require_active_owner=True,
            )
            event = self._append_event_row(session, job, event_type, code=code, payload=payload)
        self._publish(job_id, event.seq, event.type, event.code, event.payload_json, created_at=event.created_at)
        return event

    def _append_event_row(self, session, job, event_type, *, code="", payload=None):
        payload = payload or None
        next_seq = int(job.latest_event_seq or 0) + 1
        job.latest_event_seq = next_seq
        if event_type == "state":
            job.latest_state_code = code or ""
        elif event_type == "event" and code == "text_delta":
            delta = str((payload or {}).get("delta", "") or "")
            if delta:
                job.partial_output_text = (job.partial_output_text or "") + delta
        elif event_type == "error":
            job.error_message = str((payload or {}).get("message", "") or "")
        elif event_type == "paused":
            job.checkpoint_state_json = (payload or {}).get("checkpoint_state")
        event = ComputeJobEvent(
            job_id=job.id,
            seq=next_seq,
            type=event_type,
            code=code or "",
            payload_json=payload,
        )
        session.add(event)
        session.commit()
        session.refresh(event)
        return event

    def append_output_checkpoint(self, job_id, delta_chunk, window, *, code="text_checkpoint"):
        delta_chunk = str(delta_chunk or "")
        if not delta_chunk:
            return None
        with self._session() as session:
            job = self._load_job_for_update(session, job_id)
            event = self._append_checkpoint_event(
                session,
                job,
                code=code,
                payload={"text": (job.partial_output_text or "") + delta_chunk, "window": int(window)},
                partial_output_delta=delta_chunk,
            )
        self._publish(job_id, event.seq, event.type, event.code, event.payload_json, created_at=event.created_at)
        return event

    def append_output_checkpoint_for_worker(self, job_id, worker_id, delta_chunk, window, *, code="text_checkpoint"):
        delta_chunk = str(delta_chunk or "")
        if not delta_chunk:
            return None
        with self._session() as session:
            job = self._load_job_for_update(
                session,
                job_id,
                worker_id=worker_id,
                require_active_owner=True,
            )
            event = self._append_checkpoint_event(
                session,
                job,
                code=code,
                payload={"text": (job.partial_output_text or "") + delta_chunk, "window": int(window)},
                partial_output_delta=delta_chunk,
            )
        self._publish(job_id, event.seq, event.type, event.code, event.payload_json, created_at=event.created_at)
        return event

    def append_scoped_output_checkpoint(self, job_id, scope, text, window, *, code="scoped_text_checkpoint"):
        text = str(text or "")
        if not text:
            return None
        with self._session() as session:
            job = self._load_job_for_update(session, job_id)
            event = self._append_checkpoint_event(
                session,
                job,
                code=code,
                payload={**dict(scope or {}), "text": text, "window": int(window)},
            )
        self._publish(job_id, event.seq, event.type, event.code, event.payload_json, created_at=event.created_at)
        return event

    def append_scoped_output_checkpoint_for_worker(
        self,
        job_id,
        worker_id,
        scope,
        text,
        window,
        *,
        code="scoped_text_checkpoint",
    ):
        text = str(text or "")
        if not text:
            return None
        with self._session() as session:
            job = self._load_job_for_update(
                session,
                job_id,
                worker_id=worker_id,
                require_active_owner=True,
            )
            event = self._append_checkpoint_event(
                session,
                job,
                code=code,
                payload={**dict(scope or {}), "text": text, "window": int(window)},
            )
        self._publish(job_id, event.seq, event.type, event.code, event.payload_json, created_at=event.created_at)
        return event

    def _append_checkpoint_event(self, session, job, *, code, payload, partial_output_delta=""):
        partial_output_delta = str(partial_output_delta or "")
        if partial_output_delta:
            job.partial_output_text = (job.partial_output_text or "") + partial_output_delta
        next_seq = int(job.latest_event_seq or 0) + 1
        job.latest_event_seq = next_seq
        event = ComputeJobEvent(
            job_id=job.id,
            seq=next_seq,
            type="event",
            code=code,
            payload_json=payload,
        )
        session.add(event)
        session.commit()
        session.refresh(event)
        return event

    def claim_next_job(self, worker_id, lease_seconds, *, queue_name=None):
        now = utc_now()
        with self._session() as session:
            filters = [
                or_(
                    ComputeJob.status == "queued",
                    and_(
                        ComputeJob.status.in_({"running", "cancel_requested", "pause_requested"}),
                        ComputeJob.lease_expires_at.is_not(None),
                        ComputeJob.lease_expires_at <= now,
                    ),
                )
            ]
            normalized_queue = (queue_name or "").strip()
            if normalized_queue:
                filters.append(ComputeJob.queue_name == normalized_queue)
            candidate = session.scalar(
                select(ComputeJob)
                .where(*filters)
                .order_by(ComputeJob.created_at.asc())
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

    def heartbeat(self, job_id, worker_id, lease_seconds):
        with self._session() as session:
            job = session.scalar(
                select(ComputeJob)
                .where(
                    ComputeJob.id == job_id,
                    ComputeJob.worker_id == worker_id,
                    ComputeJob.status.in_(ACTIVE_JOB_STATUSES),
                )
                .with_for_update()
            )
            if job is None:
                return False
            job.lease_expires_at = utc_now() + timedelta(seconds=max(1, int(lease_seconds)))
            session.commit()
            return True

    def cancel_queued_job(self, job_id, *, error_message="Job cancelled.", event_payload=None):
        created_at = None
        with self._session() as session:
            job = self._load_job_for_update(session, job_id)
            if job.status in TERMINAL_JOB_STATUSES:
                session.commit()
                return job
            if job.status != "queued":
                raise JobStateConflictError(
                    f"Job '{job_id}' is '{job.status}' and cannot be terminalized as a queued cancellation."
                )
            job.status = "cancelled"
            job.checkpoint_state_json = None
            job.cancel_requested = True
            job.error_message = error_message or ""
            job.finished_at = utc_now()
            job.lease_expires_at = None
            job.worker_id = ""
            next_seq = int(job.latest_event_seq or 0) + 1
            job.latest_event_seq = next_seq
            payload = event_payload or {"message": job.error_message or "Job cancelled."}
            event = ComputeJobEvent(
                job_id=job_id,
                seq=next_seq,
                type="cancelled",
                code="cancelled",
                payload_json=payload,
            )
            session.add(event)
            session.flush()
            created_at = event.created_at
            session.commit()
            session.refresh(job)
        self._publish(job_id, next_seq, "cancelled", "cancelled", payload, created_at=created_at)
        return job

    def request_running_cancel(self, job_id):
        with self._session() as session:
            job = self._load_job_for_update(session, job_id)
            if job.status in TERMINAL_JOB_STATUSES:
                session.commit()
                return job
            if job.status not in {"running", "cancel_requested", "pause_requested"}:
                raise JobStateConflictError(
                    f"Job '{job_id}' is '{job.status}' and cannot be marked cancel_requested for worker handling."
                )
            job.cancel_requested = True
            job.status = "cancel_requested"
            session.commit()
            session.refresh(job)
            return job

    def request_cancel(self, job_id):
        with self._session() as session:
            job = self._load_job_for_update(session, job_id)
            status = job.status
        if status == "queued":
            return self.cancel_queued_job(job_id)
        if status in {"running", "cancel_requested", "pause_requested"}:
            return self.request_running_cancel(job_id)
        if status == "paused":
            return self.mark_cancelled(job_id, error_message="Job cancelled.")
        return self.get_job(job_id)

    def is_cancel_requested(self, job_id):
        with self._session() as session:
            job = session.scalar(select(ComputeJob).where(ComputeJob.id == job_id))
            if job is None:
                raise JobNotFoundError(f"Unknown job '{job_id}'.")
            return bool(job.cancel_requested)

    def request_pause(self, job_id, *, payload=None):
        created_at = None
        with self._session() as session:
            job = self._load_job_for_update(session, job_id)
            if job.status in TERMINAL_JOB_STATUSES:
                session.commit()
                return job
            if job.status in {"pause_requested", "paused"}:
                session.commit()
                session.refresh(job)
                return job
            if job.status != "running":
                raise JobStateConflictError(f"Job '{job_id}' is '{job.status}' and cannot be paused.")
            job.status = "pause_requested"
            next_seq = int(job.latest_event_seq or 0) + 1
            job.latest_event_seq = next_seq
            event_payload = payload or {"message": "Pause requested."}
            event = ComputeJobEvent(
                job_id=job_id,
                seq=next_seq,
                type="event",
                code="pause_requested",
                payload_json=event_payload,
            )
            session.add(event)
            session.flush()
            created_at = event.created_at
            session.commit()
            session.refresh(job)
        self._publish(job_id, next_seq, "event", "pause_requested", event_payload, created_at=created_at)
        return job

    def is_pause_requested(self, job_id):
        with self._session() as session:
            job = session.scalar(select(ComputeJob).where(ComputeJob.id == job_id))
            if job is None:
                raise JobNotFoundError(f"Unknown job '{job_id}'.")
            return (job.status or "") == "pause_requested"

    def mark_paused(self, job_id, *, worker_id, checkpoint_state=None, event_payload=None):
        created_at = None
        with self._session() as session:
            job = self._load_job_for_update(
                session,
                job_id,
                worker_id=worker_id,
                require_active_owner=bool(worker_id),
            )
            if job.status == "completed":
                session.commit()
                return job
            job.status = "paused"
            job.checkpoint_state_json = dict(checkpoint_state or {}) or None
            job.lease_expires_at = None
            job.worker_id = ""
            next_seq = int(job.latest_event_seq or 0) + 1
            job.latest_event_seq = next_seq
            payload = dict(event_payload or {})
            if job.checkpoint_state_json and "checkpoint_state" not in payload:
                payload["checkpoint_state"] = job.checkpoint_state_json
            if "message" not in payload:
                payload["message"] = "Job paused."
            event = ComputeJobEvent(
                job_id=job_id,
                seq=next_seq,
                type="paused",
                code="paused",
                payload_json=payload,
            )
            session.add(event)
            session.flush()
            created_at = event.created_at
            session.commit()
            session.refresh(job)
        self._publish(job_id, next_seq, "paused", "paused", payload, created_at=created_at)
        return job

    def resume_paused_job(self, job_id, *, checkpoint_state_patch=None, resume_payload=None):
        publish_rows = []
        with self._session() as session:
            job = self._load_job_for_update(session, job_id)
            if job.status != "paused":
                raise JobStateConflictError(f"Job '{job_id}' is '{job.status}' and cannot be resumed.")
            checkpoint_state = dict(job.checkpoint_state_json or {})
            if checkpoint_state_patch:
                checkpoint_state.update(dict(checkpoint_state_patch))
                next_seq = int(job.latest_event_seq or 0) + 1
                job.latest_event_seq = next_seq
                patch_payload = {"checkpoint_state_patch": dict(checkpoint_state_patch)}
                event = ComputeJobEvent(
                    job_id=job_id,
                    seq=next_seq,
                    type="event",
                    code="checkpoint_state_updated",
                    payload_json=patch_payload,
                )
                session.add(event)
                session.flush()
                publish_rows.append((next_seq, "event", "checkpoint_state_updated", patch_payload, event.created_at))
            job.checkpoint_state_json = checkpoint_state or None
            job.status = "queued"
            job.worker_id = ""
            job.lease_expires_at = None
            next_seq = int(job.latest_event_seq or 0) + 1
            job.latest_event_seq = next_seq
            payload = dict(resume_payload or {"message": "Job resumed."})
            event = ComputeJobEvent(
                job_id=job_id,
                seq=next_seq,
                type="event",
                code="resumed",
                payload_json=payload,
            )
            session.add(event)
            session.flush()
            publish_rows.append((next_seq, "event", "resumed", payload, event.created_at))
            session.commit()
            session.refresh(job)
        for next_seq, event_type, code, payload, created_at in publish_rows:
            self._publish(job_id, next_seq, event_type, code, payload, created_at=created_at)
        if self.redis_client is not None:
            publish_wakeup(self.redis_client)
        return job

    def mark_failed(self, job_id, *, worker_id="", error_message="", event_payload=None):
        created_at = None
        with self._session() as session:
            job = self._load_job_for_update(
                session,
                job_id,
                worker_id=worker_id,
                require_active_owner=bool(worker_id),
            )
            if job.status == "completed":
                session.commit()
                return job
            job.status = "failed"
            job.checkpoint_state_json = None
            job.error_message = error_message or ""
            if worker_id:
                job.worker_id = worker_id
            job.finished_at = utc_now()
            job.lease_expires_at = None
            next_seq = int(job.latest_event_seq or 0) + 1
            job.latest_event_seq = next_seq
            payload = event_payload or {"message": job.error_message or "Job failed."}
            event = ComputeJobEvent(
                job_id=job_id,
                seq=next_seq,
                type="error",
                code="error",
                payload_json=payload,
            )
            session.add(event)
            session.flush()
            created_at = event.created_at
            session.commit()
            session.refresh(job)
        self._publish(job_id, next_seq, "error", "error", payload, created_at=created_at)
        return job

    def mark_cancelled(self, job_id, *, worker_id="", error_message="Job cancelled.", event_payload=None):
        created_at = None
        with self._session() as session:
            job = self._load_job_for_update(
                session,
                job_id,
                worker_id=worker_id,
                require_active_owner=bool(worker_id),
            )
            if job.status == "completed":
                session.commit()
                return job
            job.status = "cancelled"
            job.checkpoint_state_json = None
            job.cancel_requested = True
            job.error_message = error_message or ""
            if worker_id:
                job.worker_id = worker_id
            job.finished_at = utc_now()
            job.lease_expires_at = None
            next_seq = int(job.latest_event_seq or 0) + 1
            job.latest_event_seq = next_seq
            payload = event_payload or {"message": job.error_message or "Job cancelled."}
            event = ComputeJobEvent(
                job_id=job_id,
                seq=next_seq,
                type="cancelled",
                code="cancelled",
                payload_json=payload,
            )
            session.add(event)
            session.flush()
            created_at = event.created_at
            session.commit()
            session.refresh(job)
        self._publish(job_id, next_seq, "cancelled", "cancelled", payload, created_at=created_at)
        return job

    def requeue_job(self, job_id):
        with self._session() as session:
            job = session.scalar(select(ComputeJob).where(ComputeJob.id == job_id).with_for_update())
            if job is None:
                raise JobNotFoundError(f"Unknown job '{job_id}'.")
            if job.status != "queued":
                raise JobRequeueError("Only queued jobs can be requeued.")
            if int(job.latest_event_seq or 0) > 0:
                raise JobRequeueError("Queued job already has durable events and cannot be blindly requeued.")
            job.worker_id = ""
            job.lease_expires_at = None
            job.cancel_requested = False
            job.error_message = ""
            session.commit()
            session.refresh(job)
        if self.redis_client is not None:
            publish_wakeup(self.redis_client)
        return job

    def complete_job(self, job_id, *, worker_id, result_json=None, done_payload=None):
        created_at = None
        with self._session() as session:
            job = self._load_job_for_update(
                session,
                job_id,
                worker_id=worker_id,
                require_active_owner=bool(worker_id),
            )
            if job.status == "completed":
                session.commit()
                return job
            job.status = "completed"
            job.checkpoint_state_json = None
            job.result_json = dict(result_json or {}) or None
            job.finished_at = utc_now()
            job.lease_expires_at = None
            job.worker_id = worker_id or job.worker_id
            next_seq = int(job.latest_event_seq or 0) + 1
            job.latest_event_seq = next_seq
            payload = dict(done_payload or {})
            if job.result_json is not None and "result" not in payload:
                payload["result"] = job.result_json
            event = ComputeJobEvent(
                job_id=job_id,
                seq=next_seq,
                type="done",
                code="done",
                payload_json=payload or None,
            )
            session.add(event)
            session.flush()
            created_at = event.created_at
            session.commit()
            session.refresh(job)
        self._publish(job_id, next_seq, "done", "done", payload or None, created_at=created_at)
        return job

    def get_terminal_event(self, job_id):
        with self._session() as session:
            return session.scalar(
                select(ComputeJobEvent)
                .where(
                    ComputeJobEvent.job_id == job_id,
                    ComputeJobEvent.type.in_(("done", "error", "cancelled")),
                )
                .order_by(ComputeJobEvent.seq.desc())
                .limit(1)
            )
