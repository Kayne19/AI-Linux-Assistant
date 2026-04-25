"""Tests for the two-phase batch runner (T10).

Covers:
- DocState round-trips through save/load
- AWAITING → SUBMIT → POLL → MERGE → FINALIZE → INDEX → CLEAN → COMPLETED via advance_one
- POLL stays on POLL while batch is still in flight
- POLL drops to FAILED on terminal-failure batch status
- run_once quarantines FAILED docs and surfaces errors in the report
- Real EnrichmentRequest list round-trips through requests.json
"""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from ingestion.batch_runner import (
    AWAITING,
    COMPLETED,
    FAILED,
    MERGING,
    POLLING,
    SUBMITTING,
    advance_one,
    run_once,
)
from ingestion.doc_state import DocState, iter_states, load_state, save_state
from ingestion.identity.schema import DocumentIdentity
from ingestion.stages.context_enrichment import EnrichmentRequest, enrich_batch_prepare


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeSubmission:
    batch_id: str
    input_file_id: str
    status: str
    created_at: int | None = 0


@dataclass
class _FakeStatus:
    batch_id: str
    status: str
    output_file_id: str | None
    error_file_id: str | None
    request_counts: dict
    completed_at: int | None = 0


def _make_requests(n: int = 2) -> list[EnrichmentRequest]:
    return [
        EnrichmentRequest(
            custom_id=f"chunk-{i:06d}-aaaa",
            element_index=i,
            system_prompt="sys",
            user_message=f"user-{i}",
            model="m",
            temperature=0.1,
            max_output_tokens=120,
        )
        for i in range(n)
    ]


def _prime_doc(
    tmp_path: Path,
    doc_id: str = "testdoc",
    initial_state: str = AWAITING,
    n_chunks: int = 2,
) -> tuple[Path, DocState, list[EnrichmentRequest]]:
    """Write the on-disk bundle a real ENRICH_PREPARE would leave behind."""
    base_dir = tmp_path / "ingest_state"
    dir_path = base_dir / doc_id
    dir_path.mkdir(parents=True)

    requests = _make_requests(n_chunks)
    enrich_batch_prepare(requests, dir_path / "batch_input.jsonl")
    (dir_path / "requests.json").write_text(
        json.dumps(
            [
                {
                    "custom_id": r.custom_id,
                    "element_index": r.element_index,
                    "system_prompt": r.system_prompt,
                    "user_message": r.user_message,
                    "model": r.model,
                    "temperature": r.temperature,
                    "max_output_tokens": r.max_output_tokens,
                }
                for r in requests
            ]
        )
    )

    elements = [
        {"type": "NarrativeText", "text": f"chunk {i} body long enough"} for i in range(n_chunks)
    ]
    (dir_path / "cleaned.json").write_text(json.dumps(elements))

    artifacts = {
        "raw_output": str(tmp_path / "raw.json"),
        "clean_output": str(tmp_path / "clean.json"),
        "context_output": str(tmp_path / "context.txt"),
        "final_output": str(tmp_path / "final.json"),
    }
    # Touch inputs to satisfy cleanup_artifacts unlink-safe behavior.
    for k, path in artifacts.items():
        if k != "final_output":
            Path(path).write_text("")

    state = DocState(
        doc_id=doc_id,
        source_pdf=str(tmp_path / "source.pdf"),
        state=initial_state,
        total_chunks=n_chunks,
        artifacts=artifacts,
    )
    save_state(base_dir, state)
    return base_dir, state, requests


def _identity() -> DocumentIdentity:
    return DocumentIdentity(
        canonical_source_id="testdoc-canonical",
        canonical_title="Test Doc",
        source_family="debian",
    )


def _write_batch_results(dir_path: Path, requests: list[EnrichmentRequest]) -> None:
    lines = []
    for request in requests:
        lines.append(
            json.dumps(
                {
                    "custom_id": request.custom_id,
                    "response": {
                        "status_code": 200,
                        "body": {
                            "output_text": f"ai-ctx-{request.element_index}",
                            "usage": {
                                "input_tokens": 5,
                                "output_tokens": 3,
                                "input_tokens_details": {"cached_tokens": 1},
                            },
                        },
                    },
                }
            )
        )
    (dir_path / "batch_output.jsonl").write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# DocState
# ---------------------------------------------------------------------------


def test_docstate_roundtrip(tmp_path):
    base = tmp_path / "ingest_state"
    state = DocState(doc_id="abc", source_pdf="/a.pdf", state=AWAITING, total_chunks=3)
    save_state(base, state)
    loaded = load_state(base, "abc")
    assert loaded is not None
    assert loaded.doc_id == "abc"
    assert loaded.state == AWAITING
    assert loaded.total_chunks == 3


