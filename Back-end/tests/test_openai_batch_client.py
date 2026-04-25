"""Tests for providers.openai_batch.

All tests use a FakeOpenAIClient — the real openai SDK is never imported.
"""
import sys
import os
import types
import time

# ---------------------------------------------------------------------------
# Ensure app is importable without real SDK
# ---------------------------------------------------------------------------

_APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

# Stub openai so the module-level import in openai_batch.py resolves to None
# (same pattern as the production lazy-import guard).
if "openai" not in sys.modules:
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = None
    sys.modules["openai"] = fake_openai

import providers.openai_batch as batch_mod
from providers.openai_batch import (
    OpenAIBatchClient,
    BatchSubmission,
    BatchStatus,
    TERMINAL_STATUSES,
    is_terminal,
    _is_rate_limit_error,
    _extract_retry_delay_seconds,
)

# ---------------------------------------------------------------------------
# Fake OpenAI client helpers
# ---------------------------------------------------------------------------

class _FakeFile:
    def __init__(self, file_id="file-abc123"):
        self.id = file_id


class _FakeFiles:
    def __init__(self, file_id="file-abc123", content_bytes=b"line1\nline2\n",
                 raises_sequence=None):
        self._file_id = file_id
        self._content_bytes = content_bytes
        self._raises_sequence = list(raises_sequence or [])
        self.create_calls = []
        self.content_calls = []

    def create(self, *, file, purpose):
        self.create_calls.append({"file": file, "purpose": purpose})
        if self._raises_sequence:
            exc = self._raises_sequence.pop(0)
            if exc is not None:
                raise exc
        return _FakeFile(self._file_id)

    def content(self, file_id):
        self.content_calls.append(file_id)
        if self._raises_sequence:
            exc = self._raises_sequence.pop(0)
            if exc is not None:
                raise exc
        return _FakeContentResponse(self._content_bytes)


class _FakeContentResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeContentResponseIterBytes:
    """Simulates a response that only supports iter_bytes(), not read()."""
    def __init__(self, data: bytes):
        self._data = data

    def iter_bytes(self):
        yield self._data


class _FakeBatchResp:
    def __init__(self, batch_id, status, input_file_id="file-abc",
                 output_file_id=None, error_file_id=None, request_counts=None,
                 completed_at=None, created_at=None):
        self.id = batch_id
        self.status = status
        self.input_file_id = input_file_id
        self.output_file_id = output_file_id
        self.error_file_id = error_file_id
        self.request_counts = request_counts
        self.completed_at = completed_at
        self.created_at = created_at


class _RequestCountsAttrs:
    """Object-attribute style request_counts."""
    def __init__(self, total, completed, failed):
        self.total = total
        self.completed = completed
        self.failed = failed


class _FakeBatches:
    def __init__(self, batch_resp=None):
        self._batch_resp = batch_resp
        self.create_calls = []
        self.retrieve_calls = []

    def create(self, *, input_file_id, endpoint, completion_window, metadata):
        self.create_calls.append({
            "input_file_id": input_file_id,
            "endpoint": endpoint,
            "completion_window": completion_window,
            "metadata": metadata,
        })
        return self._batch_resp

    def retrieve(self, batch_id):
        self.retrieve_calls.append(batch_id)
        return self._batch_resp


class _FakeClient:
    def __init__(self, files=None, batches=None):
        self.files = files or _FakeFiles()
        self.batches = batches or _FakeBatches()


class _FakeRateLimitError(Exception):
    """Mimics an openai.RateLimitError with a 429 status code."""
    def __init__(self, msg="Rate limit exceeded"):
        super().__init__(msg)
        self.status_code = 429


class _FakeOtherError(Exception):
    pass


# ---------------------------------------------------------------------------
# Tests: is_terminal
# ---------------------------------------------------------------------------

