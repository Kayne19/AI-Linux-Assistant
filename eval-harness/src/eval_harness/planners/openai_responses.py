from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .base import ScenarioPlanner
from ..models import (
    CommandExecutionResult,
    InitialUserMessageDraft,
    InitialUserMessageReview,
    PlannerReviewDecision,
    PlannerScenarioRequest,
    ScenarioSpec,
)
from ..openai_responses import OpenAIResponsesClient, OpenAIResponsesClientConfig
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
        "required": [
            "name", "command", "intent",
            "expected_substrings", "expected_regexes",
            "unexpected_substrings", "unexpected_regexes",
            "expected_exact_match", "expected_exit_code",
            "match_mode", "timeout_seconds",
        ],
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
            "metadata": {"type": "object", "properties": {}, "additionalProperties": False},
            "planner_metadata": {"type": "object", "properties": {}, "additionalProperties": False},
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
            "context_seed",
            "initial_diagnostic_commands",
            "metadata",
            "planner_metadata",
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
            "updated_verification_probes": {
                "type": "array",
                "items": _verification_check_schema(description="Normalized pre-clone broken-state verification probe."),
            },
            "metadata": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        "required": [
            "outcome",
            "summary",
            "correction_instructions",
            "updated_observable_problem_statement",
            "updated_verification_probes",
            "metadata",
        ],
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


def _initial_user_message_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
        },
        "required": ["message"],
        "additionalProperties": False,
    }


