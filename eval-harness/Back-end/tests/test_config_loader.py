"""Tests for Phase 3 config loader changes (judges: list, back-compat, defaults)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    from types import ModuleType
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.cli import _judges_from_config
from eval_harness.judges.anthropic import AnthropicBlindJudge
from eval_harness.judges.openai_responses import OpenAIResponsesBlindJudge


def _fake_anthropic_key(monkeypatch=None):
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-anthropic")


def test_judges_list_accepted() -> None:
    """Config with judges: list builds multiple judge instances."""
    _fake_anthropic_key()
    config = {
        "judges": [
            {
                "name": "haiku",
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "api_key": "test-key",
                "weight": 2.0,
            },
            {
                "name": "haiku2",
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "api_key": "test-key",
                "weight": 1.0,
            },
        ]
    }
    judges, weights = _judges_from_config(config, mode="absolute")
    assert len(judges) == 2
    assert weights == [2.0, 1.0]
    assert all(isinstance(j, AnthropicBlindJudge) for j in judges)


def test_single_judge_back_compat() -> None:
    """Legacy judge: block wraps into a one-element list."""
    config = {
        "judge": {
            "provider": "anthropic",
            "model": "claude-haiku-4-5-20251001",
            "api_key": "test-key",
        }
    }
    judges, weights = _judges_from_config(config, mode="absolute")
    assert len(judges) == 1
    assert weights == [1.0]
    assert isinstance(judges[0], AnthropicBlindJudge)


def test_no_judges_config_absolute_default() -> None:
    """Empty config falls back to nano default for absolute mode."""
    os.environ["EVAL_HARNESS_JUDGE_API_KEY"] = "env-key"
    config: dict = {}
    judges, weights = _judges_from_config(config, mode="absolute")
    assert len(judges) == 1
    assert isinstance(judges[0], OpenAIResponsesBlindJudge)
    assert judges[0].config.model == "gpt-5.4-nano"


def test_no_judges_config_pairwise_default() -> None:
    """Empty config falls back to nano default for pairwise mode."""
    os.environ["EVAL_HARNESS_JUDGE_API_KEY"] = "env-key"
    config: dict = {}
    judges, weights = _judges_from_config(config, mode="pairwise")
    assert len(judges) == 1
    assert isinstance(judges[0], OpenAIResponsesBlindJudge)
    assert judges[0].config.model == "gpt-5.4-nano"


def test_judges_list_takes_priority_over_judge_block() -> None:
    """When both judges: and judge: exist, judges: wins."""
    config = {
        "judge": {
            "provider": "anthropic",
            "model": "claude-haiku-4-5-20251001",
            "api_key": "old-key",
        },
        "judges": [
            {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "api_key": "new-key",
            }
        ],
    }
    judges, weights = _judges_from_config(config, mode="absolute")
    assert len(judges) == 1
    assert judges[0].config.model == "claude-sonnet-4-6"


def test_weight_defaults_to_one_when_omitted() -> None:
    config = {
        "judges": [
            {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "api_key": "k"},
            {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "api_key": "k", "weight": 3.0},
        ]
    }
    judges, weights = _judges_from_config(config)
    assert weights == [1.0, 3.0]