def test_iter_states_skips_non_directories_and_missing_files(tmp_path):
    base = tmp_path / "ingest_state"
    base.mkdir()
    (base / "loose_file.txt").write_text("ignored")
    (base / "emptydir").mkdir()
    save_state(base, DocState(doc_id="good", source_pdf="/p.pdf", state=AWAITING))
    found = dict(iter_states(base))
    assert set(found) == {"good"}


# ---------------------------------------------------------------------------
# advance_one — full happy-path through COMPLETED
# ---------------------------------------------------------------------------


def test_advance_one_submits_polls_merges_finalizes_indexes_cleans(tmp_path):
    base_dir, _state, requests = _prime_doc(tmp_path)

    submit_fn = lambda path, metadata=None: _FakeSubmission(
        batch_id="batch_1", input_file_id="file_1", status="validating"
    )

    poll_calls = {"n": 0}

    def poll_fn(batch_id):
        poll_calls["n"] += 1
        return _FakeStatus(
            batch_id=batch_id,
            status="completed",
            output_file_id="output_1",
            error_file_id=None,
            request_counts={"total": 2, "completed": 2, "failed": 0},
        )

    # download_fn writes the simulated Batch API result
    def download_fn(file_id, dest):
        _write_batch_results(dest.parent, requests)

    indexer_calls = {"paths": []}
    indexer_fn = lambda path, document_identity=None: (
        indexer_calls["paths"].append((path, document_identity))
        or {"rows": 2, "table_name": "t", "created_table": False}
    )

    new_state = advance_one(
        base_dir,
        "testdoc",
        submit_fn=submit_fn,
        poll_fn=poll_fn,
        download_fn=download_fn,
        indexer_fn=indexer_fn,
    )
    assert new_state is not None
    assert new_state.state == COMPLETED
    assert new_state.batch_id == "batch_1"
    assert new_state.output_file_id == "output_1"
    assert new_state.completed_chunks == 2
    assert new_state.failed_chunks == 0
    # Indexer was called with the final_output path before CLEANUP_ARTIFACTS
    # unlinked it.
    assert indexer_calls["paths"] == [(new_state.artifacts["final_output"], None)]


def test_advance_one_uses_default_batch_client_downloader(tmp_path):
    base_dir, _state, requests = _prime_doc(tmp_path)

    submit_fn = lambda path, metadata=None: _FakeSubmission(
        batch_id="batch_1", input_file_id="file_1", status="validating"
    )
    poll_fn = lambda bid: _FakeStatus(
        batch_id=bid,
        status="completed",
        output_file_id="output_1",
        error_file_id=None,
        request_counts={"total": 2, "completed": 2, "failed": 0},
    )

    class _FakeBatchClient:
        def __init__(self):
            self.downloads = []

        def download_output(self, file_id, dest):
            self.downloads.append((file_id, Path(dest).name))
            _write_batch_results(Path(dest).parent, requests)

    client = _FakeBatchClient()
    state = advance_one(
        base_dir,
        "testdoc",
        batch_client=client,
        submit_fn=submit_fn,
        poll_fn=poll_fn,
        indexer_fn=lambda path, document_identity=None: {"rows": 2, "table_name": "t", "created_table": False},
    )

    assert state.state == COMPLETED
    assert client.downloads == [("output_1", "batch_output.jsonl")]


def test_advance_one_poll_stays_when_in_progress(tmp_path):
    base_dir, _state, _requests = _prime_doc(tmp_path)

    submit_fn = lambda path, metadata=None: _FakeSubmission(
        batch_id="batch_1", input_file_id="file_1", status="validating"
    )
    poll_fn = lambda bid: _FakeStatus(
        batch_id=bid,
        status="in_progress",
        output_file_id=None,
        error_file_id=None,
        request_counts={"total": 2, "completed": 0, "failed": 0},
    )

    state = advance_one(
        base_dir, "testdoc", submit_fn=submit_fn, poll_fn=poll_fn,
    )
    assert state.state == POLLING
    assert state.batch_status == "in_progress"


