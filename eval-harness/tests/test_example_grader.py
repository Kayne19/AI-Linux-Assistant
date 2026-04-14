from eval_harness.models import (
    ArtifactPack,
    ScenarioSpec,
    VariantArtifact,
    VariantLifecycle,
    VerificationCheck,
    VerificationResult,
)
from eval_harness.plugins.example_grader import ExampleArtifactGrader


def test_example_grader_is_optional_and_operates_on_artifacts():
    scenario = ScenarioSpec(
        scenario_id="svc-nginx-001",
        title="nginx override permission fault",
        summary="Example scenario",
        target_image="debian-12-openclaw-golden",
        setup_steps=("echo setup",),
        broken_state_checks=(VerificationCheck(name="broken", command="check-broken"),),
        resolution_checks=(VerificationCheck(name="fixed", command="check-fixed"),),
        opening_user_message="nginx will not start",
    )
    pack = ArtifactPack(
        group_id="group-1",
        scenario=scenario,
        backend_name="aws_ec2",
        controller_name="openclaw",
        adapter_name="ai_linux_assistant_http",
        broken_state_results=(
            VerificationResult(check_name="broken", command="check-broken", success=True, output="failed"),
        ),
        variant_artifacts=(
            VariantArtifact(
                variant_name="regular",
                lifecycle=VariantLifecycle.COMPLETED,
                resolution_results=(
                    VerificationResult(check_name="fixed", command="check-fixed", success=True, output="active"),
                ),
            ),
            VariantArtifact(
                variant_name="magi_full",
                lifecycle=VariantLifecycle.FAILED,
                error_message="timeout",
            ),
        ),
    )

    output = ExampleArtifactGrader().grade(pack)

    assert output.plugin_name == "example_artifact_grader"
    assert output.metrics["variant_count"] == 2
    assert output.metrics["successful_repairs"] == 1
    assert output.metrics["failed_variants"] == 1
