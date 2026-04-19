"""Append-only JSONL audit log for autonomous ingestion decisions."""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path


def _default_traces_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "Back-end" / "ingest_traces"
        if candidate.parent.exists():
            return candidate
    return here.parents[3] / "Back-end" / "ingest_traces"


class AuditLog:
    """Write one JSON line per autonomous ingestion decision to a per-run JSONL file."""

    def __init__(self, run_id: str, traces_dir: str | os.PathLike | None = None) -> None:
        if traces_dir is None:
            traces_dir = _default_traces_dir()
        self._traces_dir = Path(traces_dir)
        self._traces_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._traces_dir / f"audit_{run_id}.jsonl"
        self._run_id = run_id
        self._lock = threading.Lock()
        self._fh = self._path.open("a", encoding="utf-8")

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        *,
        doc: str,
        phase: str,
        action: str,
        inputs: dict | None = None,
        chosen: dict | None = None,
        confidence: float | None = None,
        rationale: str | None = None,
    ) -> None:
        """Append one decision record as a JSON line; flushes immediately."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        entry = {
            "ts": ts,
            "run_id": self._run_id,
            "doc": doc,
            "phase": phase,
            "action": action,
            "inputs": inputs,
            "chosen": chosen,
            "confidence": confidence,
            "rationale": rationale,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            self._fh.write(line)
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.close()

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
