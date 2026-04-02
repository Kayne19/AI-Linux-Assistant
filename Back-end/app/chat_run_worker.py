import os
import signal
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from config.settings import SETTINGS
from orchestration.model_router import ModelRouter, RouterExecutionError
from orchestration.run_control import RunCancelledError
from persistence.postgres_app_store import PostgresAppStore
from persistence.postgres_memory_store import PostgresMemoryStore
from persistence.postgres_run_store import (
    AUTO_NAME_RUN_KIND,
    MESSAGE_RUN_KIND,
    PostgresRunStore,
    RunOwnershipLostError,
)
from retrieval.factory import build_runtime_components
from retrieval.vectorDB import VectorDB
from streaming.redis_events import get_shared_client as _get_redis_client


class _DeltaBuffer:
    """Fan out live text immediately and durably checkpoint it in batches."""

    def __init__(
        self,
        run_id,
        worker_id,
        run_store,
        redis_publish_fn,
        ownership_lost_event,
        flush_interval=0.2,
        flush_bytes=200,
    ):
        self._run_id = run_id
        self._worker_id = worker_id
        self._run_store = run_store
        self._redis_publish = redis_publish_fn
        self._ownership_lost_event = ownership_lost_event
        self._flush_interval = max(0.01, float(flush_interval))
        self._flush_bytes = max(1, int(flush_bytes))
        self._lock = threading.Lock()
        self._buffer = ""
        self._window = 0
        self._last_flush = time.monotonic()

    def push(self, delta_text):
        self._raise_if_ownership_lost()
        delta_text = str(delta_text or "")
        if not delta_text:
            return
        self._redis_publish(self._run_id, delta_text, self._window)
        chunk, window = self._maybe_take_flush_chunk(delta_text)
        if chunk:
            self._run_store.append_text_checkpoint_for_worker(
                self._run_id,
                self._worker_id,
                chunk,
                window,
            )

    def tick(self):
        self._raise_if_ownership_lost()
        chunk, window = self._take_flush_chunk(force=False)
        if chunk:
            self._run_store.append_text_checkpoint_for_worker(
                self._run_id,
                self._worker_id,
                chunk,
                window,
            )

    def flush(self):
        self._raise_if_ownership_lost()
        chunk, window = self._take_flush_chunk(force=True)
        if chunk:
            self._run_store.append_text_checkpoint_for_worker(
                self._run_id,
                self._worker_id,
                chunk,
                window,
            )

    def _raise_if_ownership_lost(self):
        if self._ownership_lost_event.is_set():
            raise RunOwnershipLostError(f"Worker '{self._worker_id}' no longer owns run '{self._run_id}'.")

    def _maybe_take_flush_chunk(self, delta_text):
        now = time.monotonic()
        with self._lock:
            self._buffer += delta_text
            if (
                len(self._buffer) < self._flush_bytes
                and (now - self._last_flush) < self._flush_interval
            ):
                return None, None
            return self._drain_locked(now)

    def _take_flush_chunk(self, force):
        now = time.monotonic()
        with self._lock:
            if not self._buffer:
                return None, None
            if not force and (now - self._last_flush) < self._flush_interval:
                return None, None
            return self._drain_locked(now)

    def _drain_locked(self, now):
        chunk = self._buffer
        window = self._window
        self._buffer = ""
        self._window += 1
        self._last_flush = now
        return chunk, window


def _magi_entry_key(role, phase, round_number=None):
    try:
        normalized_round = 0 if round_number is None else int(round_number)
    except (TypeError, ValueError):
        normalized_round = 0
    return f"{phase}:{role}:{normalized_round}"


