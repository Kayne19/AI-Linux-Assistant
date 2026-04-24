"""Phase 2 orchestration for batch-mode ingestion.

Walks ``ingest_state/*`` directories, advances each parked document's FSM
through ``ENRICH_SUBMIT → AWAITING_ENRICHMENT → ENRICH_POLL → ENRICH_MERGE →
FINALIZE_OUTPUT → INGEST_VECTOR_DB → CLEANUP_ARTIFACTS → COMPLETED``. A single
doc failure quarantines that doc and the runner moves on.

Designed to be invoked from a cron-style wrapper (see
``scripts/ingest/batch_runner.py``). Each call is safe to re-enter: the
durable state on disk carries all resumable context.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ingestion.doc_state import (
    DocState,
    iter_states,
    load_state,
    quarantine_doc,
    save_state,
    state_dir,
)
from ingestion.stages.context_enrichment import (
    EnrichmentRequest,
    enrich_batch_merge,
    enrich_batch_poll,
    enrich_batch_submit,
)

logger = logging.getLogger(__name__)


# States handled by the batch runner, in the order it tries to advance.
AWAITING = "AWAITING_ENRICHMENT"
SUBMITTING = "ENRICH_SUBMIT"
POLLING = "ENRICH_POLL"
MERGING = "ENRICH_MERGE"
FINALIZING = "FINALIZE_OUTPUT"
INDEXING = "INGEST_VECTOR_DB"
CLEANING = "CLEANUP_ARTIFACTS"
COMPLETED = "COMPLETED"
FAILED = "FAILED"


@dataclass
class RunnerReport:
    submitted: list[str] = field(default_factory=list)
    polled: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    quarantined: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def _load_requests(dir_path: Path) -> list[EnrichmentRequest]:
    payload = json.loads((dir_path / "requests.json").read_text())
    return [EnrichmentRequest(**row) for row in payload]


def _load_elements(dir_path: Path) -> list[dict]:
    return json.loads((dir_path / "cleaned.json").read_text())


def _save_elements(dir_path: Path, elements: list[dict]) -> None:
    (dir_path / "merged.json").write_text(json.dumps(elements, indent=2))


def _final_output_path(state: DocState) -> Path:
    return Path(state.artifacts["final_output"])


def _advance_submit(
    base_dir: Path,
    state: DocState,
    *,
    submit_fn: Callable,
) -> DocState:
    """Upload batch JSONL + create a batch job; update state in place."""
    dir_path = state_dir(base_dir, state.doc_id)
    jsonl_path = dir_path / "batch_input.jsonl"
    submission = submit_fn(jsonl_path, metadata={"doc_id": state.doc_id})
    state.batch_id = submission.batch_id
    state.input_file_id = submission.input_file_id
    state.batch_status = submission.status
    state.state = POLLING
    save_state(base_dir, state)
    return state


def _advance_poll(
    base_dir: Path,
    state: DocState,
    *,
    poll_fn: Callable,
    download_fn: Callable | None,
) -> DocState:
    """Refresh batch status. On terminal success, download output and advance."""
    batch_status = poll_fn(state.batch_id)
    state.batch_status = batch_status.status
    state.output_file_id = batch_status.output_file_id
    state.error_file_id = batch_status.error_file_id
    if batch_status.status == "completed":
        if download_fn is not None and batch_status.output_file_id:
            dest = state_dir(base_dir, state.doc_id) / "batch_output.jsonl"
            download_fn(batch_status.output_file_id, dest)
        state.state = MERGING
    elif batch_status.status in {"failed", "expired", "cancelled"}:
        state.state = FAILED
        state.error = f"batch {state.batch_id} terminated with status={batch_status.status}"
    # else: still in flight (validating, in_progress, finalizing) — stay on POLLING
    save_state(base_dir, state)
    return state


def _advance_merge(base_dir: Path, state: DocState) -> DocState:
    """Merge downloaded batch output back into elements."""
    dir_path = state_dir(base_dir, state.doc_id)
    requests = _load_requests(dir_path)
    elements = _load_elements(dir_path)
    result = enrich_batch_merge(requests, dir_path / "batch_output.jsonl", elements)
    _save_elements(dir_path, elements)
    state.completed_chunks = result.completed_count
    state.failed_chunks = result.error_count
    for key in state.cache_metrics:
        state.cache_metrics[key] += int(result.cache_metrics.get(key, 0))
    state.state = FINALIZING
    save_state(base_dir, state)
    return state


def _advance_finalize(base_dir: Path, state: DocState) -> DocState:
    """Copy merged.json to the configured final_output path."""
    dir_path = state_dir(base_dir, state.doc_id)
    merged = dir_path / "merged.json"
    final = _final_output_path(state)
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_text(merged.read_text())
    state.state = INDEXING
    save_state(base_dir, state)
    return state


def _advance_index(
    base_dir: Path,
    state: DocState,
    *,
    indexer_fn: Callable[[str], dict],
) -> DocState:
    """Invoke the vector-DB indexer on the final output."""
    result = indexer_fn(str(_final_output_path(state)))
    logger.info("Indexed doc_id=%s rows=%s table=%s", state.doc_id, result.get("rows"), result.get("table_name"))
    state.state = CLEANING
    save_state(base_dir, state)
    return state


def _advance_cleanup(base_dir: Path, state: DocState) -> DocState:
    """Run post-index artifact cleanup on the configured file set."""
    from ingestion.pipeline import cleanup_artifacts  # local import avoids cycle

    arts = state.artifacts
    cleanup_artifacts(
        Path(arts["raw_output"]),
        Path(arts["clean_output"]),
        Path(arts["context_output"]),
        Path(arts["final_output"]),
    )
    state.state = COMPLETED
    save_state(base_dir, state)
    return state


def advance_one(
    base_dir: Path,
    doc_id: str,
    *,
    batch_client=None,
    indexer_fn: Callable[[str], dict] | None = None,
    submit_fn: Callable | None = None,
    poll_fn: Callable | None = None,
    download_fn: Callable | None = None,
) -> DocState | None:
    """Advance a single doc one or more steps toward COMPLETED.

    Injectable callables let tests drive the runner without hitting the real
    OpenAI / LanceDB surfaces. When left as ``None`` they default to the real
    thin-wrapper callers.
    """
    state = load_state(base_dir, doc_id)
    if state is None:
        return None

    if submit_fn is None:
        submit_fn = lambda path, metadata=None: enrich_batch_submit(path, client=batch_client, metadata=metadata)
    if poll_fn is None:
        poll_fn = lambda bid: enrich_batch_poll(bid, client=batch_client)

    if state.state == AWAITING:
        state.state = SUBMITTING
        save_state(base_dir, state)

    if state.state == SUBMITTING:
        state = _advance_submit(base_dir, state, submit_fn=submit_fn)

    if state.state == POLLING:
        state = _advance_poll(base_dir, state, poll_fn=poll_fn, download_fn=download_fn)

    if state.state == MERGING:
        state = _advance_merge(base_dir, state)

    if state.state == FINALIZING:
        state = _advance_finalize(base_dir, state)

    if state.state == INDEXING and indexer_fn is not None:
        state = _advance_index(base_dir, state, indexer_fn=indexer_fn)

    if state.state == CLEANING:
        state = _advance_cleanup(base_dir, state)

    return state


def run_once(
    base_dir: Path,
    *,
    batch_client=None,
    indexer_fn: Callable[[str], dict] | None = None,
    submit_fn: Callable | None = None,
    poll_fn: Callable | None = None,
    download_fn: Callable | None = None,
) -> RunnerReport:
    """Walk every parked doc and try to advance it.

    Terminal-failure docs are quarantined. Returns a :class:`RunnerReport` that
    the CLI wrapper can log. No sleeping, no polling loop: callers schedule
    this via cron or a manual invocation.
    """
    report = RunnerReport()
    base_dir = Path(base_dir)
    for doc_id, state in iter_states(base_dir):
        if state.state == COMPLETED:
            continue
        if state.state == FAILED:
            quarantine_doc(base_dir, doc_id, reason=state.error or "unspecified failure")
            report.quarantined.append((doc_id, state.error or "unspecified failure"))
            continue
        try:
            before = state.state
            new_state = advance_one(
                base_dir,
                doc_id,
                batch_client=batch_client,
                indexer_fn=indexer_fn,
                submit_fn=submit_fn,
                poll_fn=poll_fn,
                download_fn=download_fn,
            )
            if new_state is None:
                continue
            if before in {AWAITING, SUBMITTING} and new_state.state != SUBMITTING:
                report.submitted.append(doc_id)
            if new_state.state == POLLING:
                report.polled.append(doc_id)
            if new_state.state == COMPLETED:
                report.completed.append(doc_id)
            if new_state.state == FAILED:
                quarantine_doc(base_dir, doc_id, reason=new_state.error or "unspecified failure")
                report.quarantined.append((doc_id, new_state.error or "unspecified failure"))
        except Exception as exc:  # noqa: BLE001
            logger.exception("batch_runner error for doc_id=%s", doc_id)
            report.errors.append((doc_id, str(exc)))
    return report
