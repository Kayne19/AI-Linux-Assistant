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

from eval_harness.cli import _judge_from_config, _planner_from_config, _subject_adapters_from_config, _user_proxy_llm_from_config
from eval_harness.adapters import AILinuxAssistantHttpAdapter, OpenAIChatGPTAdapter
from eval_harness.judges.anthropic import AnthropicBlindJudge
from eval_harness.judges.google_genai import GoogleGenAIBlindJudge
from eval_harness.judges.openai_responses import OpenAIResponsesBlindJudge
from eval_harness.orchestration.user_proxy_llm import UserProxyLLMClient
from eval_harness.orchestration.user_proxy_llm_anthropic import AnthropicUserProxyLLMClient
from eval_harness.orchestration.user_proxy_llm_google import GoogleGenAIUserProxyLLMClient
from eval_harness.planners.anthropic import AnthropicScenarioPlanner
from eval_harness.planners.google_genai import GoogleGenAIScenarioPlanner
from eval_harness.planners.openai_responses import OpenAIResponsesScenarioPlanner
from eval_harness.models import SubjectSpec, TurnSeed


def test_planner_and_judge_default_to_openai_responses() -> None:
    planner = _planner_from_config({"model": "gpt-5.4", "api_key": "planner-key"})
    judge = _judge_from_config({"model": "gpt-5.4-mini", "api_key": "judge-key"})

    assert planner.name == "openai_responses_planner"
    assert judge.name == "openai_responses_blind_judge"
    assert isinstance(planner, OpenAIResponsesScenarioPlanner)
    assert isinstance(judge, OpenAIResponsesBlindJudge)
    assert planner.config.web_search_enabled is True
    assert planner.client.config.reasoning_effort == "xhigh"
    assert planner.client.config.request_timeout_seconds is None


def test_planner_and_judge_reject_legacy_type() -> None:
    with pytest.raises(ValueError, match="openai_compatible"):
        _planner_from_config({"type": "openai_compatible", "model": "gpt-5.4", "api_key": "planner-key"})
    with pytest.raises(ValueError, match="openai_compatible"):
        _judge_from_config({"type": "openai_compatible", "model": "gpt-5.4-mini", "api_key": "judge-key"})


def test_role_provider_selection_supports_openai_anthropic_and_google() -> None:
    assert isinstance(
        _planner_from_config({"provider": "openai", "model": "gpt-5.4", "api_key": "planner-key"}),
        OpenAIResponsesScenarioPlanner,
    )
    assert isinstance(
        _planner_from_config({"provider": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "planner-key"}),
        AnthropicScenarioPlanner,
    )
    assert isinstance(
        _planner_from_config({"provider": "google", "model": "gemini-2.5-pro", "api_key": "planner-key"}),
        GoogleGenAIScenarioPlanner,
    )

    assert isinstance(
        _judge_from_config({"provider": "openai", "model": "gpt-5.4-mini", "api_key": "judge-key"}),
        OpenAIResponsesBlindJudge,
    )
    assert isinstance(
        _judge_from_config({"provider": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "judge-key"}),
        AnthropicBlindJudge,
    )
    assert isinstance(
        _judge_from_config({"provider": "google", "model": "gemini-2.5-flash", "api_key": "judge-key"}),
        GoogleGenAIBlindJudge,
    )

    assert isinstance(
        _user_proxy_llm_from_config({"provider": "openai", "model": "gpt-5.4", "api_key": "proxy-key"}),
        UserProxyLLMClient,
    )
    assert isinstance(
        _user_proxy_llm_from_config(
            {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "proxy-key"}
        ),
        AnthropicUserProxyLLMClient,
    )
    assert isinstance(
        _user_proxy_llm_from_config({"provider": "google", "model": "gemini-2.5-flash", "api_key": "proxy-key"}),
        GoogleGenAIUserProxyLLMClient,
    )


def test_subject_adapter_selection_supports_ai_linux_assistant_http_and_openai_chatgpt() -> None:
    adapters = _subject_adapters_from_config(
        {
            "subject_adapters": {
                "ai_linux_assistant_http": {
                    "type": "ai_linux_assistant_http",
                    "base_url": "https://ai.example.invalid",
                    "auth0_m2m": {
                        "token_url": "https://tenant.example.invalid/oauth/token",
                        "audience": "https://api.example.invalid",
                        "clients_by_subject": {
                            "regular": {"client_id": "rid", "client_secret": "rsec"},
                        },
                    },
                },
                "openai_chatgpt": {
                    "type": "openai_chatgpt",
                    "api_key": "chatgpt-key",
                    "model": "gpt-5.4",
                },
            }
        }
    )

    assert set(adapters) == {"ai_linux_assistant_http", "openai_chatgpt"}
    assert isinstance(adapters["ai_linux_assistant_http"], AILinuxAssistantHttpAdapter)
    assert isinstance(adapters["openai_chatgpt"], OpenAIChatGPTAdapter)


