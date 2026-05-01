from __future__ import annotations

import sys
from pathlib import Path

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    from types import ModuleType
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.judges.rubric import (
    OUTCOME_CRITERION,
    SCALE_ANCHORS,
    UNIVERSAL_RUBRIC,
    format_tagged_rubric,
)


def test_universal_rubric_has_five_items() -> None:
    assert len(UNIVERSAL_RUBRIC) == 5


def test_universal_rubric_includes_outcome_criterion() -> None:
    assert any(item.startswith(OUTCOME_CRITERION) for item in UNIVERSAL_RUBRIC)


def test_outcome_criterion_sentinel() -> None:
    assert OUTCOME_CRITERION == "Outcome"


def test_scale_anchors_covers_zero_to_four() -> None:
    for level in ("0", "1", "2", "3", "4"):
        assert level in SCALE_ANCHORS


def test_format_tagged_rubric_prefixes() -> None:
    universal = ("U1", "U2")
    scenario = ("S1",)
    result = format_tagged_rubric(universal, scenario)
    assert result == ("[universal] U1", "[universal] U2", "[scenario] S1")


def test_format_tagged_rubric_empty_scenario() -> None:
    result = format_tagged_rubric(("U1",), ())
    assert result == ("[universal] U1",)


def test_format_tagged_rubric_empty_universal() -> None:
    result = format_tagged_rubric((), ("S1", "S2"))
    assert result == ("[scenario] S1", "[scenario] S2")


def test_format_tagged_rubric_with_real_universal_rubric() -> None:
    scenario = ("Did the assistant restart nginx?",)
    tagged = format_tagged_rubric(UNIVERSAL_RUBRIC, scenario)
    assert len(tagged) == len(UNIVERSAL_RUBRIC) + len(scenario)
    assert all(item.startswith("[universal] ") for item in tagged[: len(UNIVERSAL_RUBRIC)])
    assert tagged[-1].startswith("[scenario] ")
