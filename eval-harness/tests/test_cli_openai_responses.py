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

from eval_harness.cli import _judge_from_config, _planner_from_config


def test_planner_and_judge_default_to_openai_responses() -> None:
    planner = _planner_from_config({"model": "gpt-5.4", "api_key": "planner-key"})
    judge = _judge_from_config({"model": "gpt-5.4-mini", "api_key": "judge-key"})

    assert planner.name == "openai_responses_planner"
    assert judge.name == "openai_responses_blind_judge"


def test_planner_and_judge_reject_legacy_type() -> None:
    with pytest.raises(ValueError, match="openai_compatible"):
        _planner_from_config({"type": "openai_compatible", "model": "gpt-5.4", "api_key": "planner-key"})
    with pytest.raises(ValueError, match="openai_compatible"):
        _judge_from_config({"type": "openai_compatible", "model": "gpt-5.4-mini", "api_key": "judge-key"})