def test_openai_chatgpt_adapter_selection_parses_string_booleans() -> None:
    adapters = _subject_adapters_from_config(
        {
            "subject_adapters": {
                "openai_chatgpt": {
                    "type": "openai_chatgpt",
                    "api_key": "chatgpt-key",
                    "model": "gpt-5.4",
                    "web_search_enabled": "false",
                    "web_search_include_sources": "true",
                },
            }
        }
    )

    adapter = adapters["openai_chatgpt"]
    assert isinstance(adapter, OpenAIChatGPTAdapter)
    assert adapter.config.web_search_enabled is False
    assert adapter.config.web_search_include_sources is True


def test_openai_chatgpt_adapter_defaults_mirror_chatgpt_browser() -> None:
    adapters = _subject_adapters_from_config(
        {
            "subject_adapters": {
                "openai_chatgpt": {
                    "type": "openai_chatgpt",
                    "api_key": "chatgpt-key",
                    "model": "gpt-5.4",
                },
            }
        }
    )

    adapter = adapters["openai_chatgpt"]
    assert isinstance(adapter, OpenAIChatGPTAdapter)
    assert adapter.config.web_search_enabled is True
    assert adapter.config.code_interpreter_enabled is True
    assert adapter.config.truncation == "auto"
    assert adapter.config.request_timeout_seconds is None
    assert adapter.config.max_output_tokens is None


def test_openai_chatgpt_adapter_parses_code_interpreter_and_truncation_overrides() -> None:
    adapters = _subject_adapters_from_config(
        {
            "subject_adapters": {
                "openai_chatgpt": {
                    "type": "openai_chatgpt",
                    "api_key": "chatgpt-key",
                    "model": "gpt-5.4",
                    "code_interpreter_enabled": "false",
                    "truncation": "disabled",
                },
            }
        }
    )

    adapter = adapters["openai_chatgpt"]
    assert isinstance(adapter, OpenAIChatGPTAdapter)
    assert adapter.config.code_interpreter_enabled is False
    assert adapter.config.truncation == "disabled"


def _fixed_clock():
    from datetime import datetime, timezone

    return lambda: datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)


