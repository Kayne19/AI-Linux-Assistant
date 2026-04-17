from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .base import ScenarioPlanner
from ..anthropic_llm import AnthropicStructuredOutputClient, AnthropicStructuredOutputClientConfig
from ..models import (
    CommandExecutionResult,
    PlannerReviewDecision,
    PlannerScenarioRequest,
    ScenarioSpec,
)
from ..scenario import ScenarioValidationError, validate_scenario


def _verification_check_schema(*, description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "properties": {
            "name": {"type": "string"},
            "command": {"type": "string"},
            "intent": {"type": "string"},
            "expected_substrings": {"type": "array", "items": {"type": "string"}},
            "expected_regexes": {"type": "array", "items": {"type": "string"}},
            "unexpected_substrings": {"type": "array", "items": {"type": "string"}},
            "unexpected_regexes": {"type": "array", "items": {"type": "string"}},
            "expected_exact_match": {"type": ["string", "null"]},
            "expected_exit_code": {"type": ["integer", "null"]},
            "match_mode": {"type": "string", "enum": ["all", "any"]},
            "timeout_seconds": {"type": "integer"},
        },
        "required": ["name", "command", "intent"],
        "additionalProperties": False,
    }


def _scenario_schema(*, description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "properties": {
            "scenario_name": {"type": "string"},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "what_it_tests": {"type": "array", "items": {"type": "string"}},
            "target_image": {"type": "string"},
            "observable_problem_statement": {"type": "string"},
            "sabotage_procedure": {"type": "array", "items": {"type": "string"}},
            "verification_probes": {
                "type": "array",
                "items": _verification_check_schema(description="Pre-clone broken-state verification probe."),
            },
            "repair_checks": {
                "type": "array",
                "items": _verification_check_schema(description="Objective repaired-state verification probe."),
            },
            "judge_rubric": {"type": "array", "items": {"type": "string"}},
            "turn_budget": {"type": "integer"},
            "context_seed": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["role", "content"],
                    "additionalProperties": False,
                },
            },
            "initial_diagnostic_commands": {"type": "array", "items": {"type": "string"}},
            "metadata": {"type": "object", "additionalProperties": True},
            "planner_metadata": {"type": "object", "additionalProperties": True},
        },
        "required": [
            "scenario_name",
            "title",
            "summary",
            "what_it_tests",
            "target_image",
            "observable_problem_statement",
            "sabotage_procedure",
            "verification_probes",
            "repair_checks",
            "judge_rubric",
            "turn_budget",
        ],
        "additionalProperties": False,
    }


def _planner_review_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": ["approve", "correct"]},
            "summary": {"type": "string"},
            "correction_instructions": {"type": "array", "items": {"type": "string"}},
            "updated_observable_problem_statement": {"type": "string"},
            "metadata": {"type": "object", "additionalProperties": True},
        },
        "required": ["outcome", "summary", "correction_instructions", "updated_observable_problem_statement"],
        "additionalProperties": False,
    }


