from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .base import BlindJudge
from .schema import blind_judge_schema, normalize_blind_judge_payload
from ..anthropic_llm import AnthropicStructuredOutputClient, AnthropicStructuredOutputClientConfig
from ..models import BlindJudgeRequest, BlindJudgeResult, PairwiseJudgeRequest, PairwiseJudgeResult


def _blind_judge_schema() -> dict[str, Any]:
    return blind_judge_schema()


@dataclass(frozen=True)
class AnthropicBlindJudgeConfig:
    model: str
    api_key: str
    base_url: str | None = None
    request_timeout_seconds: float = 60.0
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None


class AnthropicBlindJudge(BlindJudge):
    name = "anthropic_blind_judge"

    def __init__(self, config: AnthropicBlindJudgeConfig):
        self.config = config
        self.client = AnthropicStructuredOutputClient(
            AnthropicStructuredOutputClientConfig(
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
                "Return structured data with blind_label, summary, and scores. "
                "The scores field must be an array with one item per rubric entry. "
                "Each item must contain the rubric text unchanged in criterion and an integer score."
            ),
            user_input=json.dumps(request.to_dict(), indent=2),
            schema_name="blind_judge_result",
            schema=_blind_judge_schema(),
            schema_description="Blind benchmark grading result.",
        )
        return BlindJudgeResult.from_dict(normalize_blind_judge_payload(payload, blind_label=request.blind_label))

    def compare(self, request: PairwiseJudgeRequest) -> PairwiseJudgeResult:
        raise NotImplementedError("phase 2")
