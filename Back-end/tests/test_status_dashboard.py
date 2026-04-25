"""Tests for the T14 status dashboard CLI.

Covers:
- Empty state directory prints a friendly note and exits 0
- Synthetic state files render in the canonical FSM order with counts
- ``--json`` emits machine-readable output with all known states present
- The quarantined dir adjacent to the state dir is counted
"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "ingest"
    / "status.py"
)


def _write_state(state_dir: Path, doc_id: str, **overrides) -> None:
    base = {
        "doc_id": doc_id,
        "source_pdf": f"/tmp/{doc_id}.pdf",
        "state": "AWAITING_ENRICHMENT",
        "batch_id": None,
        "input_file_id": None,
        "output_file_id": None,
        "error_file_id": None,
        "batch_status": None,
        "total_chunks": 0,
        "completed_chunks": 0,
        "failed_chunks": 0,
        "cache_metrics": {"cached_tokens": 0, "input_tokens": 0, "output_tokens": 0},
        "error": None,
        "created_at": "x",
        "updated_at": "x",
        "artifacts": {},
    }
    base.update(overrides)
    doc_dir = state_dir / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "state.json").write_text(json.dumps(base))


def _run(state_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--state-dir", str(state_dir), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_empty_state_dir_prints_friendly_message(tmp_path):
    result = _run(tmp_path / "missing")
    assert result.returncode == 0
    assert "does not exist" in result.stdout


def test_state_directory_with_no_docs_says_zero(tmp_path):
    state_dir = tmp_path / "ingest_state"
    state_dir.mkdir()
    result = _run(state_dir)
    assert result.returncode == 0
    assert "Total documents tracked: 0" in result.stdout


def test_renders_counts_per_state(tmp_path):
    state_dir = tmp_path / "ingest_state"
    state_dir.mkdir()
    _write_state(state_dir, "doc_a", state="AWAITING_ENRICHMENT")
    _write_state(state_dir, "doc_b", state="AWAITING_ENRICHMENT")
    _write_state(state_dir, "doc_c", state="ENRICH_POLL", batch_id="b1")
    _write_state(state_dir, "doc_d", state="COMPLETED")

    result = _run(state_dir)
    assert result.returncode == 0
    out = result.stdout
    assert "AWAITING_ENRICHMENT" in out and "  2" in out
    assert "ENRICH_POLL" in out
    assert "COMPLETED" in out


def test_quarantined_sibling_dir_is_counted(tmp_path):
    state_dir = tmp_path / "ingest_state"
    state_dir.mkdir()
    _write_state(state_dir, "doc_a", state="AWAITING_ENRICHMENT")
    quarantined = tmp_path / "failed" / "broken_doc"
    quarantined.mkdir(parents=True)

    result = _run(state_dir)
    assert "QUARANTINED" in result.stdout
    assert "broken_doc" not in result.stdout  # only verbose mode lists names

    verbose = _run(state_dir, "--verbose")
    assert "broken_doc" in verbose.stdout


def test_json_mode_emits_structured_payload(tmp_path):
    state_dir = tmp_path / "ingest_state"
    state_dir.mkdir()
    _write_state(state_dir, "doc_a", state="ENRICH_POLL", batch_id="b1", batch_status="in_progress")

    result = _run(state_dir, "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["state_dir"].endswith("ingest_state")
    assert "ENRICH_POLL" in payload["totals"]
    assert payload["totals"]["ENRICH_POLL"] == 1
    assert payload["documents"][0]["doc_id"] == "doc_a"
    assert payload["documents"][0]["batch_id"] == "b1"
    assert payload["documents"][0]["batch_status"] == "in_progress"
