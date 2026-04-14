import pytest

from eval_harness.models import VerificationCheck
from eval_harness.scenario import (
    ScenarioSpec,
    ScenarioValidationError,
    collect_scenario_validation_errors,
    is_runnable_scenario,
    validate_scenario,
)


def _check(check_id: str) -> VerificationCheck:
    return VerificationCheck(check_id=check_id, instruction=f"run {check_id}")


def test_runnable_scenario_requires_both_check_sets() -> None:
    spec = ScenarioSpec(
        scenario_id="disk-full-001",
        title="Disk full repair",
        opening_message="Fix the host",
        broken_state_checks=[_check("broken-1")],
        resolution_checks=[_check("resolve-1")],
    )

    validate_scenario(spec, require_runnable=True)
    assert is_runnable_scenario(spec) is True


def test_missing_broken_state_checks_fails_runnable_validation() -> None:
    spec = ScenarioSpec(
        scenario_id="svc-down-001",
        title="Service down",
        opening_message="Bring service back",
        broken_state_checks=[],
        resolution_checks=[_check("resolve-1")],
    )

    with pytest.raises(ScenarioValidationError) as exc_info:
        validate_scenario(spec, require_runnable=True)
    assert "broken-state verification check" in str(exc_info.value)


def test_missing_resolution_checks_fails_runnable_validation() -> None:
    spec = ScenarioSpec(
        scenario_id="perm-001",
        title="Permission mismatch",
        opening_message="Fix permissions",
        broken_state_checks=[_check("broken-1")],
        resolution_checks=[],
    )

    errors = collect_scenario_validation_errors(spec, require_runnable=True)
    assert any("resolution verification check" in error for error in errors)
    assert is_runnable_scenario(spec) is False
