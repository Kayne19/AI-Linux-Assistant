from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from .base import ScenarioPlanner
from ..models import (
    CommandExecutionResult,
    PlannerReviewDecision,
    PlannerScenarioRequest,
    ScenarioSpec,
)


def _extract_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(content)


def _extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"Expected JSON object in planner response, got: {text[:200]!r}")
    return json.loads(text[start:end + 1])


@dataclass(frozen=True)
class OpenAICompatibleScenarioPlannerConfig:
    base_url: str
    model: str
    api_key: str
    request_timeout_seconds: float = 60.0


class OpenAICompatibleScenarioPlanner(ScenarioPlanner):
    name = "openai_compatible_planner"

    def __init__(self, config: OpenAICompatibleScenarioPlannerConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            }
        )

    def _request_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        response = self.session.post(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            json={
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=self.config.request_timeout_seconds,
        )
        response.raise_for_status()
        return _extract_json_object(_extract_text(response.json()))

    def generate_scenario(self, request: PlannerScenarioRequest) -> ScenarioSpec:
        system_prompt = (
            "You are a benchmark planner for troubleshooting environments. "
            "Return JSON only. Design the scenario so the broken state is objectively verifiable. "
            "You must output sabotage steps, verification probes, repair checks, an observable user-facing problem statement, "
            "what the scenario tests, and a judge rubric."
        )
        payload = self._request_json(system_prompt, json.dumps(request.to_dict(), indent=2))
        return ScenarioSpec.from_dict(payload)

    def review_sabotage(
        self,
        scenario: ScenarioSpec,
        *,
        round_index: int,
        command_results: tuple[CommandExecutionResult, ...],
        correction_count: int,
    ) -> PlannerReviewDecision:
        system_prompt = (
            "You are reviewing whether a sabotage was applied correctly. "
            "Return JSON only with outcome=approve or outcome=correct. "
            "If correcting, provide comprehensive correction_instructions."
        )
        payload = self._request_json(
            system_prompt,
            json.dumps(
                {
                    "scenario": scenario.to_dict(),
                    "round_index": round_index,
                    "correction_count": correction_count,
                    "command_results": [item.to_dict() for item in command_results],
                },
                indent=2,
            ),
        )
        return PlannerReviewDecision.from_dict(payload)
