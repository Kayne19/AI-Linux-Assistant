from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import VerificationCheck


@dataclass(slots=True)
class ScenarioSpec:
    scenario_id: str
    title: str
    opening_message: str
    setup_steps: list[str] = field(default_factory=list)
    broken_state_checks: list[VerificationCheck] = field(default_factory=list)
    resolution_checks: list[VerificationCheck] = field(default_factory=list)
    turn_budget: int = 12
    description: str | None = None
    context_seed: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self, *, require_runnable: bool = True) -> None:
        validate_scenario(self, require_runnable=require_runnable)


class ScenarioValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def collect_scenario_validation_errors(
    spec: ScenarioSpec, *, require_runnable: bool = True
) -> list[str]:
    errors: list[str] = []

    if not spec.scenario_id.strip():
        errors.append("scenario_id must not be empty")
    if not spec.title.strip():
        errors.append("title must not be empty")
    if not spec.opening_message.strip():
        errors.append("opening_message must not be empty")
    if spec.turn_budget <= 0:
        errors.append("turn_budget must be greater than zero")

    if require_runnable and len(spec.broken_state_checks) == 0:
        errors.append(
            "runnable scenarios require at least one broken-state verification check"
        )
    if require_runnable and len(spec.resolution_checks) == 0:
        errors.append(
            "runnable scenarios require at least one resolution verification check"
        )

    return errors


def validate_scenario(spec: ScenarioSpec, *, require_runnable: bool = True) -> None:
    errors = collect_scenario_validation_errors(
        spec,
        require_runnable=require_runnable,
    )
    if errors:
        raise ScenarioValidationError(errors)


def is_runnable_scenario(spec: ScenarioSpec) -> bool:
    return len(collect_scenario_validation_errors(spec, require_runnable=True)) == 0