class TestIsTerminal:
    def test_terminal_states_are_true(self):
        for s in ("completed", "failed", "expired", "cancelled"):
            assert is_terminal(s) is True, f"Expected {s!r} to be terminal"

    def test_non_terminal_states_are_false(self):
        for s in ("validating", "in_progress", "finalizing", "queued"):
            assert is_terminal(s) is False, f"Expected {s!r} to be non-terminal"

    def test_unknown_string_is_false(self):
        assert is_terminal("unknown_state") is False
        assert is_terminal("") is False

    def test_terminal_statuses_frozenset(self):
        assert isinstance(TERMINAL_STATUSES, frozenset)
        assert "completed" in TERMINAL_STATUSES
        assert "in_progress" not in TERMINAL_STATUSES


# ---------------------------------------------------------------------------
# Tests: _is_rate_limit_error
# ---------------------------------------------------------------------------

class TestIsRateLimitError:
    def test_status_code_429(self):
        exc = _FakeRateLimitError()
        assert _is_rate_limit_error(exc) is True

    def test_response_status_code_429(self):
        class _Resp:
            status_code = 429

        class _Exc(Exception):
            response = _Resp()

        assert _is_rate_limit_error(_Exc()) is True

    def test_code_attribute_rate_limit_exceeded(self):
        class _Exc(Exception):
            code = "rate_limit_exceeded"
            status_code = None

        assert _is_rate_limit_error(_Exc()) is True

    def test_message_contains_rate_limit(self):
        exc = Exception("openai: rate limit hit, slow down")
        assert _is_rate_limit_error(exc) is True

    def test_message_contains_429(self):
        exc = Exception("Error 429 from server")
        assert _is_rate_limit_error(exc) is True

    def test_normal_error_is_false(self):
        exc = ValueError("something broke")
        assert _is_rate_limit_error(exc) is False


# ---------------------------------------------------------------------------
# Tests: upload_jsonl
# ---------------------------------------------------------------------------

class TestUploadJsonl:
    def test_basic_upload(self, tmp_path):
        jsonl = tmp_path / "input.jsonl"
        jsonl.write_text('{"custom_id":"1","method":"POST","url":"/v1/responses"}\n')

        fake_files = _FakeFiles(file_id="file-xyz")
        client = OpenAIBatchClient(client=_FakeClient(files=fake_files))

        result = client.upload_jsonl(jsonl)

        assert result == "file-xyz"
        assert len(fake_files.create_calls) == 1
        assert fake_files.create_calls[0]["purpose"] == "batch"

    def test_upload_returns_file_id(self, tmp_path):
        jsonl = tmp_path / "data.jsonl"
        jsonl.write_bytes(b"{}\n")

        fake_files = _FakeFiles(file_id="file-expected-id")
        client = OpenAIBatchClient(client=_FakeClient(files=fake_files))

        assert client.upload_jsonl(jsonl) == "file-expected-id"


# ---------------------------------------------------------------------------
# Tests: submit_batch
# ---------------------------------------------------------------------------

class TestSubmitBatch:
    def _make_batch_resp(self, **kwargs):
        defaults = dict(
            batch_id="batch-001",
            status="validating",
            input_file_id="file-abc",
            created_at=1700000000,
        )
        defaults.update(kwargs)
        return _FakeBatchResp(**defaults)

    def test_default_params(self):
        resp = self._make_batch_resp()
        fake_batches = _FakeBatches(batch_resp=resp)
        client = OpenAIBatchClient(client=_FakeClient(batches=fake_batches))

        submission = client.submit_batch("file-abc")

        assert len(fake_batches.create_calls) == 1
        call = fake_batches.create_calls[0]
        assert call["input_file_id"] == "file-abc"
        assert call["endpoint"] == "/v1/responses"
        assert call["completion_window"] == "24h"
        assert call["metadata"] == {}

        assert isinstance(submission, BatchSubmission)
        assert submission.batch_id == "batch-001"
        assert submission.input_file_id == "file-abc"
        assert submission.status == "validating"
        assert submission.created_at == 1700000000

    def test_custom_params(self):
        resp = self._make_batch_resp(batch_id="batch-custom", status="in_progress")
        fake_batches = _FakeBatches(batch_resp=resp)
        client = OpenAIBatchClient(client=_FakeClient(batches=fake_batches))

        submission = client.submit_batch(
            "file-custom",
            endpoint="/v1/chat/completions",
            completion_window="1h",
            metadata={"project": "mass-ingest", "version": "1"},
        )

        call = fake_batches.create_calls[0]
        assert call["endpoint"] == "/v1/chat/completions"
        assert call["completion_window"] == "1h"
        assert call["metadata"] == {"project": "mass-ingest", "version": "1"}
        assert submission.batch_id == "batch-custom"


