from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .buffers import ScopedTextCheckpointBuffer, TextCheckpointBuffer
from .errors import JobCancelledError, JobExecutionError, JobOwnershipLostError, JobPausedError
from .redis import subscribe_wakeup


def checkpoint_flush_interval(redis_client):
    return 1.0 if redis_client is not None else 0.2


@dataclass(frozen=True)
class WorkerSettings:
    lease_seconds: int = 30
    poll_ms: int = 100
    concurrency: int = 1
    queue_name: str = ""


@dataclass
class JobExecutionResult:
    result_json: dict[str, Any] | None = None
    done_payload: dict[str, Any] | None = None


class JobExecutionContext:
    def __init__(
        self,
        *,
        job,
        worker_id,
        store,
        ownership_lost_event,
        text_buffer,
        scoped_text_buffer,
    ):
        self.job = job
        self.worker_id = worker_id
        self.store = store
        self._ownership_lost_event = ownership_lost_event
        self._text_buffer = text_buffer
        self._scoped_text_buffer = scoped_text_buffer

    def emit_state(self, code):
        self._ensure_owned()
        self.store.append_event_for_worker(self.job.id, self.worker_id, "state", code, None)

    def emit_event(self, code, payload=None):
        self._ensure_owned()
        self.store.append_event_for_worker(self.job.id, self.worker_id, "event", code, payload or None)

    def publish_live_event(self, code, payload=None):
        self.store.publish_ephemeral_event(self.job.id, code, payload or None)

    def push_output_delta(self, delta_text):
        self._text_buffer.push(delta_text)

    def flush_output(self):
        self._text_buffer.flush()

    def begin_scoped_output(self, scope):
        self._scoped_text_buffer.begin_stream(scope)

    def push_scoped_output_delta(self, scope, delta_text):
        self._scoped_text_buffer.push(scope, delta_text)

    def flush_scoped_output(self, scope):
        self._scoped_text_buffer.flush_stream(scope)

    def finish_scoped_output(self, scope):
        self._scoped_text_buffer.finish_stream(scope)

    def is_cancel_requested(self):
        return self.store.is_cancel_requested(self.job.id)

    def is_pause_requested(self):
        return self.store.is_pause_requested(self.job.id)

    def raise_if_cancel_requested(self):
        if self.is_cancel_requested():
            raise JobCancelledError("Job cancelled.")

    def _ensure_owned(self):
        if self._ownership_lost_event.is_set():
            raise JobOwnershipLostError(f"Worker '{self.worker_id}' no longer owns job '{self.job.id}'.")


