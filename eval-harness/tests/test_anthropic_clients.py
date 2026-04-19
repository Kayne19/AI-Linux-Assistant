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

from eval_harness.anthropic_llm import AnthropicStructuredOutputClient, AnthropicStructuredOutputClientConfig
from eval_harness.orchestration.user_proxy_llm import UserProxyLLMClientConfig, UserProxyToolCall
from eval_harness.orchestration.user_proxy_llm_anthropic import AnthropicUserProxyLLMClient


class _FakeMessagesAPI:
    def __init__(self, responses: list[object]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeAnthropic:
    instances: list["_FakeAnthropic"] = []
    queued_responses: list[list[object]] = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.messages = _FakeMessagesAPI(self.queued_responses.pop(0))
        self.__class__.instances.append(self)


def test_anthropic_structured_output_uses_forced_tool_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAnthropic.instances.clear()
    _FakeAnthropic.queued_responses = [
        [
            SimpleNamespace(
                id="msg-1",
                stop_reason="tool_use",
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="toolu-1",
                        name="planner_result",
                        input={"ok": True},
                    )
                ],
            )
        ]
    ]
    monkeypatch.setattr("eval_harness.anthropic_llm.Anthropic", _FakeAnthropic)

    client = AnthropicStructuredOutputClient(
        AnthropicStructuredOutputClientConfig(
            model="claude-sonnet-4-20250514",
            api_key="test-key",
            base_url="https://anthropic.example.invalid",
            request_timeout_seconds=22.0,
            max_output_tokens=333,
        )
    )

    payload = client.request_json(
        instructions="Return structured JSON.",
        user_input="hello world",
        schema_name="planner_result",
        schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        schema_description="Planner result.",
    )

    assert payload == {"ok": True}
    fake_client = _FakeAnthropic.instances[-1]
    assert fake_client.init_kwargs == {
        "api_key": "test-key",
        "base_url": "https://anthropic.example.invalid",
        "timeout": 22.0,
    }
    assert fake_client.messages.calls == [
        {
            "model": "claude-sonnet-4-20250514",
            "system": "Return structured JSON.",
            "messages": [{"role": "user", "content": "hello world"}],
            "max_tokens": 333,
            "tools": [
                {
                    "name": "planner_result",
                    "description": "Planner result.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                        "required": ["ok"],
                        "additionalProperties": False,
                    },
                }
            ],
            "tool_choice": {"type": "tool", "name": "planner_result"},
        }
    ]


def test_anthropic_user_proxy_uses_native_tool_use(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAnthropic.instances.clear()
    _FakeAnthropic.queued_responses = [
        [
            SimpleNamespace(
                id="msg-1",
                stop_reason="tool_use",
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="toolu-1",
                        name="run_command",
                        input={"command": "ls"},
                    )
                ],
            ),
            SimpleNamespace(
                id="msg-2",
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="I ran it.")],
            ),
        ]
    ]
    monkeypatch.setattr("eval_harness.orchestration.user_proxy_llm_anthropic.Anthropic", _FakeAnthropic)

    client = AnthropicUserProxyLLMClient(
        UserProxyLLMClientConfig(
            model="claude-sonnet-4-20250514",
            api_key="test-key",
            request_timeout_seconds=15.0,
            max_output_tokens=444,
        )
    )

    # transcript=[("user", "nginx is down")] — leading proxy turn skipped.
    # Native history = [{"role": "user", "content": "Run ls"}].
    start = client.start_turn(
        system_prompt="You are a confused user.",
        transcript=[("user", "nginx is down")],
        assistant_reply="Run ls",
        tools=[
            {
                "type": "function",
                "name": "run_command",
                "description": "Run a command",
                "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
                "strict": True,
            }
        ],
    )

    assert start.response_id == "msg-1"
    assert start.tool_calls == (
        UserProxyToolCall(id="toolu-1", name="run_command", arguments={"command": "ls"}),
    )

    fake_client = _FakeAnthropic.instances[-1]
    # Native history: leading proxy turn skipped → single user message with current reply.
    assert fake_client.messages.calls[0]["messages"] == [
        {"role": "user", "content": "Run ls"}
    ]

    finish = client.continue_turn(
        system_prompt="You are a confused user.",
        previous_response_id=start.response_id,
        tool_outputs=[{"type": "function_call_output", "call_id": "toolu-1", "output": "$ ls\nfile.txt\n[exit 0]"}],
        tools=[
            {
                "type": "function",
                "name": "run_command",
                "description": "Run a command",
                "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
                "strict": True,
            }
        ],
    )

    assert finish.content == "I ran it."
    assert fake_client.messages.calls[0]["tools"][0]["input_schema"]["properties"]["command"]["type"] == "string"
    assert fake_client.messages.calls[1]["messages"][-1] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu-1",
                "content": "$ ls\nfile.txt\n[exit 0]",
            }
        ],
    }