# ---------------------------------------------------------------------------
# Tests: get_status
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_request_counts_as_attributes(self):
        rc = _RequestCountsAttrs(total=100, completed=80, failed=5)
        resp = _FakeBatchResp(
            batch_id="batch-s1", status="in_progress",
            request_counts=rc, output_file_id="file-out", error_file_id="file-err",
            completed_at=None,
        )
        fake_batches = _FakeBatches(batch_resp=resp)
        client = OpenAIBatchClient(client=_FakeClient(batches=fake_batches))

        status = client.get_status("batch-s1")

        assert isinstance(status, BatchStatus)
        assert status.batch_id == "batch-s1"
        assert status.status == "in_progress"
        assert status.output_file_id == "file-out"
        assert status.error_file_id == "file-err"
        assert status.request_counts == {"total": 100, "completed": 80, "failed": 5}
        assert status.completed_at is None

    def test_request_counts_as_dict(self):
        rc_dict = {"total": 50, "completed": 50, "failed": 0}
        resp = _FakeBatchResp(
            batch_id="batch-s2", status="completed",
            request_counts=rc_dict, completed_at=1700001000,
        )
        fake_batches = _FakeBatches(batch_resp=resp)
        client = OpenAIBatchClient(client=_FakeClient(batches=fake_batches))

        status = client.get_status("batch-s2")

        assert status.request_counts == {"total": 50, "completed": 50, "failed": 0}
        assert status.completed_at == 1700001000

    def test_request_counts_none_defaults_to_zeros(self):
        resp = _FakeBatchResp(
            batch_id="batch-s3", status="validating",
            request_counts=None,
        )
        fake_batches = _FakeBatches(batch_resp=resp)
        client = OpenAIBatchClient(client=_FakeClient(batches=fake_batches))

        status = client.get_status("batch-s3")
        assert status.request_counts == {"total": 0, "completed": 0, "failed": 0}


# ---------------------------------------------------------------------------
# Tests: download_output
# ---------------------------------------------------------------------------

class TestDownloadOutput:
    def test_download_via_read(self, tmp_path):
        content = b"result1\nresult2\n"
        fake_files = _FakeFiles(content_bytes=content)
        client = OpenAIBatchClient(client=_FakeClient(files=fake_files))

        dest = tmp_path / "output.jsonl"
        returned = client.download_output("file-out-1", dest)

        assert returned == dest
        assert dest.read_bytes() == content
        assert "file-out-1" in fake_files.content_calls

    def test_download_via_iter_bytes(self, tmp_path, monkeypatch):
        """When response only has iter_bytes(), not read(), content is assembled correctly."""
        content = b"chunk1chunk2"

        class _FakeFilesIterOnly(_FakeFiles):
            def content(self, file_id):
                self.content_calls.append(file_id)
                return _FakeContentResponseIterBytes(content)

        fake_files = _FakeFilesIterOnly()
        client = OpenAIBatchClient(client=_FakeClient(files=fake_files))

        dest = tmp_path / "iter_output.jsonl"
        client.download_output("file-iter", dest)

        assert dest.read_bytes() == content

    def test_download_writes_binary_content(self, tmp_path):
        binary = bytes(range(256))
        fake_files = _FakeFiles(content_bytes=binary)
        client = OpenAIBatchClient(client=_FakeClient(files=fake_files))

        dest = tmp_path / "binary.bin"
        client.download_output("file-bin", dest)

        assert dest.read_bytes() == binary


