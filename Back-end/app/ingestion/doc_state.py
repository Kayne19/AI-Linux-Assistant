"""Durable per-document state for two-phase (batch-mode) ingestion.

The Phase 1 runner writes each document's FSM position, batch-input JSONL, and
serialized enrichment requests under::

    ingest_state/<doc_id>/
        state.json          # { "state": "AWAITING_ENRICHMENT", ... }
        batch_input.jsonl   # /v1/responses envelopes (one per chunk)
        requests.json       # serialized EnrichmentRequest list (for merge)
        cleaned.json        # cleaned+sectioned elements
        context.txt         # document context text
        batch_output.jsonl  # downloaded batch result (after poll)

Phase 2 (batch_runner) walks ``ingest_state/*`` directories and advances each
doc's FSM independently; a single bad doc never blocks the others.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class DocState:
    """On-disk FSM record for a single parked document."""

    doc_id: str                         # canonical_source_id or filename stem
    source_pdf: str                     # absolute path to the originating PDF
    state: str                          # IngestState value (string)
    batch_id: str | None = None         # assigned after ENRICH_SUBMIT
    input_file_id: str | None = None
    output_file_id: str | None = None
    error_file_id: str | None = None
    batch_status: str | None = None     # raw OpenAI batch status
    total_chunks: int = 0               # count of enrichment requests
    completed_chunks: int = 0
    failed_chunks: int = 0
    cache_metrics: dict = field(default_factory=lambda: {"cached_tokens": 0, "input_tokens": 0, "output_tokens": 0})
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Artifact paths, relative to the state dir
    artifacts: dict = field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()


def state_dir(base_dir: Path, doc_id: str) -> Path:
    """Return the directory for a specific doc's state. Caller mkdir-s."""
    return Path(base_dir) / doc_id


def save_state(base_dir: Path, state: DocState) -> Path:
    """Persist *state* to ``<base_dir>/<doc_id>/state.json``. Atomic-ish rename."""
    dir_path = state_dir(base_dir, state.doc_id)
    dir_path.mkdir(parents=True, exist_ok=True)
    state.touch()
    target = dir_path / "state.json"
    # Per-process unique tmp name so concurrent writers (multiple batch
    # runner processes acting on the same doc) cannot clobber each other's
    # in-flight tmp before the rename.
    tmp = dir_path / f"state.json.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    tmp.write_text(json.dumps(asdict(state), indent=2, sort_keys=True))
    tmp.replace(target)
    return target


def load_state(base_dir: Path, doc_id: str) -> DocState | None:
    """Load state for *doc_id*, or ``None`` if missing/unparseable."""
    path = state_dir(base_dir, doc_id) / "state.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return DocState(**data)


def iter_states(base_dir: Path):
    """Yield ``(doc_id, DocState)`` pairs for every well-formed state.json found."""
    base = Path(base_dir)
    if not base.exists():
        return
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        state = load_state(base, entry.name)
        if state is None:
            continue
        yield entry.name, state


def quarantine_doc(base_dir: Path, doc_id: str, reason: str) -> Path:
    """Move ``<base_dir>/<doc_id>`` to ``<base_dir>/../failed/<doc_id>``.

    Returns the new directory path. The quarantine root sits at the same level
    as the state root so scripts can point either at ``ingest_state/`` or
    ``failed/``.
    """
    src = state_dir(base_dir, doc_id)
    failed_root = Path(base_dir).parent / "failed"
    failed_root.mkdir(parents=True, exist_ok=True)
    dst = failed_root / doc_id
    if dst.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        dst = failed_root / f"{doc_id}_{stamp}"
    if src.exists():
        src.rename(dst)
    (dst / "error.json").write_text(
        json.dumps({"doc_id": doc_id, "reason": reason, "quarantined_at": datetime.now(timezone.utc).isoformat()}, indent=2)
    )
    return dst
