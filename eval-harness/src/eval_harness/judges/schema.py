from __future__ import annotations

from typing import Any


def blind_judge_schema() -> dict[str, Any]:
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
                        "score": {"type": "integer"},
                    },
                    "required": ["criterion", "score"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["blind_label", "summary", "scores"],
        "additionalProperties": False,
    }


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
            score_map[criterion] = item.get("score")
        normalized["scores"] = score_map
    elif not isinstance(raw_scores, dict):
        normalized["scores"] = {}

    normalized["raw_response"] = dict(normalized)
    return normalized