class _MagiRoleDeltaBuffer:
    """Fan out live MAGI role text immediately and durably checkpoint absolute entry text."""

    def __init__(
        self,
        run_id,
        worker_id,
        run_store,
        redis_publish_fn,
        ownership_lost_event,
        flush_interval=0.2,
        flush_bytes=200,
    ):
        self._run_id = run_id
        self._worker_id = worker_id
        self._run_store = run_store
        self._redis_publish = redis_publish_fn
        self._ownership_lost_event = ownership_lost_event
        self._flush_interval = max(0.01, float(flush_interval))
        self._flush_bytes = max(1, int(flush_bytes))
        self._lock = threading.Lock()
        self._states = {}

    def begin_entry(self, payload):
        context = self._normalize_context(payload)
        key = _magi_entry_key(context["role"], context["phase"], context["round"])
        with self._lock:
            self._states[key] = {
                **context,
                "buffer": "",
                "text": "",
                "window": 0,
                "last_flush": time.monotonic(),
            }

    def push(self, payload):
        self._raise_if_ownership_lost()
        context = self._normalize_context(payload)
        delta_text = str((payload or {}).get("delta", "") or "")
        if not delta_text:
            return
        state = self._ensure_state(context)
        self._redis_publish(
            self._run_id,
            {
                **context,
                "delta": delta_text,
                "window": int(state["window"]),
            },
            "magi_role_text_delta",
        )
        checkpoint = self._maybe_take_flush_chunk(state, delta_text)
        if checkpoint is not None:
            self._append_checkpoint(checkpoint)

    def flush_entry(self, payload):
        self._raise_if_ownership_lost()
        context = self._normalize_context(payload)
        state = self._get_state(context)
        if state is None:
            return
        checkpoint = self._take_flush_chunk(state, force=True)
        if checkpoint is not None:
            self._append_checkpoint(checkpoint)

    def finish_entry(self, payload):
        context = self._normalize_context(payload)
        key = _magi_entry_key(context["role"], context["phase"], context["round"])
        with self._lock:
            self._states.pop(key, None)

    def tick(self):
        self._raise_if_ownership_lost()
        checkpoints = []
        with self._lock:
            for state in self._states.values():
                checkpoint = self._take_flush_chunk_locked(state, force=False, now=time.monotonic())
                if checkpoint is not None:
                    checkpoints.append(checkpoint)
        for checkpoint in checkpoints:
            self._append_checkpoint(checkpoint)

    def flush(self):
        self._raise_if_ownership_lost()
        checkpoints = []
        with self._lock:
            for state in self._states.values():
                checkpoint = self._take_flush_chunk_locked(state, force=True, now=time.monotonic())
                if checkpoint is not None:
                    checkpoints.append(checkpoint)
        for checkpoint in checkpoints:
            self._append_checkpoint(checkpoint)

    def _raise_if_ownership_lost(self):
        if self._ownership_lost_event.is_set():
            raise RunOwnershipLostError(f"Worker '{self._worker_id}' no longer owns run '{self._run_id}'.")

    def _normalize_context(self, payload):
        payload = payload or {}
        round_number = payload.get("round")
        try:
            round_number = None if round_number is None else int(round_number)
        except (TypeError, ValueError):
            round_number = None
        return {
            "role": str(payload.get("role") or ""),
            "phase": str(payload.get("phase") or ""),
            "round": round_number,
        }

    def _get_state(self, context):
        key = _magi_entry_key(context["role"], context["phase"], context["round"])
        with self._lock:
            return self._states.get(key)

    def _ensure_state(self, context):
        key = _magi_entry_key(context["role"], context["phase"], context["round"])
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = {
                    **context,
                    "buffer": "",
                    "text": "",
                    "window": 0,
                    "last_flush": time.monotonic(),
                }
                self._states[key] = state
            return state

    def _maybe_take_flush_chunk(self, state, delta_text):
        now = time.monotonic()
        with self._lock:
            state["buffer"] += delta_text
            return self._take_flush_chunk_locked(state, force=False, now=now)

    def _take_flush_chunk(self, state, force):
        with self._lock:
            return self._take_flush_chunk_locked(state, force=force, now=time.monotonic())

    def _take_flush_chunk_locked(self, state, force, now):
        if not state["buffer"]:
            return None
        if not force and len(state["buffer"]) < self._flush_bytes and (now - state["last_flush"]) < self._flush_interval:
            return None
        state["text"] += state["buffer"]
        checkpoint = {
            "role": state["role"],
            "phase": state["phase"],
            "round": state["round"],
            "text": state["text"],
            "window": int(state["window"]),
        }
        state["buffer"] = ""
        state["window"] += 1
        state["last_flush"] = now
        return checkpoint

    def _append_checkpoint(self, checkpoint):
        self._run_store.append_magi_role_text_checkpoint_for_worker(
            self._run_id,
            self._worker_id,
            checkpoint["role"],
            checkpoint["phase"],
            checkpoint["round"],
            checkpoint["text"],
            checkpoint["window"],
        )


