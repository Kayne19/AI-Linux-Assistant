from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.judges._prompts import build_absolute_instructions, build_pairwise_instructions
from eval_harness.judges.rubric import OUTCOME_CRITERION, SCALE_ANCHORS, UNIVERSAL_RUBRIC


def test_absolute_instructions_contains_scale_anchors() -> None:
    instructions = build_absolute_instructions(())
    assert "0 —" in instructions
    assert "4 —" in instructions
    assert SCALE_ANCHORS in instructions


def test_absolute_instructions_contains_universal_rubric_items() -> None:
    instructions = build_absolute_instructions(())
    for item in UNIVERSAL_RUBRIC:
        assert item in instructions


def test_absolute_instructions_contains_outcome_rule() -> None:
    instructions = build_absolute_instructions(())
    assert OUTCOME_CRITERION in instructions
    assert "repair_success" in instructions
    assert "MUST" in instructions


def test_absolute_instructions_includes_passed_rubric_items() -> None:
    extra = ("[scenario] Did the assistant ask for logs before proposing changes?",)
    instructions = build_absolute_instructions(extra)
    assert extra[0] in instructions


def test_absolute_instructions_states_blind() -> None:
    instructions = build_absolute_instructions(())
    assert "blind" in instructions.lower()


def test_pairwise_instructions_mentions_transcript_a_and_b() -> None:
    instructions = build_pairwise_instructions(())
    assert "Transcript A" in instructions
    assert "Transcript B" in instructions


def test_pairwise_instructions_contains_winner_margin_fields() -> None:
    instructions = build_pairwise_instructions(())
    assert "winner" in instructions
    assert "margin" in instructions


def test_pairwise_instructions_contains_outcome_rule() -> None:
    instructions = build_pairwise_instructions(())
    assert OUTCOME_CRITERION in instructions
    assert "repair_success" in instructions
    assert "decisive" in instructions


def test_pairwise_instructions_contains_swap_fairness_instruction() -> None:
    instructions = build_pairwise_instructions(())
    lower = instructions.lower()
    assert "length" in lower or "formatting" in lower or "order" in lower


def test_pairwise_instructions_includes_passed_rubric_items() -> None:
    extra = ("[scenario] Did the assistant document its reasoning?",)
    instructions = build_pairwise_instructions(extra)
    assert extra[0] in instructions


def test_pairwise_instructions_states_blind() -> None:
    instructions = build_pairwise_instructions(())
    assert "blind" in instructions.lower()
