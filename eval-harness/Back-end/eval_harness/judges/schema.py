from __future__ import annotations

from typing import Any


def blind_judge_absolute_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "blind_label": {"type": "string"},
            "summary": {"type": "string"},
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {"type": "string"},
                        "score": {"type": "integer", "minimum": 0, "maximum": 4},
                        "rationale": {"type": "string", "minLength": 1},
                        "evidence": {"type": "string"},
                    },
                    "required": ["criterion", "score", "rationale", "evidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["blind_label", "summary", "scores"],
        "additionalProperties": False,
    }


def blind_judge_pairwise_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "blind_label_a": {"type": "string"},
            "blind_label_b": {"type": "string"},
            "summary": {"type": "string"},
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {"type": "string"},
                        "winner": {"type": "string", "enum": ["A", "B", "tie"]},
                        "margin": {"type": "string", "enum": ["slight", "clear", "decisive"]},
                        "rationale": {"type": "string", "minLength": 1},
                        "evidence_a": {"type": "string"},
                        "evidence_b": {"type": "string"},
                    },
                    "required": ["criterion", "winner", "margin", "rationale", "evidence_a", "evidence_b"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["blind_label_a", "blind_label_b", "summary", "verdicts"],
        "additionalProperties": False,
    }


def blind_judge_schema() -> dict[str, Any]:
    return blind_judge_absolute_schema()


def normalize_blind_judge_payload(payload: dict[str, Any], *, blind_label: str) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.setdefault("blind_label", blind_label)

    raw_scores = normalized.get("scores", {})
    if isinstance(raw_scores, list):
        score_map: dict[str, Any] = {}
        for item in raw_scores:
            if not isinstance(item, dict):
                continue
            criterion = str(item.get("criterion", "")).strip()
            if not criterion:
                continue
            score_map[criterion] = {
                "score": item.get("score"),
                "rationale": item.get("rationale", ""),
                "evidence": item.get("evidence", ""),
            }
        normalized["scores"] = score_map
    elif isinstance(raw_scores, dict):
        coerced: dict[str, Any] = {}
        for criterion, value in raw_scores.items():
            if isinstance(value, dict):
                coerced[criterion] = {
                    "score": value.get("score"),
                    "rationale": value.get("rationale", ""),
                    "evidence": value.get("evidence", ""),
                }
            else:
                coerced[criterion] = {"score": value, "rationale": "", "evidence": ""}
        normalized["scores"] = coerced
    else:
        normalized["scores"] = {}

    normalized["raw_response"] = dict(normalized)
    return normalized


def normalize_pairwise_judge_payload(
    payload: dict[str, Any], *, blind_label_a: str, blind_label_b: str
) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.setdefault("blind_label_a", blind_label_a)
    normalized.setdefault("blind_label_b", blind_label_b)

    raw_verdicts = normalized.get("verdicts", [])
    if isinstance(raw_verdicts, list):
        verdict_list = []
        for item in raw_verdicts:
            if not isinstance(item, dict):
                continue
            criterion = str(item.get("criterion", "")).strip()
            if not criterion:
                continue
            verdict_list.append({
                "criterion": criterion,
                "winner": str(item.get("winner", "tie")),
                "margin": str(item.get("margin", "slight")),
                "rationale": str(item.get("rationale", "")),
                "evidence_a": str(item.get("evidence_a", "")),
                "evidence_b": str(item.get("evidence_b", "")),
            })
        normalized["verdicts"] = verdict_list
    elif not isinstance(raw_verdicts, list):
        normalized["verdicts"] = []

    normalized["raw_response"] = dict(normalized)
    return normalized