def _planner_rectification_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "commands": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["commands"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class AnthropicScenarioPlannerConfig:
    model: str
    api_key: str
    base_url: str | None = None
    request_timeout_seconds: float = 60.0
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None


class AnthropicScenarioPlanner(ScenarioPlanner):
    name = "anthropic_planner"

    def __init__(self, config: AnthropicScenarioPlannerConfig):
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

    def _scenario_generation_prompt(self) -> str:
        return (
            "You are a benchmark planner for Linux troubleshooting environments. "
            "Return only a fully populated structured scenario object. "
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
            "repair_checks must collectively cover end-to-end functional restoration of the user's stated problem. If the observable problem is a user-visible action (e.g. 'I cannot SSH in,' 'curl to my service times out,' 'my cron job never runs'), at least one repair_check must actually attempt that action and verify it succeeds — not merely assert that a related daemon is loaded or active. State-level checks (systemctl is-active, ss -tlnp, file existence) are allowed in addition to a functional check, but never as the sole evidence of repair. "
            "The observable problem statement must not reveal the sabotage method. "
            "Keep the broken state objectively verifiable before cloning."
        )

    def _scenario_repair_prompt(self) -> str:
        return (
            "You previously returned invalid structured scenario data. "
            "Return corrected structured data only. "
            "Preserve the user's target_image and produce a fully runnable scenario object that passes validation. "
            "Remember: repair_checks must collectively cover end-to-end functional restoration of the user's stated problem. If the observable problem is a user-visible action (e.g. 'I cannot SSH in,' 'curl to my service times out,' 'my cron job never runs'), at least one repair_check must actually attempt that action and verify it succeeds — not merely assert that a related daemon is loaded or active. State-level checks (systemctl is-active, ss -tlnp, file existence) are allowed in addition to a functional check, but never as the sole evidence of repair."
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
            repaired_payload = self._request_json_schema(
                instructions=self._scenario_repair_prompt(),
                user_input=json.dumps(
                    {
                        "request": request.to_dict(),
                        "validation_errors": str(exc),
                        "invalid_payload": payload,
                    },
                    indent=2,
                ),
                schema_name="planner_scenario_repair",
                schema=_scenario_schema(description="Corrected runnable troubleshooting scenario."),
                schema_description="Corrected troubleshooting scenario.",
            )
            return self._validated_scenario_from_payload(
                repaired_payload,
                request=request,
                allow_repair=False,
            )

    def generate_scenario(self, request: PlannerScenarioRequest) -> ScenarioSpec:
        payload = self._request_json_schema(
            instructions=self._scenario_generation_prompt(),
            user_input=json.dumps(request.to_dict(), indent=2),
            schema_name="planner_scenario",
            schema=_scenario_schema(description="Runnable troubleshooting benchmark scenario."),
            schema_description="Runnable troubleshooting benchmark scenario.",
        )
        return self._validated_scenario_from_payload(payload, request=request, allow_repair=True)

    def review_sabotage(
        self,
        scenario: ScenarioSpec,
        *,
        round_index: int,
        command_results: tuple[CommandExecutionResult, ...],
        correction_count: int,
    ) -> PlannerReviewDecision:
        instructions = (
            "You are reviewing whether a sabotage was applied correctly. "
            "Return a structured decision with outcome=approve or outcome=correct. "
            "If correcting, provide comprehensive correction_instructions."
        )
        payload = self._request_json_schema(
            instructions=instructions,
            user_input=json.dumps(
                {
                    "scenario": scenario.to_dict(),
                    "round_index": round_index,
                    "correction_count": correction_count,
                    "command_results": [result.to_dict() for result in command_results],
                },
                indent=2,
            ),
            schema_name="planner_review_decision",
            schema=_planner_review_schema(),
            schema_description="Planner sabotage verification decision.",
        )
        return PlannerReviewDecision.from_dict(payload)

    def plan_rectification(
        self,
        scenario: ScenarioSpec,
        *,
        failed_command_results: tuple[CommandExecutionResult, ...],
        correction_instructions: tuple[str, ...],
        round_index: int,
    ) -> tuple[str, ...]:
        instructions = (
            "You are a benchmark setup planner generating rectification commands for a Linux eval harness. "
            "Return a structured object with key 'commands' containing concrete shell commands only."
        )
        payload = self._request_json_schema(
            instructions=instructions,
            user_input=json.dumps(
                {
                    "scenario": scenario.to_dict(),
                    "failed_command_results": [result.to_dict() for result in failed_command_results],
                    "correction_instructions": list(correction_instructions),
                    "round_index": round_index,
                },
                indent=2,
            ),
            schema_name="planner_rectification",
            schema=_planner_rectification_schema(),
            schema_description="Planner rectification command list.",
        )
        commands = payload.get("commands", [])
        if not isinstance(commands, list):
            raise RuntimeError(
                f"plan_rectification: planner returned malformed payload (expected 'commands' list): {json.dumps(payload)[:400]!r}"
            )
        return tuple(str(item).strip() for item in commands if str(item).strip())
