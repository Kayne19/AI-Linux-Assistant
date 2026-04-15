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


def test_generate_scenario_raises_if_repair_is_still_invalid() -> None:
    planner = FakePlanner(
        [
            {"scenario_name": "nginx-service-repair", "target_image": "debian-12-openclaw-golden"},
            {"scenario_name": "still-invalid", "target_image": "debian-12-openclaw-golden"},
        ]
    )

    with pytest.raises(ScenarioValidationError, match="planner_payload="):
        planner.generate_scenario(_request())