def test_openai_chatgpt_session_uses_conversation_mode_web_search_and_source_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeResponsesAPI:
        def __init__(self, responses: list[object]):
            self._responses = list(responses)
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return self._responses.pop(0)

    class _FakeConversationsAPI:
        def __init__(self):
            self.calls: list[dict[str, object]] = []

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

    def _response(*, response_id: str, output_text: str):
        return SimpleNamespace(
            id=response_id,
            status="completed",
            output_text=output_text,
            output=[
                SimpleNamespace(
                    type="message",
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            text=output_text,
                            annotations=[
                                SimpleNamespace(
                                    type="url_citation",
                                    url="https://platform.openai.com/docs",
                                    title="OpenAI Docs",
                                    start_index=0,
                                    end_index=6,
                                )
                            ],
                        )
                    ],
                ),
                SimpleNamespace(
                    type="web_search_call",
                    action=SimpleNamespace(
                        sources=[
                            SimpleNamespace(
                                type="url",
                                title="OpenAI Docs",
                                url="https://platform.openai.com/docs",
                            )
                        ]
                    ),
                ),
            ],
        )

    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [[_response(response_id="resp-1", output_text="first"), _response(response_id="resp-2", output_text="second")]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)

    from eval_harness.adapters.openai_chatgpt import OpenAIChatGPTConfig, OpenAIChatGPTSession

    session = OpenAIChatGPTSession(
        config=OpenAIChatGPTConfig(
            model="gpt-5.4",
            api_key="chatgpt-key",
            request_timeout_seconds=12.5,
            web_search_enabled=True,
            web_search_allowed_domains=("platform.openai.com",),
            web_search_include_sources=True,
            code_interpreter_enabled=False,
        ),
        benchmark_run_id="bench-1",
        subject=SubjectSpec(subject_name="baseline", adapter_type="openai_chatgpt"),
        clock=_fixed_clock(),
    )
    session.seed_context((TurnSeed(role="system", content="Prior context"), TurnSeed(role="assistant", content="Okay")))

    first = session.submit_user_message("Fix nginx.")
    second = session.submit_user_message("What changed?")

    fake_client = _FakeOpenAI.instances[-1]
    assert fake_client.init_kwargs == {"api_key": "chatgpt-key", "timeout": 12.5}
    assert first.assistant_message == "first\n\nSources:\n- OpenAI Docs - https://platform.openai.com/docs"
    assert second.assistant_message == "second\n\nSources:\n- OpenAI Docs - https://platform.openai.com/docs"
    assert fake_client.conversations.calls == [
        {
            "items": [
                {"type": "message", "role": "assistant", "content": "Okay"},
            ],
            "metadata": {
                "benchmark_run_id": "bench-1",
                "subject_name": "baseline",
            },
        }
    ]
    expected_instructions = (
        "You are ChatGPT, a large language model trained by OpenAI.\n"
        "Current date: 2026-04-18\n\nPrior context"
    )
    assert fake_client.responses.calls == [
        {
            "model": "gpt-5.4",
            "instructions": expected_instructions,
            "input": [{"role": "user", "content": "Fix nginx."}],
            "conversation": {"id": "conv_123"},
            "tools": [{"type": "web_search", "filters": {"allowed_domains": ["platform.openai.com"]}}],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "include": ["web_search_call.action.sources"],
            "truncation": "auto",
            "metadata": {
                "benchmark_run_id": "bench-1",
                "subject_name": "baseline",
                "turn_index": "0",
            },
        },
        {
            "model": "gpt-5.4",
            "instructions": expected_instructions,
            "input": [{"role": "user", "content": "What changed?"}],
            "conversation": {"id": "conv_123"},
            "tools": [{"type": "web_search", "filters": {"allowed_domains": ["platform.openai.com"]}}],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "include": ["web_search_call.action.sources"],
            "truncation": "auto",
            "metadata": {
                "benchmark_run_id": "bench-1",
                "subject_name": "baseline",
                "turn_index": "1",
            },
        },
    ]
    assert first.debug["conversation_id"] == "conv_123"
    assert first.debug["citations"] == (
        {
            "type": "url_citation",
            "start_index": 0,
            "end_index": 6,
            "url": "https://platform.openai.com/docs",
            "title": "OpenAI Docs",
            "text": "first",
        },
    )


def test_openai_chatgpt_session_surfaces_failed_response_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResponsesAPI:
        def __init__(self, responses: list[object]):
            self._responses = list(responses)

        def create(self, **kwargs):
            del kwargs
            return self._responses.pop(0)

    class _FakeConversationsAPI:
        def create(self, **kwargs):
            del kwargs
            return SimpleNamespace(id="conv_123", object="conversation")

    class _FakeOpenAI:
        queued_responses: list[list[object]] = []

        def __init__(self, **kwargs):
            del kwargs
            self.responses = _FakeResponsesAPI(self.queued_responses.pop(0))
            self.conversations = _FakeConversationsAPI()

    _FakeOpenAI.queued_responses = [[
        SimpleNamespace(
            id="resp_failed",
            status="failed",
            output_text="",
            output=[],
            error=SimpleNamespace(message="backend exploded"),
        )
    ]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)

    from eval_harness.adapters.openai_chatgpt import OpenAIChatGPTConfig, OpenAIChatGPTSession

    session = OpenAIChatGPTSession(
        config=OpenAIChatGPTConfig(model="gpt-5.4", api_key="chatgpt-key"),
        benchmark_run_id="bench-1",
        subject=SubjectSpec(subject_name="baseline", adapter_type="openai_chatgpt"),
    )

    with pytest.raises(Exception, match="backend exploded"):
        session.submit_user_message("Fix nginx.")


