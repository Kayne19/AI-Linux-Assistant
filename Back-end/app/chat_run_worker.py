import os
import signal
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from config.settings import SETTINGS
from orchestration.model_router import ModelRouter
from orchestration.run_control import RunCancelledError
from persistence.postgres_app_store import PostgresAppStore
from persistence.postgres_memory_store import PostgresMemoryStore
from persistence.postgres_run_store import PostgresRunStore
from retrieval.factory import build_runtime_components
from retrieval.vectorDB import VectorDB
from streaming.redis_events import get_shared_client as _get_redis_client


class _DeltaBuffer:
    """Fan out live text immediately and durably checkpoint it in batches."""

    def __init__(self, run_id, run_store, redis_publish_fn, flush_interval=1.0, flush_bytes=200):
        self._run_id = run_id
        self._run_store = run_store
        self._redis_publish = redis_publish_fn
        self._flush_interval = max(0.01, float(flush_interval))
        self._flush_bytes = max(1, int(flush_bytes))
        self._lock = threading.Lock()
        self._buffer = ""
        self._window = 0
        self._last_flush = time.monotonic()

    def push(self, delta_text):
        delta_text = str(delta_text or "")
        if not delta_text:
            return
        self._redis_publish(self._run_id, delta_text, self._window)
        chunk, window = self._maybe_take_flush_chunk(delta_text)
        if chunk:
            self._run_store.append_text_checkpoint(self._run_id, chunk, window)

    def tick(self):
        chunk, window = self._take_flush_chunk(force=False)
        if chunk:
            self._run_store.append_text_checkpoint(self._run_id, chunk, window)

    def flush(self):
        chunk, window = self._take_flush_chunk(force=True)
        if chunk:
            self._run_store.append_text_checkpoint(self._run_id, chunk, window)

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

    def _heartbeat_and_flush_loop(self, run_id, claimed_worker_id, stop_event, delta_buffer):
        heartbeat_interval = max(1.0, float(self.settings.chat_run_lease_seconds) / 3.0)
        flush_interval = max(0.05, min(0.2, heartbeat_interval))
        last_heartbeat = time.monotonic()
        while not stop_event.wait(flush_interval):
            delta_buffer.tick()
            now = time.monotonic()
            if (now - last_heartbeat) < heartbeat_interval:
                continue
            if not self.run_store.heartbeat(run_id, claimed_worker_id, self.settings.chat_run_lease_seconds):
                break
            last_heartbeat = now

    def _emit_state(self, run_id, state, _turn):
        self.run_store.append_event(run_id, "state", state.name, None)

    def _emit_event(self, run_id, event_type, payload):
        self.run_store.append_event(run_id, "event", event_type, payload or None)

    def _publish_text_delta(self, run_id, delta_text, window):
        if not delta_text:
            return
        self.run_store._publish(
            run_id,
            0,
            "event",
            "text_delta",
            {"delta": delta_text, "window": int(window)},
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

        delta_buffer = _DeltaBuffer(
            run_id=run.id,
            run_store=self.run_store,
            redis_publish_fn=self._publish_text_delta,
        )
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_and_flush_loop,
            args=(run.id, claimed_worker_id, heartbeat_stop, delta_buffer),
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            router = self._build_router(run)
            router.set_state_listener(lambda state, turn: self._emit_state(run.id, state, turn))

            def _event_listener(event_type, payload):
                if event_type == "text_delta":
                    delta_buffer.push((payload or {}).get("delta", ""))
                    return
                self._emit_event(run.id, event_type, payload)

            router.set_event_listener(_event_listener)

            response_text = router.ask_question_stream(run.request_content, magi=run.magi)
            turn = router.last_turn
            if self.run_store.is_cancel_requested(run.id):
                raise RunCancelledError("Run cancelled.")
            if response_text.startswith("Router error:"):
                raise RuntimeError(response_text[len("Router error:"):].strip() or "Router error")
            if turn is None:
                raise RuntimeError("Worker completed without a router turn.")

            delta_buffer.flush()
            self._complete_run(run, claimed_worker_id, turn)
        except RunCancelledError as exc:
            delta_buffer.flush()
            self._cancel_run(run, claimed_worker_id, str(exc) or "Run cancelled.")
        except Exception as exc:
            delta_buffer.flush()
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
