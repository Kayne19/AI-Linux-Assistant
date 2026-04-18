from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.openai_responses import (
    OpenAIResponsesClient,
    OpenAIResponsesClientConfig,
    extract_function_calls,
    function_call_output_item,
)


class _FakeResponsesAPI:
    def __init__(self, responses: list[object]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeOpenAI:
    instances: list["_FakeOpenAI"] = []
    queued_responses: list[list[object]] = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.responses = _FakeResponsesAPI(self.queued_responses.pop(0))
        self.__class__.instances.append(self)


def _response(
    *,
    response_id: str = "resp_123",
    status: str = "completed",
    output_text: str = "",
    output: list[object] | None = None,
    incomplete_reason: str | None = None,
):
    return SimpleNamespace(
        id=response_id,
        status=status,
        output_text=output_text,
        output=output or [],
        incomplete_details=(
            SimpleNamespace(reason=incomplete_reason)
            if incomplete_reason is not None
            else None
        ),
    )


def test_request_json_uses_responses_structured_outputs_and_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [[_response(output_text='{"ok": true}')]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)

    client = OpenAIResponsesClient(
        OpenAIResponsesClientConfig(
            model="gpt-5.4-mini",
            api_key="test-key",
            request_timeout_seconds=12.5,
            base_url=None,
            max_output_tokens=321,
            reasoning_effort="medium",
        )
    )

    payload = client.request_json(
        instructions="Return a JSON object.",
        user_input="hello world",
        schema_name="planner_result",
        schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        schema_description="Structured planner result.",
    )

    assert payload == {"ok": True}
    fake_client = _FakeOpenAI.instances[-1]
    assert fake_client.init_kwargs == {
        "api_key": "test-key",
        "timeout": 12.5,
    }
    assert fake_client.responses.calls == [
        {
            "model": "gpt-5.4-mini",
            "instructions": "Return a JSON object.",
            "input": [{"role": "user", "content": "hello world"}],
            "max_output_tokens": 321,
            "reasoning": {"effort": "medium"},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "planner_result",
                    "description": "Structured planner result.",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                        "required": ["ok"],
                        "additionalProperties": False,
                    },
                }
            },
        }
    ]


def test_request_json_omits_timeout_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [[_response(output_text='{"ok": true}')]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)

    client = OpenAIResponsesClient(
        OpenAIResponsesClientConfig(
            model="gpt-5.4-mini",
            api_key="test-key",
            request_timeout_seconds=None,
        )
    )

    payload = client.request_json(
        instructions="Return a JSON object.",
        user_input="hello world",
        schema_name="planner_result",
        schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        schema_description="Structured planner result.",
    )

    assert payload == {"ok": True}
    fake_client = _FakeOpenAI.instances[-1]
    assert fake_client.init_kwargs == {
        "api_key": "test-key",
    }


def test_request_json_supports_tools_without_breaking_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [[_response(output_text='{"ok": true}')]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)

    client = OpenAIResponsesClient(
        OpenAIResponsesClientConfig(
            model="gpt-5.4-mini",
            api_key="test-key",
            request_timeout_seconds=12.5,
            base_url=None,
            max_output_tokens=321,
            reasoning_effort="medium",
        )
    )

    payload = client.request_json(
        instructions="Return a JSON object.",
        user_input="hello world",
        schema_name="planner_result",
        schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        schema_description="Structured planner result.",
        tools=[{"type": "web_search"}],
    )

    assert payload == {"ok": True}
    fake_client = _FakeOpenAI.instances[-1]
    assert fake_client.responses.calls == [
        {
            "model": "gpt-5.4-mini",
            "instructions": "Return a JSON object.",
            "input": [{"role": "user", "content": "hello world"}],
            "max_output_tokens": 321,
            "reasoning": {"effort": "medium"},
            "tools": [{"type": "web_search"}],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "planner_result",
                    "description": "Structured planner result.",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                        "required": ["ok"],
                        "additionalProperties": False,
                    },
                }
            },
        }
    ]


