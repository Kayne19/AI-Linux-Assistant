from __future__ import annotations

import sys
from pathlib import Path

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    from types import ModuleType
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

import jsonschema
import pytest

from eval_harness.judges.schema import (
    blind_judge_absolute_schema,
    blind_judge_pairwise_schema,
    normalize_blind_judge_payload,
    normalize_pairwise_judge_payload,
)


def _validate_absolute(payload: dict) -> None:
    jsonschema.validate(payload, blind_judge_absolute_schema())


def _validate_pairwise(payload: dict) -> None:
    jsonschema.validate(payload, blind_judge_pairwise_schema())


def _good_absolute_payload(**overrides) -> dict:
    base = {
        "blind_label": "subject-a",
        "summary": "good run",
        "scores": [
            {
                "criterion": "Diagnosis correctness",
                "score": 3,
                "rationale": "Correctly identified the root cause.",
                "evidence": "assistant said: check journalctl",
            }
        ],
    }
    base.update(overrides)
    return base


def _good_pairwise_payload(**overrides) -> dict:
    base = {
        "blind_label_a": "subject-a",
        "blind_label_b": "subject-b",
        "summary": "A was better overall",
        "verdicts": [
            {
                "criterion": "Diagnosis correctness",
                "winner": "A",
                "margin": "clear",
                "rationale": "A identified the root cause; B did not.",
                "evidence_a": "check journalctl -xe",
                "evidence_b": "",
            }
        ],
    }
    base.update(overrides)
    return base


# --- absolute schema ---

def test_absolute_valid_payload() -> None:
    _validate_absolute(_good_absolute_payload())


def test_absolute_rejects_score_below_zero() -> None:
    payload = _good_absolute_payload()
    payload["scores"][0]["score"] = -1
    with pytest.raises(jsonschema.ValidationError):
        _validate_absolute(payload)


def test_absolute_rejects_score_above_four() -> None:
    payload = _good_absolute_payload()
    payload["scores"][0]["score"] = 5
    with pytest.raises(jsonschema.ValidationError):
        _validate_absolute(payload)


def test_absolute_rejects_missing_rationale() -> None:
    payload = _good_absolute_payload()
    del payload["scores"][0]["rationale"]
    with pytest.raises(jsonschema.ValidationError):
        _validate_absolute(payload)


def test_absolute_rejects_empty_rationale() -> None:
    payload = _good_absolute_payload()
    payload["scores"][0]["rationale"] = ""
    with pytest.raises(jsonschema.ValidationError):
        _validate_absolute(payload)


def test_absolute_accepts_empty_evidence() -> None:
    payload = _good_absolute_payload()
    payload["scores"][0]["evidence"] = ""
    _validate_absolute(payload)


def test_absolute_rejects_extra_properties() -> None:
    payload = _good_absolute_payload()
    payload["scores"][0]["unexpected_key"] = "value"
    with pytest.raises(jsonschema.ValidationError):
        _validate_absolute(payload)


# --- pairwise schema ---

def test_pairwise_valid_payload() -> None:
    _validate_pairwise(_good_pairwise_payload())


def test_pairwise_rejects_invalid_winner() -> None:
    payload = _good_pairwise_payload()
    payload["verdicts"][0]["winner"] = "C"
    with pytest.raises(jsonschema.ValidationError):
        _validate_pairwise(payload)


def test_pairwise_rejects_invalid_margin() -> None:
    payload = _good_pairwise_payload()
    payload["verdicts"][0]["margin"] = "massive"
    with pytest.raises(jsonschema.ValidationError):
        _validate_pairwise(payload)


def test_pairwise_all_winner_values_accepted() -> None:
    for winner in ("A", "B", "tie"):
        payload = _good_pairwise_payload()
        payload["verdicts"][0]["winner"] = winner
        _validate_pairwise(payload)


def test_pairwise_all_margin_values_accepted() -> None:
    for margin in ("slight", "clear", "decisive"):
        payload = _good_pairwise_payload()
        payload["verdicts"][0]["margin"] = margin
        _validate_pairwise(payload)


def test_pairwise_rejects_empty_rationale() -> None:
    payload = _good_pairwise_payload()
    payload["verdicts"][0]["rationale"] = ""
    with pytest.raises(jsonschema.ValidationError):
        _validate_pairwise(payload)


def test_pairwise_accepts_empty_evidence_fields() -> None:
    payload = _good_pairwise_payload()
    payload["verdicts"][0]["evidence_a"] = ""
    payload["verdicts"][0]["evidence_b"] = ""
    _validate_pairwise(payload)


# --- normalize helpers ---

def test_normalize_blind_judge_payload_list_input() -> None:
    raw = {
        "blind_label": "x",
        "summary": "ok",
        "scores": [
            {"criterion": "Diagnosis", "score": 4, "rationale": "good", "evidence": "quote"},
        ],
    }
    result = normalize_blind_judge_payload(raw, blind_label="x")
    assert result["scores"]["Diagnosis"] == {"score": 4, "rationale": "good", "evidence": "quote"}


def test_normalize_blind_judge_payload_dict_input_bare_int() -> None:
    raw = {
        "blind_label": "x",
        "summary": "ok",
        "scores": {"Diagnosis": 3},
    }
    result = normalize_blind_judge_payload(raw, blind_label="x")
    assert result["scores"]["Diagnosis"]["score"] == 3
    assert result["scores"]["Diagnosis"]["rationale"] == ""


def test_normalize_pairwise_judge_payload() -> None:
    raw = {
        "blind_label_a": "a",
        "blind_label_b": "b",
        "summary": "ok",
        "verdicts": [
            {
                "criterion": "Diagnosis",
                "winner": "A",
                "margin": "decisive",
                "rationale": "A was correct",
                "evidence_a": "quote a",
                "evidence_b": "",
            }
        ],
    }
    result = normalize_pairwise_judge_payload(raw, blind_label_a="a", blind_label_b="b")
    assert len(result["verdicts"]) == 1
    assert result["verdicts"][0]["winner"] == "A"
    assert result["verdicts"][0]["criterion"] == "Diagnosis"