def test_openai_chatgpt_session_supports_response_chain_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResponsesAPI:
        def __init__(self, responses: list[object]):
            self._responses = list(responses)
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return self._responses.pop(0)

    class _FakeConversationsAPI:
        def __init__(self):
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(id="conv_unused", object="conversation")

    class _FakeOpenAI:
        instances: list["_FakeOpenAI"] = []
        queued_responses: list[list[object]] = []

        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.responses = _FakeResponsesAPI(self.queued_responses.pop(0))
            self.conversations = _FakeConversationsAPI()
            self.__class__.instances.append(self)

    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [[
        SimpleNamespace(id="resp-1", status="completed", output_text="first", output=[]),
        SimpleNamespace(id="resp-2", status="completed", output_text="second", output=[]),
    ]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)

    from eval_harness.adapters.openai_chatgpt import OpenAIChatGPTConfig, OpenAIChatGPTSession

    session = OpenAIChatGPTSession(
        config=OpenAIChatGPTConfig(
            model="gpt-5.4",
            api_key="chatgpt-key",
            conversation_state_mode="response_chain",
            web_search_enabled=False,
            code_interpreter_enabled=False,
        ),
        benchmark_run_id="bench-1",
        subject=SubjectSpec(subject_name="baseline", adapter_type="openai_chatgpt"),
        clock=_fixed_clock(),
    )
    session.seed_context((TurnSeed(role="system", content="Prior context"),))

    session.submit_user_message("Fix nginx.")
    session.submit_user_message("What changed?")

    fake_client = _FakeOpenAI.instances[-1]
    expected_instructions = (
        "You are ChatGPT, a large language model trained by OpenAI.\n"
        "Current date: 2026-04-18\n\nPrior context"
    )
    assert fake_client.conversations.calls == []
    assert fake_client.responses.calls == [
        {
            "model": "gpt-5.4",
            "instructions": expected_instructions,
            "input": [{"role": "user", "content": "Fix nginx."}],
            "truncation": "auto",
            "metadata": {
                "benchmark_run_id": "bench-1",
                "subject_name": "baseline",
                "turn_index": "0",
            },
        },
        {
            "model": "gpt-5.4",
            "instructions": expected_instructions,
            "input": [{"role": "user", "content": "What changed?"}],
            "previous_response_id": "resp-1",
            "truncation": "auto",
            "metadata": {
                "benchmark_run_id": "bench-1",
                "subject_name": "baseline",
                "turn_index": "1",
            },
        },
    ]


def test_openai_chatgpt_session_does_not_append_sources_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResponsesAPI:
        def __init__(self, responses: list[object]):
            self._responses = list(responses)

        def create(self, **kwargs):
            del kwargs
            return self._responses.pop(0)

    class _FakeConversationsAPI:
        def create(self, **kwargs):
            del kwargs
            return SimpleNamespace(id="conv_123", object="conversation")

    class _FakeOpenAI:
        queued_responses: list[list[object]] = []

        def __init__(self, **kwargs):
            del kwargs
            self.responses = _FakeResponsesAPI(self.queued_responses.pop(0))
            self.conversations = _FakeConversationsAPI()

    _FakeOpenAI.queued_responses = [[
        SimpleNamespace(
            id="resp-1",
            status="completed",
            output_text="first",
            output=[
                SimpleNamespace(
                    type="message",
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            text="first",
                            annotations=[
                                SimpleNamespace(
                                    type="url_citation",
                                    url="https://platform.openai.com/docs",
                                    title="OpenAI Docs",
                                    start_index=0,
                                    end_index=5,
                                )
                            ],
                        )
                    ],
                )
            ],
        )
    ]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)

    from eval_harness.adapters.openai_chatgpt import OpenAIChatGPTConfig, OpenAIChatGPTSession

    session = OpenAIChatGPTSession(
        config=OpenAIChatGPTConfig(
            model="gpt-5.4",
            api_key="chatgpt-key",
            web_search_enabled=True,
            web_search_include_sources=False,
        ),
        benchmark_run_id="bench-1",
        subject=SubjectSpec(subject_name="baseline", adapter_type="openai_chatgpt"),
    )

    result = session.submit_user_message("Fix nginx.")

    assert result.assistant_message == "first"
    assert result.debug["citations"] == (
        {
            "type": "url_citation",
            "start_index": 0,
            "end_index": 5,
            "url": "https://platform.openai.com/docs",
            "title": "OpenAI Docs",
            "text": "first",
        },
    )