def test_advance_one_poll_marks_failed_when_completed_without_output(tmp_path):
    """Regression: batch reported 'completed' but output_file_id is missing
    (or download_fn is None). We must not advance to MERGING with no data —
    the merge would silently apply zero contexts."""
    base_dir, _state, _requests = _prime_doc(tmp_path)

    submit_fn = lambda path, metadata=None: _FakeSubmission(
        batch_id="batch_1", input_file_id="file_1", status="validating"
    )
    poll_fn = lambda bid: _FakeStatus(
        batch_id=bid,
        status="completed",
        output_file_id=None,
        error_file_id=None,
        request_counts={"total": 2, "completed": 2, "failed": 0},
    )

    # No download_fn — and even if one were provided, output_file_id is None.
    state = advance_one(base_dir, "testdoc", submit_fn=submit_fn, poll_fn=poll_fn)
    assert state.state == FAILED
    assert "output is unavailable" in (state.error or "")


def test_advance_one_poll_marks_failed_on_terminal_error(tmp_path):
    base_dir, _state, _requests = _prime_doc(tmp_path)

    submit_fn = lambda path, metadata=None: _FakeSubmission(
        batch_id="batch_1", input_file_id="file_1", status="validating"
    )
    poll_fn = lambda bid: _FakeStatus(
        batch_id=bid,
        status="failed",
        output_file_id=None,
        error_file_id=None,
        request_counts={"total": 2, "completed": 0, "failed": 2},
    )

    state = advance_one(base_dir, "testdoc", submit_fn=submit_fn, poll_fn=poll_fn)
    assert state.state == FAILED
    assert "failed" in (state.error or "")


def test_advance_one_downloads_batch_error_file_on_terminal_error(tmp_path):
    base_dir, _state, _requests = _prime_doc(tmp_path)

    submit_fn = lambda path, metadata=None: _FakeSubmission(
        batch_id="batch_1", input_file_id="file_1", status="validating"
    )
    poll_fn = lambda bid: _FakeStatus(
        batch_id=bid,
        status="failed",
        output_file_id=None,
        error_file_id="err_1",
        request_counts={"total": 2, "completed": 0, "failed": 2},
    )

    def download_fn(file_id, dest):
        Path(dest).write_text('{"error":"bad"}\n')

    state = advance_one(base_dir, "testdoc", submit_fn=submit_fn, poll_fn=poll_fn, download_fn=download_fn)
    assert state.state == FAILED
    assert (base_dir / "testdoc" / "batch_error.jsonl").exists()


def test_advance_one_marks_completed_batch_with_partial_failures_failed(tmp_path):
    base_dir, _state, _requests = _prime_doc(tmp_path)

    submit_fn = lambda path, metadata=None: _FakeSubmission(
        batch_id="batch_1", input_file_id="file_1", status="validating"
    )
    poll_fn = lambda bid: _FakeStatus(
        batch_id=bid,
        status="completed",
        output_file_id="out_1",
        error_file_id="err_1",
        request_counts={"total": 2, "completed": 1, "failed": 1},
    )
    downloads = []

    def download_fn(file_id, dest):
        downloads.append((file_id, Path(dest).name))
        Path(dest).write_text("")

    state = advance_one(base_dir, "testdoc", submit_fn=submit_fn, poll_fn=poll_fn, download_fn=download_fn)
    assert state.state == FAILED
    assert "partial failures" in (state.error or "")
    assert downloads == [("err_1", "batch_error.jsonl")]


def test_advance_one_skips_missing_doc(tmp_path):
    base = tmp_path / "ingest_state"
    base.mkdir()
    assert advance_one(base, "nope") is None


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------


def test_run_once_quarantines_failed_docs(tmp_path):
    base_dir, state, _requests = _prime_doc(tmp_path, initial_state=FAILED)
    state.error = "test failure"
    save_state(base_dir, state)

    report = run_once(base_dir, submit_fn=lambda *a, **k: None, poll_fn=lambda *a, **k: None)
    assert ("testdoc", "test failure") in report.quarantined
    # Directory moved to sibling 'failed/'
    assert not (base_dir / "testdoc").exists()
    assert (base_dir.parent / "failed" / "testdoc" / "error.json").exists()


def test_run_once_reports_errors_without_blocking_other_docs(tmp_path):
    base_dir_a, _sa, _ra = _prime_doc(tmp_path, doc_id="doc_a")
    base_dir_b, _sb, _rb = _prime_doc(tmp_path, doc_id="doc_b")
    assert base_dir_a == base_dir_b

    def flaky_submit(jsonl_path, metadata=None):
        if "doc_a" in str(jsonl_path):
            raise RuntimeError("submit blew up")
        return _FakeSubmission(batch_id="bid", input_file_id="fid", status="validating")

    poll_fn = lambda bid: _FakeStatus(
        batch_id=bid, status="in_progress", output_file_id=None, error_file_id=None, request_counts={}
    )

    report = run_once(base_dir_a, submit_fn=flaky_submit, poll_fn=poll_fn)
    # doc_a errored; doc_b advanced (even if it doesn't reach COMPLETED)
    assert any(doc == "doc_a" for doc, _ in report.errors)
    # doc_b is now somewhere past AWAITING
    state_b = load_state(base_dir_a, "doc_b")
    assert state_b is not None
    assert state_b.state in {SUBMITTING, POLLING}


