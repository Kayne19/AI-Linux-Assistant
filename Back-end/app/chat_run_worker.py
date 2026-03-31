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
        self.run_store = run_store or PostgresRunStore()
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

    def _heartbeat_loop(self, run_id, claimed_worker_id, stop_event):
        interval_seconds = max(1.0, float(self.settings.chat_run_lease_seconds) / 3.0)
        while not stop_event.wait(interval_seconds):
            if not self.run_store.heartbeat(run_id, claimed_worker_id, self.settings.chat_run_lease_seconds):
                break

    def _emit_state(self, run_id, state, _turn):
        self.run_store.append_event(run_id, "state", state.name, None)

    def _emit_event(self, run_id, event_type, payload):
        self.run_store.append_event(run_id, "event", event_type, payload or None)

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

        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(run.id, claimed_worker_id, heartbeat_stop),
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            router = self._build_router(run)
            router.set_state_listener(lambda state, turn: self._emit_state(run.id, state, turn))
            router.set_event_listener(lambda event_type, payload: self._emit_event(run.id, event_type, payload))

            response_text = router.ask_question_stream(run.request_content, magi=run.magi)
            turn = router.last_turn
            if self.run_store.is_cancel_requested(run.id):
                raise RunCancelledError("Run cancelled.")
            if response_text.startswith("Router error:"):
                raise RuntimeError(response_text[len("Router error:"):].strip() or "Router error")
            if turn is None:
                raise RuntimeError("Worker completed without a router turn.")

            self._complete_run(run, claimed_worker_id, turn)
        except RunCancelledError as exc:
            self._cancel_run(run, claimed_worker_id, str(exc) or "Run cancelled.")
        except Exception as exc:
            self._fail_run(run, claimed_worker_id, str(exc))
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)

    def _worker_loop(self, slot_index):
        claimed_worker_id = f"{self.worker_id}:{slot_index}"
        poll_seconds = max(0.1, float(self.settings.chat_run_worker_poll_ms) / 1000.0)
        while not self._stop_event.is_set():
            run = self.run_store.claim_next_run(claimed_worker_id, self.settings.chat_run_lease_seconds)
            if run is None:
                self._stop_event.wait(poll_seconds)
                continue
            self._handle_claimed_run(run, claimed_worker_id)

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
