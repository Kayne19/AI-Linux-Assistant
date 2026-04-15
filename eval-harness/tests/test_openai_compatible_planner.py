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
from eval_harness.planners.openai_compatible import OpenAICompatibleScenarioPlanner, OpenAICompatibleScenarioPlannerConfig
from eval_harness.scenario import ScenarioValidationError


class FakePlanner(OpenAICompatibleScenarioPlanner):
    def __init__(self, responses: list[dict]):
        super().__init__(
            OpenAICompatibleScenarioPlannerConfig(
                base_url="https://example.invalid/v1",
                model="test-model",
                api_key="test-key",
            )
        )
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def _request_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.calls.append((system_prompt, user_prompt))
        return self.responses.pop(0)


def _request() -> PlannerScenarioRequest:
    return PlannerScenarioRequest(
        planning_brief="Break nginx in a machine-verifiable way.",
        target_image="debian-12-openclaw-golden",
        scenario_name_hint="nginx-service-repair",
    )


def _valid_payload() -> dict:
    return {
        "scenario_name": "nginx-service-repair",
        "title": "Nginx service fails after a bad override",
        "summary": "The environment contains a broken nginx override that prevents startup.",
        "what_it_tests": ["systemd troubleshooting"],
        "target_image": "debian-12-openclaw-golden",
        "observable_problem_statement": "The website is down and nginx will not start.",
        "sabotage_procedure": ["Write a bad systemd override for nginx and reload systemd."],
        "verification_probes": [
            {
                "name": "nginx-broken",
                "command": "systemctl is-active nginx || true",
                "expected_substrings": ["inactive", "failed"],
                "match_mode": "any",
            }
        ],
        "repair_checks": [
            {
                "name": "nginx-fixed",
                "command": "systemctl is-active nginx",
                "expected_substrings": ["active"],
            }
        ],
        "judge_rubric": ["Diagnosed the failure accurately."],
        "turn_budget": 6,
    }


def test_generate_scenario_repairs_invalid_initial_payload() -> None:
    planner = FakePlanner(
        [
            {"scenario_name": "nginx-service-repair", "target_image": "debian-12-openclaw-golden"},
            _valid_payload(),
        ]
    )

    scenario = planner.generate_scenario(_request())

    assert scenario.title == "Nginx service fails after a bad override"
    assert len(planner.calls) == 2
    assert "validation_errors" in planner.calls[1][1]
    assert scenario.verification_probes[0].command.startswith("bash -lc 'state=$(systemctl show -p ActiveState --value nginx")
    assert scenario.verification_probes[0].match_mode.value == "any"


def test_generate_scenario_raises_if_repair_is_still_invalid() -> None:
    planner = FakePlanner(
        [
            {"scenario_name": "nginx-service-repair", "target_image": "debian-12-openclaw-golden"},
            {"scenario_name": "still-invalid", "target_image": "debian-12-openclaw-golden"},
        ]
    )

    with pytest.raises(ScenarioValidationError, match="planner_payload="):
        planner.generate_scenario(_request())


def test_generate_scenario_does_not_rewrite_nginx_probe_command() -> None:
    # nginx-specific rewrites were removed; the command must be stored verbatim.
    original_command = "bash -lc 'nginx -t >/tmp/nginx-test.out 2>&1; rc=$?; cat /tmp/nginx-test.out; exit $rc'"
    payload = _valid_payload()
    payload["verification_probes"] = [
        {
            "name": "nginx-config-broken",
            "command": original_command,
            "expected_exit_code": 1,
            "expected_substrings": ["emerg", "/etc/nginx/"],
        }
    ]
    planner = FakePlanner([payload])

    scenario = planner.generate_scenario(_request())

    assert scenario.verification_probes[0].command == original_command
