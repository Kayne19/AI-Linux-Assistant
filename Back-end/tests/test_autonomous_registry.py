"""Tests for auto_apply_registry_suggestion (T5)."""

import json
import sys
from pathlib import Path

import pytest

# Ensure app/ is on sys.path so ingestion imports work.
_BACKEND = Path(__file__).resolve().parents[1]
_APP = _BACKEND / "app"
for _p in (_BACKEND, _APP):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ingestion.audit import AuditLog
from ingestion.pipeline import auto_apply_registry_suggestion


def _read_audit_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# suggestion is None
# ---------------------------------------------------------------------------

def test_none_suggestion_returns_skip(tmp_path):
    result = auto_apply_registry_suggestion(None, {})
    assert result == {"action": "skip", "reason": "parser_failed"}


def test_none_suggestion_audit_record(tmp_path):
    with AuditLog("run_test_none", traces_dir=tmp_path) as audit:
        auto_apply_registry_suggestion(None, {"filename": "test.pdf"}, audit=audit)
    lines = _read_audit_lines(audit.path)
    assert len(lines) == 1
    record = lines[0]
    assert record["action"] == "reject_missing"
    assert record["phase"] == "registry_update"
    assert record["inputs"]["suggestion"] is None
    assert record["inputs"]["document_identity"] == {"filename": "test.pdf"}


# ---------------------------------------------------------------------------
# unrecognized action
# ---------------------------------------------------------------------------

def test_bad_action_returns_skip(tmp_path):
    result = auto_apply_registry_suggestion({"action": "nonsense"}, {})
    assert result == {"action": "skip", "reason": "unrecognized_action"}


def test_bad_action_audit_record(tmp_path):
    suggestion = {"action": "nonsense"}
    with AuditLog("run_test_bad_action", traces_dir=tmp_path) as audit:
        auto_apply_registry_suggestion(suggestion, {}, audit=audit)
    lines = _read_audit_lines(audit.path)
    assert len(lines) == 1
    assert lines[0]["action"] == "reject_bad_action"
    assert lines[0]["inputs"]["suggestion"] == suggestion


# ---------------------------------------------------------------------------
# upsert with no label
# ---------------------------------------------------------------------------

def test_upsert_no_label_returns_skip(tmp_path):
    result = auto_apply_registry_suggestion({"action": "upsert"}, {})
    assert result == {"action": "skip", "reason": "missing_label"}


def test_upsert_no_label_audit_record(tmp_path):
    suggestion = {"action": "upsert"}
    with AuditLog("run_test_no_label", traces_dir=tmp_path) as audit:
        auto_apply_registry_suggestion(suggestion, {}, audit=audit)
    lines = _read_audit_lines(audit.path)
    assert len(lines) == 1
    assert lines[0]["action"] == "reject_missing_label"


# ---------------------------------------------------------------------------
# valid upsert
# ---------------------------------------------------------------------------

def test_valid_upsert_returns_unchanged(tmp_path):
    suggestion = {"action": "upsert", "label": "debian", "aliases": ["apt"]}
    result = auto_apply_registry_suggestion(suggestion, {"stem": "debian_guide"})
    assert result is suggestion


def test_valid_upsert_audit_record(tmp_path):
    suggestion = {"action": "upsert", "label": "debian", "aliases": ["apt"]}
    doc_identity = {"stem": "debian_guide"}
    with AuditLog("run_test_upsert", traces_dir=tmp_path) as audit:
        auto_apply_registry_suggestion(suggestion, doc_identity, audit=audit)
    lines = _read_audit_lines(audit.path)
    assert len(lines) == 1
    record = lines[0]
    assert record["action"] == "accept_upsert"
    assert record["chosen"] == suggestion
    assert record["inputs"]["document_identity"] == doc_identity


# ---------------------------------------------------------------------------
# skip action
# ---------------------------------------------------------------------------

def test_skip_suggestion_returns_unchanged(tmp_path):
    suggestion = {"action": "skip", "reason": "already_present"}
    result = auto_apply_registry_suggestion(suggestion, {})
    assert result is suggestion


def test_skip_suggestion_audit_record(tmp_path):
    suggestion = {"action": "skip", "reason": "already_present"}
    with AuditLog("run_test_skip", traces_dir=tmp_path) as audit:
        auto_apply_registry_suggestion(suggestion, {}, audit=audit)
    lines = _read_audit_lines(audit.path)
    assert len(lines) == 1
    assert lines[0]["action"] == "accept_skip"
    assert lines[0]["chosen"] == suggestion


# ---------------------------------------------------------------------------
# audit=None must not crash
# ---------------------------------------------------------------------------

def test_no_audit_no_crash(tmp_path):
    # Passes audit=None (default) — must not raise and must not create any file.
    result = auto_apply_registry_suggestion(None, {}, audit=None)
    assert result["action"] == "skip"
    # No JSONL files should exist in tmp_path.
    jsonl_files = list(tmp_path.glob("*.jsonl"))
    assert jsonl_files == []
