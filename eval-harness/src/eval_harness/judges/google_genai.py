from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .base import BlindJudge
from ..google_genai_llm import GoogleGenAIStructuredOutputClient, GoogleGenAIStructuredOutputClientConfig
from ..models import BlindJudgeRequest, BlindJudgeResult


def _blind_judge_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "blind_label": {"type": "string"},
            "summary": {"type": "string"},
            "scores": {"type": "object", "additionalProperties": True},
            "raw_response": {"type": "object", "additionalProperties": True},
            "metadata": {"type": "object", "additionalProperties": True},
        },
        "required": ["summary", "scores"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class GoogleGenAIBlindJudgeConfig:
    model: str
    api_key: str
    base_url: str | None = None
    request_timeout_seconds: float = 60.0
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None


class GoogleGenAIBlindJudge(BlindJudge):
    name = "google_genai_blind_judge"

    def __init__(self, config: GoogleGenAIBlindJudgeConfig):
        self.config = config
        self.client = GoogleGenAIStructuredOutputClient(
            GoogleGenAIStructuredOutputClientConfig(
                model=config.model,
                api_key=config.api_key,
                base_url=config.base_url,
                request_timeout_seconds=config.request_timeout_seconds,
                max_output_tokens=config.max_output_tokens,
                reasoning_effort=config.reasoning_effort,
            )
        )

    def grade(self, request: BlindJudgeRequest) -> BlindJudgeResult:
        payload = self.client.request_json(
            instructions=(
                "You are a blind benchmark judge. Grade the transcript against the rubric without inferring system identity. "
                "Return structured data with blind_label, summary, and scores."
            ),
            user_input=json.dumps(request.to_dict(), indent=2),
            schema_name="blind_judge_result",
            schema=_blind_judge_schema(),
            schema_description="Blind benchmark grading result.",
        )
        if "blind_label" not in payload:
            payload["blind_label"] = request.blind_label
        if "raw_response" not in payload:
            payload["raw_response"] = dict(payload)
        return BlindJudgeResult.from_dict(payload)