# ---------------------------------------------------------------------------
# Tests: retry behaviour
# ---------------------------------------------------------------------------

class TestRetry:
    def test_upload_retries_on_429_then_succeeds(self, monkeypatch):
        sleep_calls = []
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        # Raise 429 twice, then succeed
        fake_files = _FakeFiles(
            file_id="file-retry-ok",
            raises_sequence=[_FakeRateLimitError(), _FakeRateLimitError(), None],
        )
        client = OpenAIBatchClient(client=_FakeClient(files=fake_files))

        jsonl = "/dev/null"  # We'll patch open so it doesn't matter for retry test
        # We need a real (empty) file to open
        import tempfile, pathlib
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            f.write(b"{}\n")
            tmp = pathlib.Path(f.name)

        try:
            result = client.upload_jsonl(tmp)
        finally:
            tmp.unlink(missing_ok=True)

        assert result == "file-retry-ok"
        assert len(sleep_calls) >= 2, f"Expected at least 2 sleeps, got {len(sleep_calls)}"

    def test_upload_non_rate_limit_propagates(self, tmp_path):
        jsonl = tmp_path / "x.jsonl"
        jsonl.write_bytes(b"{}\n")

        fake_files = _FakeFiles(
            raises_sequence=[_FakeOtherError("boom")]
        )
        client = OpenAIBatchClient(client=_FakeClient(files=fake_files))

        try:
            client.upload_jsonl(jsonl)
            assert False, "Expected exception to propagate"
        except _FakeOtherError as exc:
            assert "boom" in str(exc)

    def test_retry_exhaustion_raises(self, monkeypatch, tmp_path):
        """After max_retries attempts, the last rate-limit error is re-raised."""
        monkeypatch.setattr(time, "sleep", lambda s: None)

        # Always raise 429
        always_429 = [_FakeRateLimitError() for _ in range(20)]
        fake_files = _FakeFiles(raises_sequence=always_429)
        client = OpenAIBatchClient(client=_FakeClient(files=fake_files))

        jsonl = tmp_path / "x.jsonl"
        jsonl.write_bytes(b"{}\n")

        try:
            client.upload_jsonl(jsonl)
            assert False, "Expected _FakeRateLimitError to be raised"
        except _FakeRateLimitError:
            pass

    def test_download_retries_content_call(self, tmp_path, monkeypatch):
        sleep_calls = []
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        # Raise 429 once on content(), then succeed
        fake_files = _FakeFiles(
            content_bytes=b"data",
            raises_sequence=[_FakeRateLimitError(), None],
        )
        client = OpenAIBatchClient(client=_FakeClient(files=fake_files))

        dest = tmp_path / "out.jsonl"
        client.download_output("file-dl", dest)

        assert dest.read_bytes() == b"data"
        assert len(sleep_calls) >= 1


# ---------------------------------------------------------------------------
# Tests: _extract_retry_delay_seconds
# ---------------------------------------------------------------------------

class TestExtractRetryDelay:
    def test_parses_ms_suffix(self):
        exc = Exception("please try again in 500ms")
        delay = _extract_retry_delay_seconds(exc, 1)
        # 500ms = 0.5s → clamped to 1.0
        assert delay == 1.0

    def test_parses_s_suffix(self):
        exc = Exception("please try again in 5s")
        delay = _extract_retry_delay_seconds(exc, 1)
        assert delay == 5.0

    def test_exponential_backoff_first_attempt(self):
        exc = Exception("no delay hint")
        delay = _extract_retry_delay_seconds(exc, 1)
        assert delay == 1.0

    def test_exponential_backoff_caps_at_80(self):
        exc = Exception("no delay hint")
        delay = _extract_retry_delay_seconds(exc, 20)
        assert delay == 80.0
