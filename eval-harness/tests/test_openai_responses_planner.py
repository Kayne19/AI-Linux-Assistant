from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.models import PlannerScenarioRequest
from eval_harness.planners.openai_responses import OpenAIResponsesScenarioPlanner, OpenAIResponsesScenarioPlannerConfig
from eval_harness.scenario import ScenarioValidationError


class FakePlanner(OpenAIResponsesScenarioPlanner):
    def __init__(self, responses: list[dict]):
        super().__init__(
            OpenAIResponsesScenarioPlannerConfig(
                model="test-model",
                api_key="test-key",
            )
        )
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def _request_json_schema(
        self,
        *,
        instructions: str,
        user_input: str,
        schema_name: str,
        schema: dict,
        schema_description: str = "",
    ) -> dict:
        self.calls.append(
            {
                "instructions": instructions,
                "user_input": user_input,
                "schema_name": schema_name,
                "schema": schema,
                "schema_description": schema_description,
            }
        )
        return self.responses.pop(0)


def _request() -> PlannerScenarioRequest:
    return PlannerScenarioRequest(
        planning_brief="Break nginx in a machine-verifiable way.",
        target_image="debian-12-ssm-golden",
        scenario_name_hint="nginx-service-repair",
    )


def _valid_payload() -> dict:
    return {
        "scenario_name": "nginx-service-repair",
        "title": "Nginx service fails after a bad override",
        "summary": "The environment contains a broken nginx override that prevents startup.",
        "what_it_tests": ["systemd troubleshooting"],
        "target_image": "debian-12-ssm-golden",
        "observable_problem_statement": "The website is down and nginx will not start.",
        "sabotage_procedure": ["Write a bad systemd override for nginx and reload systemd."],
        "verification_probes": [
            {
                "name": "nginx-broken",
                "command": "systemctl is-active nginx || true",
                "intent": "Confirm nginx is not active before cloning.",
                "expected_substrings": ["inactive", "failed"],
                "match_mode": "any",
            }
        ],
        "repair_checks": [
            {
                "name": "nginx-fixed",
                "command": "systemctl is-active nginx",
                "intent": "Confirm nginx is active again.",
                "expected_substrings": ["active"],
            }
        ],
        "judge_rubric": ["Diagnosed the failure accurately."],
        "turn_budget": 6,
    }


def test_generate_scenario_repairs_invalid_initial_payload() -> None:
    planner = FakePlanner(
        [
            {"scenario_name": "nginx-service-repair", "target_image": "debian-12-ssm-golden"},
            _valid_payload(),
        ]
    )

    scenario = planner.generate_scenario(_request())

    assert scenario.title == "Nginx service fails after a bad override"
    assert len(planner.calls) == 2
    assert planner.calls[0]["schema_name"] == "planner_scenario"
    assert planner.calls[1]["schema_name"] == "planner_scenario_repair"
    assert "validation_errors" in str(planner.calls[1]["user_input"])
    assert scenario.verification_probes[0].command.startswith("bash -lc 'state=$(systemctl show -p ActiveState --value nginx")
    assert scenario.verification_probes[0].match_mode.value == "any"


def test_generate_scenario_raises_if_repair_is_still_invalid() -> None:
    planner = FakePlanner(
        [
            {"scenario_name": "nginx-service-repair", "target_image": "debian-12-ssm-golden"},
            {"scenario_name": "still-invalid", "target_image": "debian-12-ssm-golden"},
        ]
    )

    with pytest.raises(ScenarioValidationError, match="planner_payload="):
        planner.generate_scenario(_request())


def test_generate_scenario_does_not_rewrite_nginx_probe_command() -> None:
    original_command = "bash -lc 'nginx -t >/tmp/nginx-test.out 2>&1; rc=$?; cat /tmp/nginx-test.out; exit $rc'"
    payload = _valid_payload()
    payload["verification_probes"] = [
        {
            "name": "nginx-config-broken",
            "command": original_command,
            "intent": "Verify nginx config test fails.",
            "expected_exit_code": 1,
            "expected_substrings": ["emerg", "/etc/nginx/"],
        }
    ]
    planner = FakePlanner([payload])

    scenario = planner.generate_scenario(_request())

    assert scenario.verification_probes[0].command == original_command


def test_generation_prompt_requires_generic_end_state_checks_without_service_hardcoding() -> None:
    planner = FakePlanner([_valid_payload()])

    prompt = planner._scenario_generation_prompt()

    assert "repair checks must validate the repaired end state" in prompt.lower()
    assert "at least one repair check must be user-visible or symptom-level" in prompt.lower()
    assert "do not include a repair check that can fail on an otherwise repaired system" in prompt.lower()
    assert "if a check needs elevated privileges" in prompt.lower()
    assert "before finalizing repair_checks, ask whether any check could fail even though the system is repaired" in prompt.lower()
    assert "nginx-specific" not in prompt.lower()


def test_review_and_rectification_use_structured_schemas() -> None:
    planner = FakePlanner(
        [
            _valid_payload(),
            {
                "outcome": "correct",
                "summary": "Probe output still shows nginx active.",
                "correction_instructions": ["Break the nginx override more explicitly."],
                "updated_observable_problem_statement": "",
            },
            {
                "commands": [
                    "sudo mkdir -p /etc/systemd/system/nginx.service.d",
                    "sudo systemctl daemon-reload",
                ]
            },
        ]
    )
    scenario = planner.generate_scenario(_request())

    review = planner.review_sabotage(
        scenario,
        round_index=1,
        correction_count=0,
        command_results=(),
    )
    commands = planner.plan_rectification(
        scenario,
        failed_command_results=(),
        correction_instructions=("Break it more.",),
        round_index=2,
    )

    assert review.outcome.value == "correct"
    assert planner.calls[1]["schema_name"] == "planner_review_decision"
    assert planner.calls[2]["schema_name"] == "planner_rectification"
    assert commands == (
        "sudo mkdir -p /etc/systemd/system/nginx.service.d",
        "sudo systemctl daemon-reload",
    )
