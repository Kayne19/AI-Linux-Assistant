import pytest

from eval_harness.models import ScenarioSpec, VerificationCheck
from eval_harness.scenario import ScenarioValidationError, validate_scenario


def _base_scenario():
    return ScenarioSpec(
        scenario_name="nginx-recovery",
        title="nginx override permission fault",
        summary="Example scenario",
        what_it_tests=("service recovery",),
        target_image="debian-12-openclaw-golden",
        sabotage_procedure=("apt-get install -y nginx", "break nginx override"),
        verification_probes=(
            VerificationCheck(
                name="nginx-broken",
                command="systemctl is-active nginx",
                expected_substrings=("inactive", "failed"),
            ),
        ),
        repair_checks=(
            VerificationCheck(
                name="nginx-fixed",
                command="systemctl is-active nginx",
                expected_substrings=("active",),
            ),
        ),
        observable_problem_statement="nginx will not start",
        judge_rubric=("diagnosis", "repair quality"),
        turn_budget=6,
    )


def test_validate_scenario_accepts_runnable_spec():
    validate_scenario(_base_scenario())


def test_validate_scenario_requires_verification_and_repair_checks():
    spec = ScenarioSpec(
        scenario_name="bad-1",
        title="broken",
        summary="bad",
        what_it_tests=("test",),
        target_image="image",
        sabotage_procedure=("echo setup",),
        verification_probes=(),
        repair_checks=(),
        observable_problem_statement="help",
        judge_rubric=("quality",),
        turn_budget=3,
    )

    with pytest.raises(ScenarioValidationError) as exc_info:
        validate_scenario(spec)

    message = str(exc_info.value)
    assert "verification_probe" in message
    assert "repair_check" in message


def test_validate_scenario_rejects_non_objective_verification_probe():
    spec = _base_scenario()
    spec = ScenarioSpec(
        scenario_name=spec.scenario_name,
        title=spec.title,
        summary=spec.summary,
        what_it_tests=spec.what_it_tests,
        target_image=spec.target_image,
        sabotage_procedure=spec.sabotage_procedure,
        verification_probes=(VerificationCheck(name="probe", command="true"),),
        repair_checks=spec.repair_checks,
        observable_problem_statement=spec.observable_problem_statement,
        judge_rubric=spec.judge_rubric,
        turn_budget=spec.turn_budget,
    )

    with pytest.raises(ScenarioValidationError) as exc_info:
        validate_scenario(spec)

    assert "machine-checkable expectation" in str(exc_info.value)


def test_validate_scenario_accepts_regex_and_negative_expectations() -> None:
    spec = ScenarioSpec(
        scenario_name="http-recovery",
        title="http recovery",
        summary="Example scenario",
        what_it_tests=("http validation",),
        target_image="debian-12-openclaw-golden",
        sabotage_procedure=("break app",),
        verification_probes=(
            VerificationCheck(
                name="app-broken",
                command="curl -si http://localhost/health",
                expected_regexes=(r"^HTTP/1\\.[01] 5\\d\\d",),
                unexpected_substrings=("200 OK",),
            ),
        ),
        repair_checks=(
            VerificationCheck(
                name="app-fixed",
                command="curl -si http://localhost/health",
                expected_regexes=(r"^HTTP/1\\.[01] 200",),
                unexpected_regexes=(r"traceback",),
            ),
        ),
        observable_problem_statement="the health check is failing",
        judge_rubric=("diagnosis", "repair quality"),
        turn_budget=6,
    )

    validate_scenario(spec)
