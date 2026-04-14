from __future__ import annotations

import json
from pathlib import Path

from .models import ScenarioSpec


class ScenarioValidationError(ValueError):
    """Raised when a scenario is not runnable."""


def validate_scenario(spec: ScenarioSpec) -> None:
    errors: list[str] = []
    if not spec.scenario_id:
        errors.append("scenario_id is required")
    if not spec.title:
        errors.append("title is required")
    if not spec.target_image:
        errors.append("target_image is required")
    if not spec.setup_steps:
        errors.append("at least one setup step is required")
    if not spec.broken_state_checks:
        errors.append("at least one broken_state_check is required")
    if not spec.resolution_checks:
        errors.append("at least one resolution_check is required")
    if not spec.opening_user_message:
        errors.append("opening_user_message is required")
    if spec.turn_budget <= 0:
        errors.append("turn_budget must be greater than 0")
    if not spec.variants:
        errors.append("at least one variant is required")

    for index, step in enumerate(spec.setup_steps, start=1):
        if not step.strip():
            errors.append(f"setup_steps[{index}] must not be empty")

    for label, checks in (
        ("broken_state_checks", spec.broken_state_checks),
        ("resolution_checks", spec.resolution_checks),
    ):
        for index, check in enumerate(checks, start=1):
            if not check.name:
                errors.append(f"{label}[{index}].name is required")
            if not check.command:
                errors.append(f"{label}[{index}].command is required")
            if check.timeout_seconds <= 0:
                errors.append(f"{label}[{index}].timeout_seconds must be greater than 0")

    seen_variants: set[str] = set()
    for index, variant in enumerate(spec.variants, start=1):
        if not variant.name:
            errors.append(f"variants[{index}].name is required")
            continue
        if variant.name in seen_variants:
            errors.append(f"variants[{index}].name must be unique")
        seen_variants.add(variant.name)

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