def test_run_once_skips_already_completed_docs(tmp_path):
    base_dir, state, _requests = _prime_doc(tmp_path, initial_state=COMPLETED)
    # Should be a no-op — no submit/poll callbacks required.
    report = run_once(
        base_dir,
        submit_fn=lambda *a, **k: pytest.fail("should not submit"),
        poll_fn=lambda *a, **k: pytest.fail("should not poll"),
    )
    assert report.completed == []
    assert report.submitted == []


# ---------------------------------------------------------------------------
# Integration: the end-to-end state sequence
# ---------------------------------------------------------------------------


def test_advance_one_records_state_transitions_in_trace(tmp_path):
    base_dir, _state, requests = _prime_doc(tmp_path)

    submit_fn = lambda path, metadata=None: _FakeSubmission(
        batch_id="batch_1", input_file_id="file_1", status="validating"
    )
    poll_fn = lambda bid: _FakeStatus(
        batch_id=bid, status="completed", output_file_id="out_1", error_file_id=None,
        request_counts={"total": 2, "completed": 2, "failed": 0},
    )
    download_fn = lambda file_id, dest: _write_batch_results(dest.parent, requests)
    indexer_fn = lambda path, document_identity=None: {"rows": 2, "table_name": "t", "created_table": False}

    advance_one(
        base_dir, "testdoc",
        submit_fn=submit_fn, poll_fn=poll_fn, download_fn=download_fn, indexer_fn=indexer_fn,
    )

    # Tail state persists COMPLETED with accurate cache + chunk counts.
    final = load_state(base_dir, "testdoc")
    assert final.state == COMPLETED
    assert final.completed_chunks == 2
    assert final.cache_metrics["input_tokens"] == 10
    assert final.cache_metrics["output_tokens"] == 6
    assert final.cache_metrics["cached_tokens"] == 2


def test_advance_one_fails_on_missing_output_rows(tmp_path):
    base_dir, _state, requests = _prime_doc(tmp_path)

    submit_fn = lambda path, metadata=None: _FakeSubmission(
        batch_id="batch_1", input_file_id="file_1", status="validating"
    )
    poll_fn = lambda bid: _FakeStatus(
        batch_id=bid,
        status="completed",
        output_file_id="out_1",
        error_file_id=None,
        request_counts={"total": 2, "completed": 2, "failed": 0},
    )

    def download_fn(file_id, dest):
        line = {
            "custom_id": requests[0].custom_id,
            "response": {"status_code": 200, "body": {"output_text": "only one"}},
        }
        Path(dest).write_text(json.dumps(line))

    state = advance_one(base_dir, "testdoc", submit_fn=submit_fn, poll_fn=poll_fn, download_fn=download_fn)
    assert state.state == FAILED
    assert "completed=1, expected=2" in (state.error or "")
    assert (base_dir / "testdoc" / "merge_errors.json").exists()


def test_advance_one_passes_document_identity_to_indexer(tmp_path):
    base_dir, state, requests = _prime_doc(tmp_path)
    identity = _identity()
    state.document_identity = identity.to_dict()
    save_state(base_dir, state)

    submit_fn = lambda path, metadata=None: _FakeSubmission(
        batch_id="batch_1", input_file_id="file_1", status="validating"
    )
    poll_fn = lambda bid: _FakeStatus(
        batch_id=bid,
        status="completed",
        output_file_id="out_1",
        error_file_id=None,
        request_counts={"total": 2, "completed": 2, "failed": 0},
    )
    seen = {}

    def indexer_fn(path, document_identity=None):
        seen["identity"] = document_identity
        return {"rows": 2, "table_name": "t", "created_table": False}

    advance_one(
        base_dir,
        "testdoc",
        submit_fn=submit_fn,
        poll_fn=poll_fn,
        download_fn=lambda file_id, dest: _write_batch_results(Path(dest).parent, requests),
        indexer_fn=indexer_fn,
    )

    assert isinstance(seen["identity"], DocumentIdentity)
    assert seen["identity"].canonical_source_id == "testdoc-canonical"
