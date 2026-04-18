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

from eval_harness.models import PlannerScenarioRequest, ScenarioSpec
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


def _config() -> OpenAIResponsesScenarioPlannerConfig:
    return OpenAIResponsesScenarioPlannerConfig(
        model="test-model",
        api_key="test-key",
    )


def _valid_payload() -> dict:
    return {
        "scenario_name": "nginx-service-repair",
        "title": "Nginx service fails after a bad override",
        "summary": "The environment contains a broken nginx override that prevents startup.",
        "what_it_tests": ["systemd troubleshooting"],
        "target_image": "debian-12-ssm-golden",
        "observable_problem_statement": "The website is down and nginx will not start.",
        "sabotage_procedure": [
            "mkdir -p /etc/systemd/system/nginx.service.d",
            "printf '[Service]\\nExecStart=\\n' > /etc/systemd/system/nginx.service.d/override.conf",
            "systemctl daemon-reload && systemctl stop nginx",
        ],
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


def _scenario():
    return FakePlanner([_valid_payload()]).generate_scenario(_request())


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


def test_generate_scenario_repairs_prose_sabotage_steps() -> None:
    invalid_payload = _valid_payload()
    invalid_payload["sabotage_procedure"] = [
        "Install nginx and write a broken systemd override for it.",
    ]
    planner = FakePlanner(
        [
            invalid_payload,
            _valid_payload(),
        ]
    )

    scenario = planner.generate_scenario(_request())

    assert len(planner.calls) == 2
    assert planner.calls[1]["schema_name"] == "planner_scenario_repair"
    assert "sabotage_procedure" in str(planner.calls[1]["user_input"])
    assert scenario.sabotage_procedure[0] == "mkdir -p /etc/systemd/system/nginx.service.d"


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

    assert "sabotage_procedure must contain raw executable shell commands or shell snippets" in prompt.lower()
    assert "no prose" in prompt.lower()
    assert "no markdown" in prompt.lower()
    assert "no backticks" in prompt.lower()
    assert "repair checks must validate the repaired end state" in prompt.lower()
    assert "at least one repair check must be user-visible or symptom-level" in prompt.lower()
    assert "do not include a repair check that can fail on an otherwise repaired system" in prompt.lower()
    assert "if a check needs elevated privileges" in prompt.lower()
    assert "before finalizing repair_checks, ask whether any check could fail even though the system is repaired" in prompt.lower()
    assert "nginx-specific" not in prompt.lower()


def test_generate_initial_user_message_returns_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    planner = OpenAIResponsesScenarioPlanner(_config())
    monkeypatch.setattr(
        planner,
        "_request_json_schema",
        lambda **_: {"message": "My website is down. I tried restarting nginx and it still failed."},
    )

    draft = planner.generate_initial_user_message(
        scenario=_scenario(),
        hidden_context={"visible_symptoms": ["restart failed"]},
    )

    assert draft.message.startswith("My website is down.")


def test_generate_initial_user_message_uses_structured_request_wiring() -> None:
    planner = FakePlanner(
        [
            _valid_payload(),
            {"message": "My website is down. I tried restarting nginx and it still failed."},
        ]
    )
    scenario = planner.generate_scenario(_request())

    draft = planner.generate_initial_user_message(
        scenario=scenario,
        hidden_context={"visible_symptoms": ["restart failed"]},
    )

    assert draft.message.startswith("My website is down.")
    assert planner.calls[1]["schema_name"] == "planner_initial_user_message"
    assert '"hidden_context": {' in str(planner.calls[1]["user_input"])
    assert '"visible_symptoms"' in str(planner.calls[1]["user_input"])


def test_generate_initial_user_message_raises_for_blank_message(monkeypatch: pytest.MonkeyPatch) -> None:
    planner = OpenAIResponsesScenarioPlanner(_config())
    monkeypatch.setattr(planner, "_request_json_schema", lambda **_: {"message": "   "})

    with pytest.raises(ValueError, match="message"):
        planner.generate_initial_user_message(
            scenario=_scenario(),
            hidden_context={"visible_symptoms": ["restart failed"]},
        )


def test_generate_initial_user_message_raises_for_missing_message(monkeypatch: pytest.MonkeyPatch) -> None:
    planner = OpenAIResponsesScenarioPlanner(_config())
    monkeypatch.setattr(planner, "_request_json_schema", lambda **_: {})

    with pytest.raises(ValueError, match="message"):
        planner.generate_initial_user_message(
            scenario=_scenario(),
            hidden_context={"visible_symptoms": ["restart failed"]},
        )


def test_review_initial_user_message_can_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    planner = OpenAIResponsesScenarioPlanner(_config())
    monkeypatch.setattr(
        planner,
        "_request_json_schema",
        lambda **_: {
            "outcome": "rewrite",
            "notes": "Draft reveals the config directive.",
            "final_message": "My website is down. I tried restarting nginx and it still failed.",
        },
    )

    review = planner.review_initial_user_message(
        scenario=_scenario(),
        draft_message="The server_name line in nginx is missing a semicolon.",
    )

    assert review.outcome == "rewrite"
    assert "directive" in review.notes.lower()


def test_review_initial_user_message_uses_structured_request_wiring() -> None:
    planner = FakePlanner(
        [
            _valid_payload(),
            {
                "outcome": "approve",
                "notes": "Looks realistic.",
                "final_message": "My website is down. I tried restarting nginx and it still failed.",
            },
        ]
    )
    scenario = planner.generate_scenario(_request())

    review = planner.review_initial_user_message(
        scenario=scenario,
        draft_message="My website is down. I tried restarting nginx and it still failed.",
    )

    assert review.outcome == "approve"
    assert planner.calls[1]["schema_name"] == "planner_initial_user_message_review"
    assert '"draft_message"' in str(planner.calls[1]["user_input"])


def test_review_initial_user_message_raises_for_unknown_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    planner = OpenAIResponsesScenarioPlanner(_config())
    monkeypatch.setattr(
        planner,
        "_request_json_schema",
        lambda **_: {
            "outcome": "maybe",
            "notes": "unclear",
            "final_message": "My website is down.",
        },
    )

    with pytest.raises(ValueError, match="outcome"):
        planner.review_initial_user_message(
            scenario=_scenario(),
            draft_message="My website is down.",
        )


def test_review_initial_user_message_raises_for_blank_final_message(monkeypatch: pytest.MonkeyPatch) -> None:
    planner = OpenAIResponsesScenarioPlanner(_config())
    monkeypatch.setattr(
        planner,
        "_request_json_schema",
        lambda **_: {
            "outcome": "approve",
            "notes": "Looks realistic.",
            "final_message": "   ",
        },
    )

    with pytest.raises(ValueError, match="final_message"):
        planner.review_initial_user_message(
            scenario=_scenario(),
            draft_message="My website is down.",
        )


def test_scenario_from_dict_uses_opening_user_message_for_initial_user_message() -> None:
    scenario = ScenarioSpec.from_dict(
        {
            **_valid_payload(),
            "observable_problem_statement": "",
            "opening_user_message": "My website is down. I tried restarting nginx.",
        }
    )

    assert scenario.observable_problem_statement == "My website is down. I tried restarting nginx."
    assert scenario.initial_user_message == "My website is down. I tried restarting nginx."


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
