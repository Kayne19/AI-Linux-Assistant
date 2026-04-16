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
from ..scenario import ScenarioValidationError, validate_scenario


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

    def _scenario_generation_prompt(self) -> str:
        return (
            "You are a benchmark planner for Linux troubleshooting environments. "
            "Return JSON only and include every required field. "
            "Required top-level keys: "
            "scenario_name, title, summary, what_it_tests, target_image, observable_problem_statement, "
            "sabotage_procedure, verification_probes, repair_checks, judge_rubric, turn_budget. "
            "what_it_tests, sabotage_procedure, judge_rubric must be non-empty arrays of strings. "
            "verification_probes and repair_checks must be non-empty arrays of objects with: "
            "name, command, and intent (explaining the explicit verification intent). "
            "They must also declare robust machine-checkable expectations using at least one of: "
            "expected_exit_code, expected_substrings, expected_regexes, unexpected_substrings, unexpected_regexes, or expected_exact_match. "
            "Repair checks must validate the repaired end state against the scenario's real success criteria, not a stricter incidental administrative invariant. "
            "At least one repair check must be user-visible or symptom-level whenever the scenario has a user-visible success condition. "
            "Do not include a repair check that can fail on an otherwise repaired system because of missing privileges, stale runtime files, or execution context alone. "
            "If a check needs elevated privileges, express that explicitly in the command itself rather than assuming an unprivileged equivalent is acceptable. "
            "Before finalizing repair_checks, ask whether any check could fail even though the system is repaired; if so, replace or remove it. "
            "Repair checks must be robust and functionally verify the system rather than relying strictly on generic service states. "
            "The observable problem statement must not reveal the sabotage method. "
            "Keep the broken state objectively verifiable before cloning."
        )

    def _scenario_repair_prompt(self) -> str:
        return (
            "You previously returned invalid scenario JSON. "
            "Return corrected JSON only. "
            "Do not explain or comment. "
            "Preserve the user's target_image and produce a fully runnable scenario object that passes validation."
        )

    def _validated_scenario_from_payload(
        self,
        payload: dict[str, Any],
        *,
        request: PlannerScenarioRequest,
        allow_repair: bool,
    ) -> ScenarioSpec:
        scenario = ScenarioSpec.from_dict(payload)
        try:
            validate_scenario(scenario)
            return scenario
        except ScenarioValidationError as exc:
            if not allow_repair:
                raise ScenarioValidationError(
                    f"{exc}; planner_payload={json.dumps(payload, sort_keys=True)}"
                ) from exc
            repaired_payload = self._request_json(
                self._scenario_repair_prompt(),
                json.dumps(
                    {
                        "request": request.to_dict(),
                        "validation_errors": str(exc),
                        "invalid_payload": payload,
                    },
                    indent=2,
                ),
            )
            return self._validated_scenario_from_payload(
                repaired_payload,
                request=request,
                allow_repair=False,
            )

    def generate_scenario(self, request: PlannerScenarioRequest) -> ScenarioSpec:
        payload = self._request_json(self._scenario_generation_prompt(), json.dumps(request.to_dict(), indent=2))
        return self._validated_scenario_from_payload(payload, request=request, allow_repair=True)

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
