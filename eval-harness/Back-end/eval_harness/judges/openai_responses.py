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
from ..models import BlindJudgeRequest, BlindJudgeResult, PairwiseJudgeRequest, PairwiseJudgeResult
from ..openai_responses import OpenAIResponsesClient, OpenAIResponsesClientConfig


def _blind_judge_schema() -> dict[str, Any]:
    return blind_judge_absolute_schema()


@dataclass(frozen=True)
class OpenAIResponsesBlindJudgeConfig:
    model: str
    api_key: str
    base_url: str | None = None
    request_timeout_seconds: float = 60.0
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None


class OpenAIResponsesBlindJudge(BlindJudge):
    name = "openai_responses_blind_judge"

    def __init__(self, config: OpenAIResponsesBlindJudgeConfig):
        self.config = config
        self.client = OpenAIResponsesClient(
            OpenAIResponsesClientConfig(
                model=config.model,
                api_key=config.api_key,
                base_url=config.base_url,
                request_timeout_seconds=config.request_timeout_seconds,
                max_output_tokens=config.max_output_tokens,
                reasoning_effort=config.reasoning_effort,
                temperature=0.0,
            )
        )

    def _request_json_schema(
        self,
        *,
        instructions: str,
        user_input: str,
        schema_name: str,
        schema: dict[str, Any],
        schema_description: str = "",
    ) -> dict[str, Any]:
        return self.client.request_json(
            instructions=instructions,
            user_input=user_input,
            schema_name=schema_name,
            schema=schema,
            schema_description=schema_description,
        )

    def grade(self, request: BlindJudgeRequest) -> BlindJudgeResult:
        payload = self._request_json_schema(
            instructions=build_absolute_instructions(request.rubric),
            user_input=json.dumps(request.to_dict(), indent=2),
            schema_name="blind_judge_result",
            schema=blind_judge_absolute_schema(),
            schema_description="Blind benchmark grading result.",
        )
        return BlindJudgeResult.from_dict(normalize_blind_judge_payload(payload, blind_label=request.blind_label))

    def compare(self, request: PairwiseJudgeRequest) -> PairwiseJudgeResult:
        payload = self._request_json_schema(
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
