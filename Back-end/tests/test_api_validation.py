import json

import pytest
from pydantic import ValidationError

from api import RunCreateRequest, RunResumeRequest, _parse_redis_stream_payload


def test_run_create_request_rejects_invalid_magi_mode():
    with pytest.raises(ValidationError):
        RunCreateRequest(content="hello", magi="invalid")


def test_run_create_request_rejects_oversized_content_and_client_request_id():
    with pytest.raises(ValidationError):
        RunCreateRequest(content="x" * 20001, client_request_id="ok")

    with pytest.raises(ValidationError):
        RunCreateRequest(content="hello", client_request_id="r" * 121)


def test_run_resume_request_rejects_invalid_input_kind():
    with pytest.raises(ValidationError):
        RunResumeRequest(input_text="extra", input_kind="note")


def test_parse_redis_stream_payload_returns_none_for_bad_json():
    assert _parse_redis_stream_payload("not-json", "run-1") is None
    assert _parse_redis_stream_payload(None, "run-1") is None


def test_parse_redis_stream_payload_returns_decoded_payload():
    payload = {"type": "done", "seq": 1, "message": "finished"}

    assert _parse_redis_stream_payload(json.dumps(payload), "run-1") == payload
