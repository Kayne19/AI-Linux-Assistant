from __future__ import annotations

import sys
from pathlib import Path

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    from types import ModuleType
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.models import (
    BlindJudgeResult,
    PairwiseJudgeRequest,
    PairwiseJudgeResult,
    PairwiseVerdict,
    TurnRecord,
)


def _turn(role: str, content: str) -> TurnRecord:
    return TurnRecord(role=role, content=content)  # type: ignore[arg-type]


# --- PairwiseVerdict round-trip ---

def test_pairwise_verdict_round_trip() -> None:
    v = PairwiseVerdict(
        criterion="Diagnosis correctness",
        winner="A",
        margin="clear",
        rationale="A found the root cause.",
        evidence_a="journalctl showed SELinux denial",
        evidence_b="",
    )
    d = v.to_dict()
    v2 = PairwiseVerdict.from_dict(d)
    assert v == v2


def test_pairwise_verdict_frozen() -> None:
    v = PairwiseVerdict(
        criterion="Outcome",
        winner="tie",
        margin="slight",
        rationale="Both passed.",
        evidence_a="",
        evidence_b="",
    )
    try:
        v.criterion = "other"  # type: ignore[misc]
        assert False, "should be frozen"
    except (AttributeError, TypeError):
        pass


# --- PairwiseJudgeRequest round-trip ---

def test_pairwise_judge_request_round_trip() -> None:
    req = PairwiseJudgeRequest(
        blind_label_a="alpha",
        blind_label_b="beta",
        transcript_a=(_turn("user", "nginx is down"), _turn("assistant", "check journalctl")),
        transcript_b=(_turn("user", "nginx is down"), _turn("assistant", "restart nginx")),
        rubric=("Diagnosis correctness", "Outcome"),
        repair_success_a=True,
        repair_success_b=False,
        metadata={"scenario": "nginx_repair"},
    )
    d = req.to_dict()
    req2 = PairwiseJudgeRequest.from_dict(d)
    assert req2.blind_label_a == "alpha"
    assert req2.blind_label_b == "beta"
    assert len(req2.transcript_a) == 2
    assert len(req2.transcript_b) == 2
    assert req2.rubric == ("Diagnosis correctness", "Outcome")
    assert req2.repair_success_a is True
    assert req2.repair_success_b is False
    assert req2.metadata["scenario"] == "nginx_repair"


def test_pairwise_judge_request_optional_fields_default() -> None:
    req = PairwiseJudgeRequest(
        blind_label_a="a",
        blind_label_b="b",
        transcript_a=(),
        transcript_b=(),
        rubric=(),
    )
    assert req.repair_success_a is None
    assert req.repair_success_b is None
    assert req.metadata == {}


# --- PairwiseJudgeResult round-trip ---

def test_pairwise_judge_result_round_trip() -> None:
    result = PairwiseJudgeResult(
        blind_label_a="alpha",
        blind_label_b="beta",
        summary="Alpha won on diagnosis.",
        verdicts=(
            PairwiseVerdict(
                criterion="Diagnosis correctness",
                winner="A",
                margin="decisive",
                rationale="A nailed it.",
                evidence_a="journalctl -xe showed ...",
                evidence_b="",
            ),
        ),
        raw_response={"raw": True},
        metadata={"judge": "claude"},
    )
    d = result.to_dict()
    result2 = PairwiseJudgeResult.from_dict(d)
    assert result2.blind_label_a == "alpha"
    assert result2.blind_label_b == "beta"
    assert result2.summary == "Alpha won on diagnosis."
    assert len(result2.verdicts) == 1
    assert result2.verdicts[0].winner == "A"
    assert result2.raw_response == {"raw": True}


# --- BlindJudgeResult new scores shape ---

def test_blind_judge_result_scores_new_shape_round_trip() -> None:
    scores = {
        "Diagnosis correctness": {"score": 3, "rationale": "mostly right", "evidence": "quote"},
        "Outcome": {"score": 4, "rationale": "repair succeeded", "evidence": ""},
    }
    result = BlindJudgeResult(
        blind_label="subject-a",
        summary="good",
        scores=scores,
    )
    d = result.to_dict()
    result2 = BlindJudgeResult.from_dict(d)
    assert result2.scores["Diagnosis correctness"]["score"] == 3
    assert result2.scores["Outcome"]["evidence"] == ""


def test_blind_judge_result_round_trip_empty_scores() -> None:
    result = BlindJudgeResult(blind_label="x", summary="", scores={})
    assert BlindJudgeResult.from_dict(result.to_dict()).scores == {}
