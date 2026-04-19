import json
import re
import threading
import pytest
from ingestion.audit import AuditLog

_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$")
_EXPECTED_KEYS = ["ts", "run_id", "doc", "phase", "action", "inputs", "chosen", "confidence", "rationale"]


def _read_lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_record_produces_valid_jsonl(tmp_path):
    with AuditLog("run1", traces_dir=tmp_path) as al:
        al.record(doc="doc.pdf", phase="registry_update", action="upsert_domain")
    lines = _read_lines(al.path)
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert isinstance(obj, dict)


def test_record_key_order(tmp_path):
    with AuditLog("run1", traces_dir=tmp_path) as al:
        al.record(doc="doc.pdf", phase="identity_resolution", action="create_identity")
    obj = json.loads(_read_lines(al.path)[0])
    assert list(obj.keys()) == _EXPECTED_KEYS


def test_ts_is_iso8601_utc(tmp_path):
    with AuditLog("run1", traces_dir=tmp_path) as al:
        al.record(doc="doc.pdf", phase="p", action="a")
    obj = json.loads(_read_lines(al.path)[0])
    assert _ISO_UTC_RE.match(obj["ts"]), f"unexpected ts format: {obj['ts']}"


def test_optional_fields_default_null(tmp_path):
    with AuditLog("run1", traces_dir=tmp_path) as al:
        al.record(doc="doc.pdf", phase="p", action="a")
    obj = json.loads(_read_lines(al.path)[0])
    assert obj["inputs"] is None
    assert obj["chosen"] is None
    assert obj["confidence"] is None
    assert obj["rationale"] is None


def test_two_records_append(tmp_path):
    with AuditLog("run1", traces_dir=tmp_path) as al:
        al.record(doc="a.pdf", phase="p", action="a1")
        al.record(doc="b.pdf", phase="p", action="a2")
    lines = _read_lines(al.path)
    assert len(lines) == 2
    assert json.loads(lines[0])["doc"] == "a.pdf"
    assert json.loads(lines[1])["doc"] == "b.pdf"


def test_reopen_same_run_id_appends(tmp_path):
    al1 = AuditLog("run1", traces_dir=tmp_path)
    al1.record(doc="first.pdf", phase="p", action="a")
    al1.close()

    al2 = AuditLog("run1", traces_dir=tmp_path)
    al2.record(doc="second.pdf", phase="p", action="b")
    al2.close()

    lines = _read_lines(al2.path)
    assert len(lines) == 2
    assert json.loads(lines[0])["doc"] == "first.pdf"
    assert json.loads(lines[1])["doc"] == "second.pdf"


def test_context_manager_closes_file(tmp_path):
    with AuditLog("run1", traces_dir=tmp_path) as al:
        al.record(doc="doc.pdf", phase="p", action="a")
    assert al._fh.closed


def test_concurrent_records_no_interleaving(tmp_path):
    n_threads = 8
    records_per_thread = 25
    al = AuditLog("run1", traces_dir=tmp_path)

    def worker(tid):
        for i in range(records_per_thread):
            al.record(
                doc=f"doc_{tid}_{i}.pdf",
                phase="p",
                action="a",
                rationale=f"thread {tid} record {i}",
            )

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    al.close()

    lines = _read_lines(al.path)
    assert len(lines) == n_threads * records_per_thread
    for line in lines:
        obj = json.loads(line)
        assert list(obj.keys()) == _EXPECTED_KEYS


def test_custom_traces_dir(tmp_path):
    custom = tmp_path / "my_traces"
    with AuditLog("run42", traces_dir=custom) as al:
        al.record(doc="d.pdf", phase="p", action="a")
    assert al.path == custom / "audit_run42.jsonl"
    assert al.path.exists()


def test_optional_fields_written_when_provided(tmp_path):
    with AuditLog("run1", traces_dir=tmp_path) as al:
        al.record(
            doc="d.pdf",
            phase="p",
            action="a",
            inputs={"k": "v"},
            chosen={"x": 1},
            confidence=0.95,
            rationale="high confidence",
        )
    obj = json.loads(_read_lines(al.path)[0])
    assert obj["inputs"] == {"k": "v"}
    assert obj["chosen"] == {"x": 1}
    assert obj["confidence"] == pytest.approx(0.95)
    assert obj["rationale"] == "high confidence"
