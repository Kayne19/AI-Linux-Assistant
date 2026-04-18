from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.cli import _judge_from_config, _planner_from_config, _user_proxy_llm_from_config
from eval_harness.judges.anthropic import AnthropicBlindJudge
from eval_harness.judges.google_genai import GoogleGenAIBlindJudge
from eval_harness.judges.openai_responses import OpenAIResponsesBlindJudge
from eval_harness.orchestration.user_proxy_llm import UserProxyLLMClient
from eval_harness.orchestration.user_proxy_llm_anthropic import AnthropicUserProxyLLMClient
from eval_harness.orchestration.user_proxy_llm_google import GoogleGenAIUserProxyLLMClient
from eval_harness.planners.anthropic import AnthropicScenarioPlanner
from eval_harness.planners.google_genai import GoogleGenAIScenarioPlanner
from eval_harness.planners.openai_responses import OpenAIResponsesScenarioPlanner


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
        _user_proxy_llm_from_config({"provider": "openai", "model": "gpt-5.4-mini", "api_key": "proxy-key"}),
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
