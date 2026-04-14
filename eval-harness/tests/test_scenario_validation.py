from eval_harness.models import ScenarioSpec, VerificationCheck
from eval_harness.scenario import ScenarioValidationError, validate_scenario


def _base_scenario():
    return ScenarioSpec(
        scenario_id="svc-nginx-001",
        title="nginx override permission fault",
        summary="Example scenario",
        target_image="debian-12-openclaw-golden",
        setup_steps=("apt-get install -y nginx",),
        broken_state_checks=(
            VerificationCheck(
                name="nginx-broken",
                command="systemctl is-active nginx",
                expected_substrings=("inactive", "failed"),
            ),
        ),
        resolution_checks=(
            VerificationCheck(
                name="nginx-fixed",
                command="systemctl is-active nginx",
                expected_substrings=("active",),
            ),
        ),
        opening_user_message="nginx will not start",
        turn_budget=6,
    )


def test_validate_scenario_accepts_runnable_spec():
    validate_scenario(_base_scenario())


def test_validate_scenario_requires_broken_and_resolution_checks():
    spec = ScenarioSpec(
        scenario_id="bad-1",
        title="broken",
        summary="",
        target_image="image",
        setup_steps=("echo setup",),
        broken_state_checks=(),
        resolution_checks=(),
        opening_user_message="help",
        turn_budget=3,
    )

    try:
        validate_scenario(spec)
    except ScenarioValidationError as exc:
        message = str(exc)
    else:
        raise AssertionError("ScenarioValidationError was not raised")

    assert "broken_state_check" in message
    assert "resolution_check" in message


def test_validate_scenario_rejects_empty_setup_step():
    spec = _base_scenario()
    spec = ScenarioSpec(
        scenario_id=spec.scenario_id,
        title=spec.title,
        summary=spec.summary,
        target_image=spec.target_image,
        setup_steps=(" ",),
        broken_state_checks=spec.broken_state_checks,
        resolution_checks=spec.resolution_checks,
        opening_user_message=spec.opening_user_message,
        turn_budget=spec.turn_budget,
    )

    try:
        validate_scenario(spec)
    except ScenarioValidationError as exc:
        assert "setup_steps[1]" in str(exc)
    else:
        raise AssertionError("ScenarioValidationError was not raised")


def test_validate_scenario_requires_target_image():
    spec = _base_scenario()
    spec = ScenarioSpec(
        scenario_id=spec.scenario_id,
        title=spec.title,
        summary=spec.summary,
        target_image="",
        setup_steps=spec.setup_steps,
        broken_state_checks=spec.broken_state_checks,
        resolution_checks=spec.resolution_checks,
        opening_user_message=spec.opening_user_message,
        turn_budget=spec.turn_budget,
    )

    try:
        validate_scenario(spec)
    except ScenarioValidationError as exc:
        assert "target_image is required" in str(exc)
    else:
        raise AssertionError("ScenarioValidationError was not raised")
