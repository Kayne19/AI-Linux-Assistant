from __future__ import annotations

import json
from pathlib import Path

from .models import CommandExecutionResult, ScenarioSpec, VerificationCheck


class ScenarioValidationError(ValueError):
    """Raised when a scenario is not runnable."""


def evaluate_verification(check: VerificationCheck, result: CommandExecutionResult) -> bool:
    return check.is_satisfied_by(result)


def validate_scenario(spec: ScenarioSpec) -> None:
    errors: list[str] = []
    if not spec.scenario_name:
        errors.append("scenario_name is required")
    if not spec.title:
        errors.append("title is required")
    if not spec.summary:
        errors.append("summary is required")
    if not spec.what_it_tests:
        errors.append("what_it_tests must contain at least one item")
    if not spec.target_image:
        errors.append("target_image is required")
    if not spec.sabotage_procedure:
        errors.append("at least one sabotage_procedure step is required")
    if not spec.verification_probes:
        errors.append("at least one verification_probe is required")
    if not spec.repair_checks:
        errors.append("at least one repair_check is required")
    if not spec.observable_problem_statement:
        errors.append("observable_problem_statement is required")
    if not spec.judge_rubric:
        errors.append("judge_rubric must contain at least one rubric item")
    if spec.turn_budget <= 0:
        errors.append("turn_budget must be greater than 0")

    for index, step in enumerate(spec.sabotage_procedure, start=1):
        if not step.strip():
            errors.append(f"sabotage_procedure[{index}] must not be empty")

    for label, checks in (("verification_probes", spec.verification_probes), ("repair_checks", spec.repair_checks)):
        for index, check in enumerate(checks, start=1):
            if not check.name:
                errors.append(f"{label}[{index}].name is required")
            if not check.command:
                errors.append(f"{label}[{index}].command is required")
            if check.timeout_seconds <= 0:
                errors.append(f"{label}[{index}].timeout_seconds must be greater than 0")
            if not check.has_machine_expectation():
                errors.append(
                    f"{label}[{index}] must include at least one machine-checkable expectation "
                    "(e.g., expected_exit_code, expected_substrings, expected_regexes, etc.)"
                )

    if errors:
        raise ScenarioValidationError("; ".join(errors))


def load_scenario(path: str | Path) -> ScenarioSpec:
    scenario_path = Path(path)
    payload = json.loads(scenario_path.read_text(encoding="utf-8"))
    spec = ScenarioSpec.from_dict(payload)
    validate_scenario(spec)
    return spec


def write_scenario(path: str | Path, spec: ScenarioSpec) -> Path:
    validate_scenario(spec)
    scenario_path = Path(path)
    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    scenario_path.write_text(json.dumps(spec.to_dict(), indent=2), encoding="utf-8")
    return scenario_path