class LeasedJobWorkerService:
    def __init__(self, *, store, settings=None, worker_id=None):
        self.store = store
        self.settings = settings or WorkerSettings()
        self.worker_id = worker_id or os.getenv("COMPUTE_PLANE_WORKER_ID", f"compute-worker-{uuid.uuid4().hex[:8]}")
        self._stop_event = threading.Event()

    def execute_job(self, job, context):
        raise NotImplementedError

    def after_complete(self, job, result):
        del job, result

    def _heartbeat_and_flush_loop(
        self,
        job_id,
        claimed_worker_id,
        stop_event,
        ownership_lost_event,
        text_buffer,
        scoped_text_buffer,
    ):
        heartbeat_interval = max(1.0, float(self.settings.lease_seconds) / 3.0)
        flush_interval = max(0.05, min(0.2, heartbeat_interval))
        last_heartbeat = time.monotonic()
        while not stop_event.wait(flush_interval):
            try:
                text_buffer.tick()
                scoped_text_buffer.tick()
            except JobOwnershipLostError:
                ownership_lost_event.set()
                break
            now = time.monotonic()
            if (now - last_heartbeat) < heartbeat_interval:
                continue
            if not self.store.heartbeat(job_id, claimed_worker_id, self.settings.lease_seconds):
                ownership_lost_event.set()
                break
            last_heartbeat = now

    def _publish_live_text(self, job_id, delta_text, window):
        if not delta_text:
            return
        self.store.publish_ephemeral_event(job_id, "text_delta", {"delta": delta_text, "window": int(window)})

    def _publish_live_scoped_event(self, job_id, payload, code):
        payload = payload or {}
        if code.endswith("_delta") and not payload.get("delta"):
            return
        self.store.publish_ephemeral_event(job_id, code, payload)

    def _build_context(self, job, claimed_worker_id, ownership_lost_event, text_buffer, scoped_text_buffer):
        return JobExecutionContext(
            job=job,
            worker_id=claimed_worker_id,
            store=self.store,
            ownership_lost_event=ownership_lost_event,
            text_buffer=text_buffer,
            scoped_text_buffer=scoped_text_buffer,
        )

    def _complete_job(self, job, claimed_worker_id, result):
        done_payload = dict((result.done_payload or {}))
        self.store.complete_job(
            job.id,
            worker_id=claimed_worker_id,
            result_json=result.result_json,
            done_payload=done_payload or None,
        )
        self.after_complete(job, result)

    def _cancel_job(self, job, claimed_worker_id, message="Job cancelled."):
        self.store.mark_cancelled(
            job.id,
            worker_id=claimed_worker_id,
            error_message=message,
            event_payload={"message": message},
        )

    def _fail_job(self, job, claimed_worker_id, message):
        self.store.mark_failed(
            job.id,
            worker_id=claimed_worker_id,
            error_message=message,
            event_payload={"message": message},
        )

    def _handle_claimed_job(self, job, claimed_worker_id):
        if job.status == "cancel_requested":
            self._cancel_job(job, claimed_worker_id)
            return
        resumable_job = bool(getattr(job, "checkpoint_state_json", None))
        if job.status == "running" and int(getattr(job, "latest_event_seq", 0) or 0) > 0 and not resumable_job:
            self._fail_job(job, claimed_worker_id, "Job lease expired before completion.")
            return

        ownership_lost_event = threading.Event()
        text_buffer = TextCheckpointBuffer(
            job_id=job.id,
            worker_id=claimed_worker_id,
            store=self.store,
            ownership_lost_event=ownership_lost_event,
            publish_live_fn=self._publish_live_text,
            flush_interval=checkpoint_flush_interval(self.store.redis_client),
        )
        scoped_text_buffer = ScopedTextCheckpointBuffer(
            job_id=job.id,
            worker_id=claimed_worker_id,
            store=self.store,
            ownership_lost_event=ownership_lost_event,
            publish_live_fn=self._publish_live_scoped_event,
            flush_interval=checkpoint_flush_interval(self.store.redis_client),
        )
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_and_flush_loop,
            args=(
                job.id,
                claimed_worker_id,
                heartbeat_stop,
                ownership_lost_event,
                text_buffer,
                scoped_text_buffer,
            ),
            daemon=True,
        )
        heartbeat_thread.start()
        context = self._build_context(job, claimed_worker_id, ownership_lost_event, text_buffer, scoped_text_buffer)
        try:
            result = self.execute_job(job, context)
            if self.store.is_cancel_requested(job.id):
                raise JobCancelledError("Job cancelled.")
            if result is None:
                result = JobExecutionResult()
            if not isinstance(result, JobExecutionResult):
                raise TypeError("execute_job() must return JobExecutionResult or None.")
            text_buffer.flush()
            scoped_text_buffer.flush()
            self._complete_job(job, claimed_worker_id, result)
        except JobOwnershipLostError:
            try:
                text_buffer.flush()
                scoped_text_buffer.flush()
            except JobOwnershipLostError:
                pass
        except JobCancelledError as exc:
            try:
                text_buffer.flush()
                scoped_text_buffer.flush()
            except JobOwnershipLostError:
                return
            self._cancel_job(job, claimed_worker_id, str(exc) or "Job cancelled.")
        except JobPausedError as exc:
            try:
                text_buffer.flush()
                scoped_text_buffer.flush()
            except JobOwnershipLostError:
                return
            self.store.mark_paused(
                job.id,
                worker_id=claimed_worker_id,
                checkpoint_state=getattr(exc, "checkpoint_state", None) or {},
                event_payload=getattr(exc, "payload", None) or {"message": "Job paused."},
            )
        except JobExecutionError as exc:
            try:
                text_buffer.flush()
                scoped_text_buffer.flush()
            except JobOwnershipLostError:
                return
            self._fail_job(job, claimed_worker_id, str(exc))
        except Exception as exc:
            try:
                text_buffer.flush()
                scoped_text_buffer.flush()
            except JobOwnershipLostError:
                return
            self._fail_job(job, claimed_worker_id, str(exc))
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)

    def _worker_loop(self, slot_index):
        claimed_worker_id = f"{self.worker_id}:{slot_index}"
        poll_seconds = max(0.05, float(self.settings.poll_ms) / 1000.0)
        wakeup_sub = None
        if self.store.redis_client is not None:
            try:
                wakeup_sub = subscribe_wakeup(self.store.redis_client)
            except Exception:
                wakeup_sub = None
        try:
            while not self._stop_event.is_set():
                job = self.store.claim_next_job(
                    claimed_worker_id,
                    self.settings.lease_seconds,
                    queue_name=self.settings.queue_name or None,
                )
                if job is None:
                    if wakeup_sub is not None:
                        try:
                            wakeup_sub.get_message(timeout=poll_seconds)
                        except Exception:
                            self._stop_event.wait(poll_seconds)
                    else:
                        self._stop_event.wait(poll_seconds)
                    continue
                self._handle_claimed_job(job, claimed_worker_id)
        finally:
            if wakeup_sub is not None:
                try:
                    wakeup_sub.unsubscribe()
                    wakeup_sub.close()
                except Exception:
                    pass

    def run(self):
        concurrency = max(1, int(self.settings.concurrency))
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(self._worker_loop, index) for index in range(concurrency)]
            try:
                for future in futures:
                    future.result()
            except KeyboardInterrupt:
                self.stop()
            finally:
                self.stop()

    def stop(self):
        self._stop_event.set()