def _initial_user_message_review_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": ["approve", "rewrite"]},
            "notes": {"type": "string"},
            "final_message": {"type": "string"},
        },
        "required": ["outcome", "notes", "final_message"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class OpenAIResponsesScenarioPlannerConfig:
    model: str
    api_key: str
    base_url: str | None = None
    request_timeout_seconds: float = 60.0
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None
    web_search_enabled: bool = True


class OpenAIResponsesScenarioPlanner(ScenarioPlanner):
    name = "openai_responses_planner"

    def __init__(self, config: OpenAIResponsesScenarioPlannerConfig):
        self.config = config
        self.client = OpenAIResponsesClient(
            OpenAIResponsesClientConfig(
                model=config.model,
                api_key=config.api_key,
                base_url=config.base_url,
                request_timeout_seconds=config.request_timeout_seconds,
                max_output_tokens=config.max_output_tokens,
                reasoning_effort=config.reasoning_effort if config.reasoning_effort is not None else "xhigh",
            )
        )

    def _responses_tools(self) -> tuple[dict[str, Any], ...] | None:
        if not self.config.web_search_enabled:
            return None
        return ({"type": "web_search"},)

    def _request_json_schema(
        self,
        *,
        instructions: str,
        user_input: str,
        schema_name: str,
        schema: dict[str, Any],
        schema_description: str = "",
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> dict[str, Any]:
        return self.client.request_json(
            instructions=instructions,
            user_input=user_input,
            schema_name=schema_name,
            schema=schema,
            schema_description=schema_description,
            tools=tools,
        )

    def _scenario_generation_prompt(self) -> str:
        return (
            "You are a benchmark planner for Linux troubleshooting environments. "
            "Return only a fully populated structured scenario object. "
            "Required top-level keys: "
            "scenario_name, title, summary, what_it_tests, target_image, observable_problem_statement, "
            "sabotage_procedure, verification_probes, repair_checks, judge_rubric, turn_budget. "
            "sabotage_procedure must contain raw executable shell commands or shell snippets only, with no prose, no markdown, and no backticks. "
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
            "The observable_problem_statement must be written as a frustrated non-expert user describing what they saw and tried — not as an expert explaining the root cause. "
            "It MAY include verbatim terminal error output the user would naturally have seen (e.g. a failed systemctl status snippet, a curl error, a log line they pasted), because that is what a real user would share. "
            "It must NOT include: the diagnosis of why the error occurred, specific config file paths or line numbers to fix, or any statement that tells the subject where to look or what to change. "
            "The error output gives surface-level evidence; the subject must still figure out the cause and the fix. "
            "Example (correct): 'My website went down. I tried restarting nginx and got: Job for nginx.service failed. See `systemctl status nginx.service` for details.' "
            "Example (incorrect): 'nginx will not start because /etc/nginx/sites-enabled/default is missing a closing brace — fix that file.' "
            "Keep the broken state objectively verifiable before cloning."
        )

    def _scenario_repair_prompt(self) -> str:
        return (
            "You previously returned invalid structured scenario data. "
            "Return corrected structured data only. "
            "Preserve the user's target_image and produce a fully runnable scenario object that passes validation. "
            "Keep sabotage_procedure as raw executable shell commands or shell snippets only, with no prose, no markdown, and no backticks. "
            "Remember: repair_checks must collectively cover end-to-end functional restoration of the user's stated problem. If the observable problem is a user-visible action (e.g. 'I cannot SSH in,' 'curl to my service times out,' 'my cron job never runs'), at least one repair_check must actually attempt that action and verify it succeeds — not merely assert that a related daemon is loaded or active. State-level checks (systemctl is-active, ss -tlnp, file existence) are allowed in addition to a functional check, but never as the sole evidence of repair."
        )

    def _initial_user_message_prompt(self) -> str:
        return (
            "You are writing the canonical first user turn for a Linux troubleshooting benchmark. "
            "Return a structured object with a single key 'message'. "
            "Write as the end user who is experiencing the issue, in natural first-person language. "
            "Describe visible symptoms and what the user plausibly tried. "
            "Use only information a real user could have observed. "
            "Do not reveal hidden scenario setup, sabotage steps, exact fixes, or internal benchmark design details."
        )

    def _initial_user_message_review_prompt(self) -> str:
        return (
            "You are reviewing a benchmark's candidate opening user message for realism and information leakage. "
            "Return a structured object with outcome=approve or outcome=rewrite, notes, and final_message. "
            "Approve only if the draft sounds like a real frustrated user and does not reveal hidden causes, sabotage details, or fix instructions. "
            "Rewrite when needed so the final_message stays natural while preserving only user-observable evidence."
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
                tools=self._responses_tools(),
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
            tools=self._responses_tools(),
        )
        return self._validated_scenario_from_payload(payload, request=request, allow_repair=True)

    def generate_initial_user_message(
        self,
        *,
        scenario: ScenarioSpec,
        hidden_context: dict[str, Any],
    ) -> InitialUserMessageDraft:
        payload = self._request_json_schema(
            instructions=self._initial_user_message_prompt(),
            user_input=json.dumps(
                {
                    "scenario": scenario.to_dict(),
                    "hidden_context": hidden_context,
                },
                indent=2,
            ),
            schema_name="planner_initial_user_message",
            schema=_initial_user_message_schema(),
            schema_description="Canonical first-turn benchmark user message.",
            tools=self._responses_tools(),
        )
        return InitialUserMessageDraft.from_dict(payload)

    def review_initial_user_message(
        self,
        *,
        scenario: ScenarioSpec,
        draft_message: str,
    ) -> InitialUserMessageReview:
        payload = self._request_json_schema(
            instructions=self._initial_user_message_review_prompt(),
            user_input=json.dumps(
                {
                    "scenario": scenario.to_dict(),
                    "draft_message": draft_message,
                },
                indent=2,
            ),
            schema_name="planner_initial_user_message_review",
            schema=_initial_user_message_review_schema(),
            schema_description="Self-review for a canonical first-turn benchmark user message.",
            tools=self._responses_tools(),
        )
        return InitialUserMessageReview.from_dict(payload)

    def review_sabotage(
        self,
        scenario: ScenarioSpec,
        *,
        round_index: int,
        command_results: tuple[CommandExecutionResult, ...],
        correction_count: int,
        verification_snapshot: dict[str, Any] | None = None,
    ) -> PlannerReviewDecision:
        instructions = (
            "You are reviewing whether a sabotage was applied correctly. "
            "Return a structured decision with outcome=approve or outcome=correct. "
            "If the machine is in the intended broken state but the verification probes are brittle, "
            "approve and rewrite updated_verification_probes to make them robust across equivalent outputs. "
            "Only return updated_verification_probes when the sabotage is correct and the matcher is the problem. "
            "If correcting, provide comprehensive correction_instructions and leave updated_verification_probes empty."
        )
        payload = self._request_json_schema(
            instructions=instructions,
            user_input=json.dumps(
                {
                    "scenario": scenario.to_dict(),
                    "round_index": round_index,
                    "correction_count": correction_count,
                    "verification_snapshot": verification_snapshot or {},
                    "command_results": [item.to_dict() for item in command_results],
                },
                indent=2,
            ),
            schema_name="planner_review_decision",
            schema=_planner_review_schema(),
            schema_description="Planner review of sabotage verification results.",
            tools=self._responses_tools(),
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
            "The sabotage verification probes failed. "
            "Return a structured object with key 'commands' containing concrete shell commands "
            "that bring the broken state back to match the scenario's observable_problem_statement "
            "and make the verification probes succeed. "
            "Do not include empty strings."
        )
        payload = self._request_json_schema(
            instructions=instructions,
            user_input=json.dumps(
                {
                    "scenario": {
                        "summary": scenario.summary,
                        "target_image": scenario.target_image,
                        "observable_problem_statement": scenario.observable_problem_statement,
                        "sabotage_procedure": list(scenario.sabotage_procedure),
                        "verification_probes": [item.to_dict() for item in scenario.verification_probes],
                        "repair_checks": [item.to_dict() for item in scenario.repair_checks],
                    },
                    "failed_command_results": [item.to_dict() for item in failed_command_results],
                    "correction_instructions": list(correction_instructions),
                    "round_index": round_index,
                },
                indent=2,
            ),
            schema_name="planner_rectification",
            schema=_planner_rectification_schema(),
            schema_description="Concrete rectification shell commands.",
            tools=self._responses_tools(),
        )
        commands = payload.get("commands")
        if not isinstance(commands, list):
            raise RuntimeError(
                f"plan_rectification: planner returned malformed payload (expected 'commands' list): "
                f"{json.dumps(payload)[:400]!r}"
            )
        return tuple(str(cmd).strip() for cmd in commands if str(cmd).strip())
