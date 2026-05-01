from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .base import BlindJudge
from ._prompts import build_absolute_instructions, build_pairwise_instructions
from .schema import (
    blind_judge_absolute_schema,
    blind_judge_pairwise_schema,
    normalize_blind_judge_payload,
    normalize_pairwise_judge_payload,
)
from ..google_genai_llm import GoogleGenAIStructuredOutputClient, GoogleGenAIStructuredOutputClientConfig
from ..models import BlindJudgeRequest, BlindJudgeResult, PairwiseJudgeRequest, PairwiseJudgeResult


def _blind_judge_schema() -> dict[str, Any]:
    return blind_judge_absolute_schema()


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
                temperature=0.0,
            )
        )

    def grade(self, request: BlindJudgeRequest) -> BlindJudgeResult:
        payload = self.client.request_json(
            instructions=build_absolute_instructions(request.rubric),
            user_input=json.dumps(request.to_dict(), indent=2),
            schema_name="blind_judge_result",
            schema=blind_judge_absolute_schema(),
            schema_description="Blind benchmark grading result.",
        )
        return BlindJudgeResult.from_dict(normalize_blind_judge_payload(payload, blind_label=request.blind_label))

    def compare(self, request: PairwiseJudgeRequest) -> PairwiseJudgeResult:
        payload = self.client.request_json(
            instructions=build_pairwise_instructions(request.rubric),
            user_input=json.dumps(request.to_dict(), indent=2),
            schema_name="pairwise_judge_result",
            schema=blind_judge_pairwise_schema(),
            schema_description="Blind benchmark pairwise verdict.",
        )
        normalized = normalize_pairwise_judge_payload(
            payload,
            blind_label_a=request.blind_label_a,
            blind_label_b=request.blind_label_b,
        )
        return PairwiseJudgeResult.from_dict(normalized)