def test_anthropic_user_proxy_review_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAnthropic.instances.clear()
    _FakeAnthropic.queued_responses = [
        [
            SimpleNamespace(
                id="review-1",
                stop_reason="end_turn",
                content=[
                    SimpleNamespace(
                        type="text",
                        text='{"final_reply":"I ran the command and got exit 1.","verdict":"accept","reason":"Terminal evidence only.","audit_json":{"reasoning":"Terminal evidence only.","edited_reply":true}}',
                    )
                ],
            )
        ]
    ]
    monkeypatch.setattr("eval_harness.orchestration.user_proxy_llm_anthropic.Anthropic", _FakeAnthropic)

    client = AnthropicUserProxyLLMClient(
        UserProxyLLMClientConfig(
            model="claude-sonnet-4-20250514",
            api_key="test-key",
            request_timeout_seconds=15.0,
            max_output_tokens=512,
        )
    )

    review = client.review_reply(
        system_prompt="You are a confused user.",
        transcript=[],
        subject_reply="What did the command print?",
        recent_memory_text="$ ls\nfile.txt\n[exit 0]",
        tool_outputs_text=["$ ls\nfile.txt\n[exit 0]"],
        tool_names_used_this_turn=["run_command"],
        draft_reply="You should run ls and let me know the output.",
    )

    assert review.final_reply == "I ran the command and got exit 1."
    assert review.verdict == "accept"
    assert review.reason == "Terminal evidence only."
    assert review.audit_json["reasoning"] == "Terminal evidence only."
    fake_client = _FakeAnthropic.instances[-1]
    call = fake_client.messages.calls[0]
    assert "review" in call["system"].lower() or "draft" in call["messages"][0]["content"].lower()
    assert "tool names used this turn" in call["messages"][0]["content"].lower()


def test_anthropic_user_proxy_retry_turn_includes_tool_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAnthropic.instances.clear()
    _FakeAnthropic.queued_responses = [
        [
            SimpleNamespace(
                id="retry-1",
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="I removed it and nginx -t is clean now.")],
            )
        ]
    ]
    monkeypatch.setattr("eval_harness.orchestration.user_proxy_llm_anthropic.Anthropic", _FakeAnthropic)

    client = AnthropicUserProxyLLMClient(
        UserProxyLLMClientConfig(
            model="claude-sonnet-4-20250514",
            api_key="test-key",
            request_timeout_seconds=15.0,
        )
    )

    response = client.retry_turn(
        system_prompt="You are a confused user.",
        transcript=[],
        assistant_reply="Remove that line and run nginx -t again.",
        tools=None,
        recent_memory_text="$ cat /etc/nginx/conf.d/zz-benchmark-bad.conf\ninvalid-directive on\n[exit 0]",
        draft_reply="It still looks broken.",
        review_verdict="retry_with_tools",
        review_reason="Use the tools now.",
        tool_names_used_this_turn=["read_file"],
        tool_outputs_text=["$ cat /etc/nginx/conf.d/zz-benchmark-bad.conf\ninvalid-directive on\n[exit 0]"],
    )

    assert response.content == "I removed it and nginx -t is clean now."
    fake_client = _FakeAnthropic.instances[-1]
    retry_message = fake_client.messages.calls[0]["messages"][-1]["content"]
    assert "[Terminal output this turn]" in retry_message
    assert "invalid-directive on" in retry_message
