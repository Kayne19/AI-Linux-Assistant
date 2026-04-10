from __future__ import annotations

import json
import threading
import time

from .errors import JobOwnershipLostError


class TextCheckpointBuffer:
    """Fan out live text immediately and durably checkpoint it in batches."""

    def __init__(
        self,
        *,
        job_id,
        worker_id,
        store,
        ownership_lost_event,
        publish_live_fn,
        flush_interval=0.2,
        flush_bytes=200,
        checkpoint_code="text_checkpoint",
    ):
        self._job_id = job_id
        self._worker_id = worker_id
        self._store = store
        self._ownership_lost_event = ownership_lost_event
        self._publish_live_fn = publish_live_fn
        self._flush_interval = max(0.01, float(flush_interval))
        self._flush_bytes = max(1, int(flush_bytes))
        self._checkpoint_code = checkpoint_code
        self._lock = threading.Lock()
        self._buffer = ""
        self._window = 0
        self._last_flush = time.monotonic()

    def push(self, delta_text):
        self._raise_if_ownership_lost()
        delta_text = str(delta_text or "")
        if not delta_text:
            return
        self._publish_live_fn(self._job_id, delta_text, self._window)
        chunk, window = self._maybe_take_flush_chunk(delta_text)
        if chunk:
            self._store.append_output_checkpoint_for_worker(
                self._job_id,
                self._worker_id,
                chunk,
                window,
                code=self._checkpoint_code,
            )

    def tick(self):
        self._raise_if_ownership_lost()
        chunk, window = self._take_flush_chunk(force=False)
        if chunk:
            self._store.append_output_checkpoint_for_worker(
                self._job_id,
                self._worker_id,
                chunk,
                window,
                code=self._checkpoint_code,
            )

    def flush(self):
        self._raise_if_ownership_lost()
        chunk, window = self._take_flush_chunk(force=True)
        if chunk:
            self._store.append_output_checkpoint_for_worker(
                self._job_id,
                self._worker_id,
                chunk,
                window,
                code=self._checkpoint_code,
            )

    def _raise_if_ownership_lost(self):
        if self._ownership_lost_event.is_set():
            raise JobOwnershipLostError(f"Worker '{self._worker_id}' no longer owns job '{self._job_id}'.")

    def _maybe_take_flush_chunk(self, delta_text):
        now = time.monotonic()
        with self._lock:
            self._buffer += delta_text
            if len(self._buffer) < self._flush_bytes and (now - self._last_flush) < self._flush_interval:
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


def _scope_state_key(scope):
    scope = dict(scope or {})
    return json.dumps(scope, sort_keys=True, default=str)


class ScopedTextCheckpointBuffer:
    """Checkpoint multiple named text streams using absolute-text snapshots."""

    def __init__(
        self,
        *,
        job_id,
        worker_id,
        store,
        ownership_lost_event,
        publish_live_fn,
        flush_interval=0.2,
        flush_bytes=200,
        delta_code="scoped_text_delta",
        checkpoint_code="scoped_text_checkpoint",
    ):
        self._job_id = job_id
        self._worker_id = worker_id
        self._store = store
        self._ownership_lost_event = ownership_lost_event
        self._publish_live_fn = publish_live_fn
        self._flush_interval = max(0.01, float(flush_interval))
        self._flush_bytes = max(1, int(flush_bytes))
        self._delta_code = delta_code
        self._checkpoint_code = checkpoint_code
        self._lock = threading.Lock()
        self._states = {}

    def begin_stream(self, scope):
        normalized_scope = dict(scope or {})
        key = _scope_state_key(normalized_scope)
        with self._lock:
            self._states[key] = {
                "scope": normalized_scope,
                "buffer": "",
                "text": "",
                "window": 0,
                "last_flush": time.monotonic(),
            }

    def push(self, scope, delta_text):
        self._raise_if_ownership_lost()
        delta_text = str(delta_text or "")
        if not delta_text:
            return
        state = self._ensure_state(scope)
        self._publish_live_fn(
            self._job_id,
            {**state["scope"], "delta": delta_text, "window": int(state["window"])},
            self._delta_code,
        )
        checkpoint = self._maybe_take_flush_chunk(state, delta_text)
        if checkpoint is not None:
            self._append_checkpoint(checkpoint)

    def flush_stream(self, scope):
        self._raise_if_ownership_lost()
        state = self._get_state(scope)
        if state is None:
            return
        checkpoint = self._take_flush_chunk(state, force=True)
        if checkpoint is not None:
            self._append_checkpoint(checkpoint)

    def finish_stream(self, scope):
        key = _scope_state_key(scope)
        with self._lock:
            self._states.pop(key, None)

    def tick(self):
        self._raise_if_ownership_lost()
        checkpoints = []
        now = time.monotonic()
        with self._lock:
            for state in self._states.values():
                checkpoint = self._take_flush_chunk_locked(state, force=False, now=now)
                if checkpoint is not None:
                    checkpoints.append(checkpoint)
        for checkpoint in checkpoints:
            self._append_checkpoint(checkpoint)

    def flush(self):
        self._raise_if_ownership_lost()
        checkpoints = []
        now = time.monotonic()
        with self._lock:
            for state in self._states.values():
                checkpoint = self._take_flush_chunk_locked(state, force=True, now=now)
                if checkpoint is not None:
                    checkpoints.append(checkpoint)
        for checkpoint in checkpoints:
            self._append_checkpoint(checkpoint)

    def _raise_if_ownership_lost(self):
        if self._ownership_lost_event.is_set():
            raise JobOwnershipLostError(f"Worker '{self._worker_id}' no longer owns job '{self._job_id}'.")

    def _get_state(self, scope):
        key = _scope_state_key(scope)
        with self._lock:
            return self._states.get(key)

    def _ensure_state(self, scope):
        normalized_scope = dict(scope or {})
        key = _scope_state_key(normalized_scope)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = {
                    "scope": normalized_scope,
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

    def _take_flush_chunk_locked(self, state, *, force, now):
        if not state["buffer"]:
            return None
        if not force and len(state["buffer"]) < self._flush_bytes and (now - state["last_flush"]) < self._flush_interval:
            return None
        state["text"] += state["buffer"]
        checkpoint = {
            "scope": dict(state["scope"]),
            "text": state["text"],
            "window": int(state["window"]),
        }
        state["buffer"] = ""
        state["window"] += 1
        state["last_flush"] = now
        return checkpoint

    def _append_checkpoint(self, checkpoint):
        self._store.append_scoped_output_checkpoint_for_worker(
            self._job_id,
            self._worker_id,
            checkpoint["scope"],
            checkpoint["text"],
            checkpoint["window"],
            code=self._checkpoint_code,
        )