def test_openai_chatgpt_session_defaults_enable_web_search_and_code_interpreter_with_auto_preamble(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeResponsesAPI:
        def __init__(self, responses):
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
            return SimpleNamespace(id="conv_default", object="conversation")

    class _FakeOpenAI:
        instances: list = []
        queued_responses: list = []

        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.responses = _FakeResponsesAPI(self.queued_responses.pop(0))
            self.conversations = _FakeConversationsAPI()
            self.__class__.instances.append(self)

    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [[
        SimpleNamespace(id="resp-1", status="completed", output_text="Hi.", output=[]),
    ]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)

    from eval_harness.adapters.openai_chatgpt import OpenAIChatGPTConfig, OpenAIChatGPTSession

    session = OpenAIChatGPTSession(
        config=OpenAIChatGPTConfig(model="gpt-5.4", api_key="chatgpt-key"),
        benchmark_run_id="bench-2",
        subject=SubjectSpec(subject_name="baseline", adapter_type="openai_chatgpt"),
        clock=_fixed_clock(),
    )

    session.submit_user_message("Hello.")

    fake_client = _FakeOpenAI.instances[-1]
    assert fake_client.init_kwargs == {"api_key": "chatgpt-key"}
    call = fake_client.responses.calls[0]
    assert call["instructions"] == (
        "You are ChatGPT, a large language model trained by OpenAI.\n"
        "Current date: 2026-04-18"
    )
    assert call["tools"] == [
        {"type": "web_search"},
        {"type": "code_interpreter", "container": {"type": "auto"}},
    ]
    assert call["tool_choice"] == "auto"
    assert call["parallel_tool_calls"] is True
    assert call["truncation"] == "auto"
    assert "user" not in call
    assert call["metadata"] == {
        "benchmark_run_id": "bench-2",
        "subject_name": "baseline",
        "turn_index": "0",
    }
    assert "include" not in call


def test_openai_chatgpt_session_respects_explicit_instructions_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeResponsesAPI:
        def __init__(self, responses):
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
            return SimpleNamespace(id="conv_explicit", object="conversation")

    class _FakeOpenAI:
        instances: list = []
        queued_responses: list = []

        def __init__(self, **kwargs):
            del kwargs
            self.responses = _FakeResponsesAPI(self.queued_responses.pop(0))
            self.conversations = _FakeConversationsAPI()
            self.__class__.instances.append(self)

    _FakeOpenAI.instances.clear()
    _FakeOpenAI.queued_responses = [[
        SimpleNamespace(id="resp-1", status="completed", output_text="Hi.", output=[]),
    ]]
    monkeypatch.setattr("eval_harness.openai_responses.OpenAI", _FakeOpenAI)

    from eval_harness.adapters.openai_chatgpt import OpenAIChatGPTConfig, OpenAIChatGPTSession

    session = OpenAIChatGPTSession(
        config=OpenAIChatGPTConfig(
            model="gpt-5.4",
            api_key="chatgpt-key",
            instructions="You are a helpful pirate.",
            web_search_enabled=False,
            code_interpreter_enabled=False,
        ),
        benchmark_run_id="bench-3",
        subject=SubjectSpec(subject_name="baseline", adapter_type="openai_chatgpt"),
        clock=_fixed_clock(),
    )
    session.seed_context((TurnSeed(role="system", content="Stay in scope."),))
    session.submit_user_message("Ahoy.")

    fake_client = _FakeOpenAI.instances[-1]
    assert fake_client.responses.calls[0]["instructions"] == (
        "You are a helpful pirate.\n\nStay in scope."
    )


def test_openai_planner_config_controls_web_search_and_reasoning() -> None:
    planner = _planner_from_config(
        {
            "provider": "openai",
            "model": "gpt-5.4",
            "api_key": "planner-key",
            "reasoning_effort": "medium",
            "web_search_enabled": False,
        }
    )

    assert isinstance(planner, OpenAIResponsesScenarioPlanner)
    assert planner.config.web_search_enabled is False
    assert planner.client.config.reasoning_effort == "medium"


def test_openai_planner_config_accepts_explicit_timeout_when_set() -> None:
    planner = _planner_from_config(
        {
            "provider": "openai",
            "model": "gpt-5.4",
            "api_key": "planner-key",
            "request_timeout_seconds": 600,
        }
    )

    assert isinstance(planner, OpenAIResponsesScenarioPlanner)
    assert planner.client.config.request_timeout_seconds == 600.0


def test_openai_planner_config_rejects_invalid_web_search_string() -> None:
    with pytest.raises(ValueError, match="web_search_enabled"):
        _planner_from_config(
            {
                "provider": "openai",
                "model": "gpt-5.4",
                "api_key": "planner-key",
                "web_search_enabled": "fasle",
            }
        )


def test_role_provider_selection_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="mistral"):
        _planner_from_config({"provider": "mistral", "model": "mistral-large", "api_key": "planner-key"})
    with pytest.raises(ValueError, match="mistral"):
        _judge_from_config({"provider": "mistral", "model": "mistral-large", "api_key": "judge-key"})
    with pytest.raises(ValueError, match="mistral"):
        _user_proxy_llm_from_config({"provider": "mistral", "model": "mistral-large", "api_key": "proxy-key"})
