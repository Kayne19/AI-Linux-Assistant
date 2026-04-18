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

from eval_harness.google_genai_llm import GoogleGenAIStructuredOutputClient, GoogleGenAIStructuredOutputClientConfig
from eval_harness.orchestration.user_proxy_llm import UserProxyLLMClientConfig, UserProxyToolCall
from eval_harness.orchestration.user_proxy_llm_google import GoogleGenAIUserProxyLLMClient


class _FakeModelsAPI:
    def __init__(self, responses: list[object]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeGoogleClient:
    instances: list["_FakeGoogleClient"] = []
    queued_responses: list[list[object]] = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.models = _FakeModelsAPI(self.queued_responses.pop(0))
        self.__class__.instances.append(self)


def test_google_structured_output_uses_native_response_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeGoogleClient.instances.clear()
    _FakeGoogleClient.queued_responses = [[SimpleNamespace(text='{"ok": true}')]]
    monkeypatch.setattr("eval_harness.google_genai_llm.genai", SimpleNamespace(Client=_FakeGoogleClient))

    client = GoogleGenAIStructuredOutputClient(
        GoogleGenAIStructuredOutputClientConfig(
            model="gemini-2.5-pro",
            api_key="test-key",
            request_timeout_seconds=19.0,
            max_output_tokens=222,
        )
    )

    payload = client.request_json(
        instructions="Return JSON.",
        user_input="hello world",
        schema_name="judge_result",
        schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        schema_description="Judge result.",
    )

    assert payload == {"ok": True}
    fake_client = _FakeGoogleClient.instances[-1]
    assert fake_client.init_kwargs == {"api_key": "test-key", "http_options": {"timeout": 19.0}}
    assert fake_client.models.calls == [
        {
            "model": "gemini-2.5-pro",
            "contents": "hello world",
            "config": {
                "system_instruction": "Return JSON.",
                "response_mime_type": "application/json",
                "response_schema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
                "max_output_tokens": 222,
            },
        }
    ]


def test_google_user_proxy_uses_native_function_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeGoogleClient.instances.clear()
    _FakeGoogleClient.queued_responses = [
        [
            SimpleNamespace(
                text="",
                function_calls=[SimpleNamespace(id="fc-1", name="run_command", args={"command": "ls"})],
            ),
            SimpleNamespace(
                text="I ran it.",
                function_calls=[],
            ),
        ]
    ]
    monkeypatch.setattr("eval_harness.orchestration.user_proxy_llm_google.genai", SimpleNamespace(Client=_FakeGoogleClient))

    client = GoogleGenAIUserProxyLLMClient(
        UserProxyLLMClientConfig(
            model="gemini-2.5-flash",
            api_key="test-key",
            request_timeout_seconds=17.0,
            max_output_tokens=555,
        )
    )

    # transcript=[("user", "nginx is down")] — leading proxy turn skipped.
    # Native history = [{"role": "user", "parts": [{"text": "Run ls"}]}].
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

    assert start.tool_calls == (
        UserProxyToolCall(id="fc-1", name="run_command", arguments={"command": "ls"}),
    )

    fake_client = _FakeGoogleClient.instances[-1]
    # Native history: leading proxy turn skipped → single user turn.
    first_contents = fake_client.models.calls[0]["contents"]
    assert first_contents == [{"role": "user", "parts": [{"text": "Run ls"}]}]

    finish = client.continue_turn(
        system_prompt="You are a confused user.",
        previous_response_id=start.response_id,
        tool_outputs=[{"type": "function_call_output", "call_id": "fc-1", "output": "$ ls\nfile.txt\n[exit 0]"}],
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
    assert fake_client.models.calls[0]["config"]["tools"] == [
        {
            "function_declarations": [
                {
                    "name": "run_command",
                    "description": "Run a command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ]
        }
    ]
    assert fake_client.models.calls[1]["contents"][-1] == {
        "role": "user",
        "parts": [
            {
                "function_response": {
                    "name": "run_command",
                    "id": "fc-1",
                    "response": {"output": "$ ls\nfile.txt\n[exit 0]"},
                }
            }
        ],
    }


def test_google_user_proxy_review_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeGoogleClient.instances.clear()
    _FakeGoogleClient.queued_responses = [
        [SimpleNamespace(text="I ran the command and it failed.", function_calls=[])]
    ]
    monkeypatch.setattr("eval_harness.orchestration.user_proxy_llm_google.genai", SimpleNamespace(Client=_FakeGoogleClient))

    client = GoogleGenAIUserProxyLLMClient(
        UserProxyLLMClientConfig(
            model="gemini-2.5-flash",
            api_key="test-key",
            request_timeout_seconds=17.0,
        )
    )

    review = client.review_reply(
        system_prompt="You are a confused user.",
        transcript=[],
        subject_reply="What did the command print?",
        recent_memory_text="$ ls\nfile.txt\n[exit 0]",
        tool_outputs_text=["$ ls\nfile.txt\n[exit 0]"],
        draft_reply="You should run ls to see the output.",
    )

    assert review.final_reply == "I ran the command and it failed."
    fake_client = _FakeGoogleClient.instances[-1]
    call = fake_client.models.calls[0]
    assert "[Draft reply]" in call["contents"]
    assert "You should run ls" in call["contents"]
