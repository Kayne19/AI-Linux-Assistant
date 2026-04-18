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
    build_web_search_tool,
    conversation_request_payload,
    extract_response_citations,
    extract_response_source_metadata,
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


class _FakeConversationsAPI:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id="conv_123", object="conversation")


class _FakeOpenAI:
    instances: list["_FakeOpenAI"] = []
    queued_responses: list[list[object]] = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.responses = _FakeResponsesAPI(self.queued_responses.pop(0))
        self.conversations = _FakeConversationsAPI()
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


def test_create_response_supports_conversation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [[_response(output_text="done")]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)

    client = OpenAIResponsesClient(OpenAIResponsesClientConfig(model="gpt-5.4-mini", api_key="test-key"))

    client.create_response(
        instructions="Continue.",
        input_items="hello",
        conversation_id="conv_123",
    )

    fake_client = _FakeOpenAI.instances[-1]
    assert fake_client.responses.calls == [
        {
            "model": "gpt-5.4-mini",
            "instructions": "Continue.",
            "input": [{"role": "user", "content": "hello"}],
            "conversation": {"id": "conv_123"},
        }
    ]


def test_create_response_supports_include_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [[_response(output_text="done")]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)

    client = OpenAIResponsesClient(OpenAIResponsesClientConfig(model="gpt-5.4-mini", api_key="test-key"))

    client.create_response(
        instructions="Continue.",
        input_items="hello",
        tools=[{"type": "web_search"}],
        include=["web_search_call.action.sources"],
    )

    fake_client = _FakeOpenAI.instances[-1]
    assert fake_client.responses.calls == [
        {
            "model": "gpt-5.4-mini",
            "instructions": "Continue.",
            "input": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "web_search"}],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "include": ["web_search_call.action.sources"],
        }
    ]


def test_request_json_supports_conversation_id_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [[_response(output_text='{"ok": true}')]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)

    client = OpenAIResponsesClient(OpenAIResponsesClientConfig(model="gpt-5.4-mini", api_key="test-key"))

    payload = client.request_json(
        instructions="Return JSON.",
        user_input="hello",
        schema_name="judge_result",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
        conversation_id="conv_abc",
    )

    assert payload == {"ok": True}
    fake_client = _FakeOpenAI.instances[-1]
    assert fake_client.responses.calls[0]["conversation"] == {"id": "conv_abc"}


def test_conversation_request_payload_wraps_identifier() -> None:
    assert conversation_request_payload("conv_123") == {"conversation": {"id": "conv_123"}}


def test_create_conversation_normalizes_seed_items(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [[]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)

    client = OpenAIResponsesClient(OpenAIResponsesClientConfig(model="gpt-5.4-mini", api_key="test-key"))

    conversation = client.create_conversation(
        items=[
            {"role": "system", "content": "Prior context"},
            {"role": "assistant", "content": "Previous reply"},
        ],
        metadata={"topic": "benchmark"},
    )

    assert conversation.id == "conv_123"
    fake_client = _FakeOpenAI.instances[-1]
    assert fake_client.conversations.calls == [
        {
            "items": [
                {"type": "message", "role": "system", "content": "Prior context"},
                {"type": "message", "role": "assistant", "content": "Previous reply"},
            ],
            "metadata": {"topic": "benchmark"},
        }
    ]


def test_build_web_search_tool_includes_domains_location_and_passthrough() -> None:
    tool = build_web_search_tool(
        allowed_domains=("example.com", "docs.example.com"),
        user_location={
            "type": "approximate",
            "country": "US",
            "region": "CA",
            "city": "San Francisco",
            "timezone": "America/Los_Angeles",
        },
        passthrough_config={"search_context_size": "high", "allow_related_results": False},
    )

    assert tool == {
        "type": "web_search",
        "filters": {"allowed_domains": ["example.com", "docs.example.com"]},
        "user_location": {
            "type": "approximate",
            "country": "US",
            "region": "CA",
            "city": "San Francisco",
            "timezone": "America/Los_Angeles",
        },
        "search_context_size": "high",
        "allow_related_results": False,
    }


def test_extract_response_citations_and_source_metadata() -> None:
    response = _response(
        output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(
                        type="output_text",
                        text="See the docs.",
                        annotations=[
                            SimpleNamespace(
                                type="url_citation",
                                url="https://example.com/docs",
                                title="Example Docs",
                                start_index=4,
                                end_index=8,
                            ),
                            SimpleNamespace(
                                type="file_citation",
                                file_id="file_123",
                                filename="manual.pdf",
                                start_index=0,
                                end_index=3,
                            ),
                        ],
                    )
                ],
            )
        ]
    )

    citations = extract_response_citations(response)
    sources = extract_response_source_metadata(response)

    assert citations == (
        {
            "type": "url_citation",
            "start_index": 4,
            "end_index": 8,
            "url": "https://example.com/docs",
            "title": "Example Docs",
            "text": "See the docs.",
        },
        {
            "type": "file_citation",
            "start_index": 0,
            "end_index": 3,
            "file_id": "file_123",
            "filename": "manual.pdf",
            "text": "See the docs.",
        },
    )
    assert sources == (
        {
            "type": "url_citation",
            "source_id": "https://example.com/docs",
            "title": "Example Docs",
            "url": "https://example.com/docs",
        },
        {
            "type": "file_citation",
            "source_id": "file_123",
            "filename": "manual.pdf",
        },
    )


def test_extract_response_source_metadata_includes_web_search_sources() -> None:
    response = _response(
        output=[
            SimpleNamespace(
                type="web_search_call",
                action=SimpleNamespace(
                    sources=[
                        SimpleNamespace(
                            type="url",
                            title="OpenAI Docs",
                            url="https://platform.openai.com/docs",
                        ),
                        SimpleNamespace(
                            type="url",
                            title="OpenAI Docs",
                            url="https://platform.openai.com/docs",
                        ),
                    ]
                ),
            )
        ]
    )

    assert extract_response_source_metadata(response) == (
        {
            "type": "url",
            "source_id": "https://platform.openai.com/docs",
            "title": "OpenAI Docs",
            "url": "https://platform.openai.com/docs",
        },
    )


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


def test_request_json_raises_on_failed_response_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [
        [
            SimpleNamespace(
                id="resp_failed",
                status="failed",
                output_text="",
                output=[],
                incomplete_details=None,
                error=SimpleNamespace(message="quota exceeded"),
            )
        ]
    ]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)
    client = OpenAIResponsesClient(OpenAIResponsesClientConfig(model="gpt-5.4-mini", api_key="test-key"))

    with pytest.raises(RuntimeError, match="quota exceeded"):
        client.request_json(
            instructions="Return JSON.",
            user_input="hello",
            schema_name="judge_result",
            schema={"type": "object", "properties": {}, "additionalProperties": False},
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