def _checkpoint_flush_interval(redis_client):
    # Redis live fanout can carry smooth token pacing, so durable checkpoints can be coarse.
    # Without Redis, SSE falls back to Postgres polling and needs more frequent checkpoints.
    return 1.0 if redis_client is not None else 0.2


def _iso(value):
    return value.isoformat() if value is not None else ""


def _serialize_message(message):
    return {
        "id": message.id,
        "session_id": message.session_id,
        "role": message.role,
        "content": message.content,
        "created_at": _iso(getattr(message, "created_at", None)),
        "council_entries": getattr(message, "council_entries", None) or None,
    }


def _extract_sources(retrieved_docs):
    sources = []
    for line in (retrieved_docs or "").splitlines():
        if line.startswith("[Source:"):
            sources.append(line.strip())
    return sources


class ChatRunWorkerService:
    def __init__(self, worker_id=None, settings=None, run_store=None):
        self.settings = settings or SETTINGS
        self.worker_id = worker_id or os.getenv("CHAT_RUN_WORKER_ID", f"chat-worker-{uuid.uuid4().hex[:8]}")
        self.run_store = run_store or PostgresRunStore(redis_client=_get_redis_client())
        self.app_store = PostgresAppStore()
        self._stop_event = threading.Event()
        self._shared_retrieval_components = build_runtime_components()

    def _build_router(self, run):
        return ModelRouter(
            database=VectorDB(runtime_components=self._shared_retrieval_components),
            memory_store=PostgresMemoryStore(project_id=run.project_id),
            chat_store=self.app_store,
            chat_session_id=run.chat_session_id,
            cancel_check=lambda checkpoint: self.run_store.is_cancel_requested(run.id),
            persist_turn_messages=False,
        )

    def _heartbeat_and_flush_loop(
        self,
        run_id,
        claimed_worker_id,
        stop_event,
        ownership_lost_event,
        delta_buffer,
        magi_role_delta_buffer,
    ):
        heartbeat_interval = max(1.0, float(self.settings.chat_run_lease_seconds) / 3.0)
        flush_interval = max(0.05, min(0.2, heartbeat_interval))
        last_heartbeat = time.monotonic()
        while not stop_event.wait(flush_interval):
            try:
                delta_buffer.tick()
                magi_role_delta_buffer.tick()
            except RunOwnershipLostError:
                ownership_lost_event.set()
                break
            now = time.monotonic()
            if (now - last_heartbeat) < heartbeat_interval:
                continue
            if not self.run_store.heartbeat(run_id, claimed_worker_id, self.settings.chat_run_lease_seconds):
                ownership_lost_event.set()
                break
            last_heartbeat = now

    def _emit_state(self, run_id, claimed_worker_id, state, _turn):
        self.run_store.append_event_for_worker(run_id, claimed_worker_id, "state", state.name, None)

    def _emit_event(self, run_id, claimed_worker_id, event_type, payload):
        self.run_store.append_event_for_worker(run_id, claimed_worker_id, "event", event_type, payload or None)

    def _publish_live_event(self, run_id, payload, code):
        payload = payload or {}
        if code == "text_delta" and not payload.get("delta"):
            return
        if code == "magi_role_text_delta" and not payload.get("delta"):
            return
        self.run_store._publish(
            run_id,
            0,
            "event",
            code,
            payload,
        )

    def _complete_run(self, run, claimed_worker_id, turn):
        done_payload = {
            "user_message": None,
            "assistant_message": None,
            "debug": {
                "state_trace": list(getattr(turn, "state_trace", []) or []),
                "tool_events": list(getattr(turn, "tool_events", []) or []),
                "retrieval_query": getattr(turn, "retrieval_query", "") or "",
                "retrieved_sources": _extract_sources(getattr(turn, "retrieved_docs", "") or ""),
                "auto_name_scheduled": bool(getattr(turn, "schedule_auto_name", False)),
            },
        }
        user_message, assistant_message = self.run_store.complete_run_with_messages(
            run.id,
            worker_id=claimed_worker_id,
            user_role="user",
            user_content=run.request_content,
            assistant_role="model",
            assistant_content=turn.response,
            council_entries=turn.council_entries or None,
            done_payload=done_payload,
        )
        done_payload["user_message"] = _serialize_message(user_message)
        done_payload["assistant_message"] = _serialize_message(assistant_message)

    def _complete_background_run(self, run, claimed_worker_id, turn):
        self.run_store.complete_run_without_messages(
            run.id,
            worker_id=claimed_worker_id,
            done_payload={
                "debug": {
                    "state_trace": list(getattr(turn, "state_trace", []) or []),
                    "tool_events": list(getattr(turn, "tool_events", []) or []),
                    "retrieval_query": "",
                    "retrieved_sources": [],
                    "generated_chat_title": getattr(turn, "generated_chat_title", "") or "",
                }
            },
        )

    def _queue_auto_name_run(self, run, turn):
        if not getattr(turn, "schedule_auto_name", False):
            return None
        if not run.chat_session_id:
            return None
        try:
            chat_session = self.app_store.get_chat_session(run.chat_session_id)
            if chat_session is None or (getattr(chat_session, "title", "") or "").strip():
                return None
            return self.run_store.create_or_reuse_run(
                chat_session_id=run.chat_session_id,
                project_id=run.project_id,
                user_id=run.user_id,
                request_content=f"Auto-name follow-up for run {run.id}",
                magi="off",
                client_request_id=f"auto-name:{run.id}",
                max_active_runs_per_user=self.settings.max_active_runs_per_user_default,
                run_kind=AUTO_NAME_RUN_KIND,
            )
        except Exception:
            return None

    def _cancel_run(self, run, claimed_worker_id, message="Run cancelled."):
        self.run_store.mark_cancelled(
            run.id,
            worker_id=claimed_worker_id,
            error_message=message,
            event_payload={"message": message},
        )

    def _fail_run(self, run, claimed_worker_id, message):
        self.run_store.mark_failed(
            run.id,
            worker_id=claimed_worker_id,
            error_message=message,
            event_payload={"message": message},
        )

    def _handle_claimed_run(self, run, claimed_worker_id):
        if run.status == "cancel_requested":
            self._cancel_run(run, claimed_worker_id)
            return
        if run.status == "running" and int(getattr(run, "latest_event_seq", 0) or 0) > 0:
            self._fail_run(run, claimed_worker_id, "Run lease expired before completion.")
            return

        ownership_lost_event = threading.Event()
        delta_buffer = _DeltaBuffer(
            run_id=run.id,
            worker_id=claimed_worker_id,
            run_store=self.run_store,
            redis_publish_fn=lambda run_id, delta_text, window: self._publish_live_event(
                run_id,
                {"delta": delta_text, "window": int(window)},
                "text_delta",
            ),
            ownership_lost_event=ownership_lost_event,
            flush_interval=_checkpoint_flush_interval(getattr(self.run_store, "_redis_client", None)),
        )
        magi_role_delta_buffer = _MagiRoleDeltaBuffer(
            run_id=run.id,
            worker_id=claimed_worker_id,
            run_store=self.run_store,
            redis_publish_fn=lambda run_id, payload, code: self._publish_live_event(run_id, payload, code),
            ownership_lost_event=ownership_lost_event,
            flush_interval=_checkpoint_flush_interval(getattr(self.run_store, "_redis_client", None)),
        )
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_and_flush_loop,
            args=(
                run.id,
                claimed_worker_id,
                heartbeat_stop,
                ownership_lost_event,
                delta_buffer,
                magi_role_delta_buffer,
            ),
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            router = self._build_router(run)
            router.set_state_listener(
                lambda state, turn: self._emit_state(run.id, claimed_worker_id, state, turn)
            )

            def _event_listener(event_type, payload):
                if event_type == "text_delta":
                    delta_buffer.push((payload or {}).get("delta", ""))
                    return
                if event_type == "magi_role_start":
                    magi_role_delta_buffer.begin_entry(payload)
                if event_type == "magi_role_text_delta":
                    magi_role_delta_buffer.push(payload)
                    return
                if event_type == "magi_role_complete":
                    magi_role_delta_buffer.flush_entry(payload)
                self._emit_event(run.id, claimed_worker_id, event_type, payload)
                if event_type == "magi_role_complete":
                    magi_role_delta_buffer.finish_entry(payload)

            router.set_event_listener(_event_listener)

            if (getattr(run, "run_kind", MESSAGE_RUN_KIND) or MESSAGE_RUN_KIND) == AUTO_NAME_RUN_KIND:
                turn = router.run_auto_name_follow_up()
            else:
                turn = router.run_turn(run.request_content, stream_response=True, magi=run.magi)
            if self.run_store.is_cancel_requested(run.id):
                raise RunCancelledError("Run cancelled.")
            if turn is None:
                raise RuntimeError("Worker completed without a router turn.")

            delta_buffer.flush()
            magi_role_delta_buffer.flush()
            if (getattr(run, "run_kind", MESSAGE_RUN_KIND) or MESSAGE_RUN_KIND) == AUTO_NAME_RUN_KIND:
                self._complete_background_run(run, claimed_worker_id, turn)
            else:
                self._complete_run(run, claimed_worker_id, turn)
                self._queue_auto_name_run(run, turn)
        except RunOwnershipLostError:
            try:
                delta_buffer.flush()
                magi_role_delta_buffer.flush()
            except RunOwnershipLostError:
                pass
        except RunCancelledError as exc:
            try:
                delta_buffer.flush()
                magi_role_delta_buffer.flush()
            except RunOwnershipLostError:
                return
            self._cancel_run(run, claimed_worker_id, str(exc) or "Run cancelled.")
        except RouterExecutionError as exc:
            try:
                delta_buffer.flush()
                magi_role_delta_buffer.flush()
            except RunOwnershipLostError:
                return
            self._fail_run(run, claimed_worker_id, str(exc))
        except Exception as exc:
            try:
                delta_buffer.flush()
                magi_role_delta_buffer.flush()
            except RunOwnershipLostError:
                return
            self._fail_run(run, claimed_worker_id, str(exc))
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)

    def _worker_loop(self, slot_index):
        claimed_worker_id = f"{self.worker_id}:{slot_index}"
        poll_seconds = max(0.05, float(self.settings.chat_run_worker_poll_ms) / 1000.0)
        wakeup_sub = None
        redis_client = _get_redis_client()
        if redis_client is not None:
            try:
                from streaming.redis_events import subscribe_wakeup

                wakeup_sub = subscribe_wakeup(redis_client)
            except Exception:
                wakeup_sub = None
        try:
            while not self._stop_event.is_set():
                run = self.run_store.claim_next_run(claimed_worker_id, self.settings.chat_run_lease_seconds)
                if run is None:
                    if wakeup_sub is not None:
                        try:
                            wakeup_sub.get_message(timeout=poll_seconds)
                        except Exception:
                            self._stop_event.wait(poll_seconds)
                    else:
                        self._stop_event.wait(poll_seconds)
                    continue
                self._handle_claimed_run(run, claimed_worker_id)
        finally:
            if wakeup_sub is not None:
                try:
                    wakeup_sub.unsubscribe()
                    wakeup_sub.close()
                except Exception:
                    pass

    def run(self):
        concurrency = max(1, int(self.settings.chat_run_worker_concurrency))
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


def main():
    service = ChatRunWorkerService()

    def _shutdown(*_args):
        service.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    service.run()


if __name__ == "__main__":
    main()