def test_create_response_supports_previous_response_id_and_tool_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [[_response(output_text="done")]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)

    client = OpenAIResponsesClient(
        OpenAIResponsesClientConfig(
            model="gpt-5.4-mini",
            api_key="test-key",
            base_url="https://api.example.invalid/v1",
        )
    )

    response = client.create_response(
        instructions="Continue.",
        input_items=[function_call_output_item("call_123", {"exit_code": 0})],
        previous_response_id="resp_previous",
    )

    assert response.output_text == "done"
    fake_client = _FakeOpenAI.instances[-1]
    assert fake_client.init_kwargs == {
        "api_key": "test-key",
        "base_url": "https://api.example.invalid/v1",
    }
    assert fake_client.responses.calls == [
        {
            "model": "gpt-5.4-mini",
            "instructions": "Continue.",
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": "call_123",
                    "output": '{"exit_code": 0}',
                }
            ],
            "previous_response_id": "resp_previous",
        }
    ]


def test_request_json_raises_on_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [
        [
            _response(
                response_id="resp_refused",
                output=[
                    SimpleNamespace(
                        type="message",
                        content=[SimpleNamespace(type="refusal", refusal="cannot comply")],
                    )
                ],
            )
        ]
    ]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)
    client = OpenAIResponsesClient(OpenAIResponsesClientConfig(model="gpt-5.4-mini", api_key="test-key"))

    with pytest.raises(RuntimeError, match="resp_refused"):
        client.request_json(
            instructions="Return JSON.",
            user_input="bad request",
            schema_name="judge_result",
            schema={"type": "object", "properties": {}, "additionalProperties": False},
        )


def test_request_json_raises_on_incomplete_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [
        [_response(response_id="resp_incomplete", status="incomplete", incomplete_reason="max_output_tokens")]
    ]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)
    client = OpenAIResponsesClient(OpenAIResponsesClientConfig(model="gpt-5.4-mini", api_key="test-key"))

    with pytest.raises(RuntimeError, match="max_output_tokens"):
        client.request_json(
            instructions="Return JSON.",
            user_input="hello",
            schema_name="judge_result",
            schema={"type": "object", "properties": {}, "additionalProperties": False},
        )


def test_request_json_rejects_openai_strict_schema_without_closed_nested_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [[_response(output_text='{"ok": true}')]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)
    client = OpenAIResponsesClient(OpenAIResponsesClientConfig(model="gpt-5.4-mini", api_key="test-key"))

    with pytest.raises(ValueError, match="additionalProperties=false"):
        client.request_json(
            instructions="Return JSON.",
            user_input="hello",
            schema_name="judge_result",
            schema={
                "type": "object",
                "properties": {
                    "scores": {
                        "type": "object",
                        "properties": {"diagnosis": {"type": "integer"}},
                        "required": ["diagnosis"],
                    }
                },
                "required": ["scores"],
                "additionalProperties": False,
            },
        )


def test_request_json_rejects_openai_strict_schema_when_required_fields_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [[_response(output_text='{"ok": true}')]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)
    client = OpenAIResponsesClient(OpenAIResponsesClientConfig(model="gpt-5.4-mini", api_key="test-key"))

    with pytest.raises(ValueError, match="must declare every property as required"):
        client.request_json(
            instructions="Return JSON.",
            user_input="hello",
            schema_name="judge_result",
            schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "scores": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        )


def test_extract_function_calls_parses_stringified_arguments() -> None:
    response = _response(
        output=[
            SimpleNamespace(type="message", content=[]),
            SimpleNamespace(
                type="function_call",
                name="run_command",
                call_id="call_456",
                arguments='{"command":"uname -a"}',
            ),
        ]
    )

    tool_calls = extract_function_calls(response)

    assert len(tool_calls) == 1
    assert tool_calls[0].name == "run_command"
    assert tool_calls[0].call_id == "call_456"
    assert tool_calls[0].arguments == {"command": "uname -a"}
