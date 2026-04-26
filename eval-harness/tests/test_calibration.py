"""Tests for calibrate-judge agreement metrics with FakeJudge fixtures."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    from types import ModuleType
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.judges.base import BlindJudge
from eval_harness.models import (
    BlindJudgeRequest,
    BlindJudgeResult,
    PairwiseJudgeRequest,
    PairwiseJudgeResult,
    PairwiseVerdict,
)


# ---------------------------------------------------------------------------
# Helpers: shared agreement/kappa computation extracted from cli.py
# ---------------------------------------------------------------------------

def _cohens_kappa(obs_a: list[str], obs_b: list[str]) -> float:
    categories = ["A", "B", "tie"]
    n = len(obs_a)
    if n == 0:
        return 0.0
    p_o = sum(a == b for a, b in zip(obs_a, obs_b)) / n
    freq_a = {c: obs_a.count(c) / n for c in categories}
    freq_b = {c: obs_b.count(c) / n for c in categories}
    p_e = sum(freq_a[c] * freq_b[c] for c in categories)
    if abs(1 - p_e) < 1e-9:
        return 1.0
    return (p_o - p_e) / (1 - p_e)


def _raw_agreement(obs_a: list[str], obs_b: list[str]) -> float:
    if not obs_a:
        return 0.0
    return sum(a == b for a, b in zip(obs_a, obs_b)) / len(obs_a)


# ---------------------------------------------------------------------------
# FakeJudge variants
# ---------------------------------------------------------------------------

@dataclass
class FixedPairwiseJudge(BlindJudge):
    """Always returns the same winner for every criterion."""

    name: str = "fixed_judge"
    fixed_winner: str = "A"

    def grade(self, request: BlindJudgeRequest) -> BlindJudgeResult:
        return BlindJudgeResult(
            blind_label=request.blind_label,
            summary="",
            scores={crit: {"score": 3, "rationale": "", "evidence": ""} for crit in request.rubric},
            raw_response={},
        )

    def compare(self, request: PairwiseJudgeRequest) -> PairwiseJudgeResult:
        return PairwiseJudgeResult(
            blind_label_a=request.blind_label_a,
            blind_label_b=request.blind_label_b,
            summary="",
            verdicts=tuple(
                PairwiseVerdict(
                    criterion=crit,
                    winner=self.fixed_winner,
                    margin="clear",
                    rationale="fixed",
                    evidence_a="",
                    evidence_b="",
                )
                for crit in request.rubric
            ),
            raw_response={},
        )


@dataclass
class FixedAbsoluteJudge(BlindJudge):
    """Always returns the same score for every criterion."""

    name: str = "fixed_absolute_judge"
    fixed_score: int = 3

    def grade(self, request: BlindJudgeRequest) -> BlindJudgeResult:
        return BlindJudgeResult(
            blind_label=request.blind_label,
            summary="",
            scores={
                crit: {"score": self.fixed_score, "rationale": "", "evidence": ""}
                for crit in request.rubric
            },
            raw_response={},
        )

    def compare(self, request: PairwiseJudgeRequest) -> PairwiseJudgeResult:
        return PairwiseJudgeResult(
            blind_label_a=request.blind_label_a,
            blind_label_b=request.blind_label_b,
            summary="",
            verdicts=(),
            raw_response={},
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_perfect_pairwise_agreement_kappa_one() -> None:
    """Two identical judges → kappa = 1.0 and raw agreement = 1.0."""
    rubric = ("Criterion A", "Criterion B")
    strong = FixedPairwiseJudge(fixed_winner="A")
    candidate = FixedPairwiseJudge(fixed_winner="A")

    # Simulate one pair comparison.
    req = PairwiseJudgeRequest(
        blind_label_a="A",
        blind_label_b="B",
        transcript_a=(),
        transcript_b=(),
        rubric=rubric,
        repair_success_a=None,
        repair_success_b=None,
    )
    s_res = strong.compare(req)
    c_res = candidate.compare(req)

    obs_s = [v.winner for v in s_res.verdicts]
    obs_c = [v.winner for v in c_res.verdicts]

    assert _raw_agreement(obs_s, obs_c) == 1.0
    assert abs(_cohens_kappa(obs_s, obs_c) - 1.0) < 1e-9


def test_total_disagreement_produces_low_kappa() -> None:
    """Two judges disagreeing on every verdict → kappa <= 0 and raw_agreement == 0."""
    rubric = ("Criterion A", "Criterion B", "Criterion C")
    # strong always says A, candidate always says B.
    strong = FixedPairwiseJudge(fixed_winner="A")
    candidate = FixedPairwiseJudge(fixed_winner="B")

    req = PairwiseJudgeRequest(
        blind_label_a="A",
        blind_label_b="B",
        transcript_a=(),
        transcript_b=(),
        rubric=rubric,
        repair_success_a=None,
        repair_success_b=None,
    )
    s_res = strong.compare(req)
    c_res = candidate.compare(req)

    obs_s = [v.winner for v in s_res.verdicts]
    obs_c = [v.winner for v in c_res.verdicts]

    assert _raw_agreement(obs_s, obs_c) == 0.0
    kappa = _cohens_kappa(obs_s, obs_c)
    # Total disagreement → kappa ≤ 0 (0 means chance agreement == observed agreement).
    assert kappa <= 0.0


def test_perfect_absolute_agreement_mae_zero() -> None:
    """Two judges returning identical scores → MAE = 0 per criterion."""
    import math

    rubric = ("Criterion X", "Criterion Y")
    strong = FixedAbsoluteJudge(fixed_score=3)
    candidate = FixedAbsoluteJudge(fixed_score=3)

    req = BlindJudgeRequest(
        blind_label="subj",
        transcript=(),
        rubric=rubric,
        repair_success=None,
    )
    s_res = strong.grade(req)
    c_res = candidate.grade(req)

    for crit in rubric:
        sv = s_res.scores[crit]["score"]
        cv = c_res.scores[crit]["score"]
        assert abs(sv - cv) == 0


def test_absolute_mae_computed_correctly() -> None:
    """MAE = |score_strong - score_candidate| for a single sample per criterion."""
    rubric = ("Criterion X",)
    strong = FixedAbsoluteJudge(fixed_score=4)
    candidate = FixedAbsoluteJudge(fixed_score=2)

    req = BlindJudgeRequest(
        blind_label="subj",
        transcript=(),
        rubric=rubric,
        repair_success=None,
    )
    s_res = strong.grade(req)
    c_res = candidate.grade(req)

    sv = float(s_res.scores["Criterion X"]["score"])
    cv = float(c_res.scores["Criterion X"]["score"])
    mae = abs(sv - cv)
    assert mae == 2.0


def test_kappa_non_tie_agreement_filter() -> None:
    """Non-tie agreement correctly ignores verdicts where either judge said 'tie'."""
    obs_strong = ["A", "tie", "B", "A"]
    obs_candidate = ["A", "B", "B", "tie"]

    # Non-tie pairs where BOTH are non-tie: only index 0 (A,A) and index 2 (B,B).
    non_tie_pairs = [
        (s, c)
        for s, c in zip(obs_strong, obs_candidate)
        if s != "tie" and c != "tie"
    ]
    assert len(non_tie_pairs) == 2
    agree = sum(1 for s, c in non_tie_pairs if s == c)
    assert agree == 2  # both (A,A) and (B,B) agree.
    non_tie_agreement = agree / len(non_tie_pairs)
    assert non_tie_agreement == 1.0
