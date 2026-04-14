from dataclasses import asdict

from eval_harness.artifacts import ArtifactPack, VariantArtifact
from eval_harness.models import CheckPhase, CheckStatus, RunStatus, VerificationResult
from eval_harness.plugins.example_grader import ExampleGrader


def test_example_grader_is_optional_and_post_hoc() -> None:
    pack = ArtifactPack(
        artifact_pack_id="pack-001",
        scenario_id="scenario-001",
        run_group_id="run-group-001",
        variants=[
            VariantArtifact(
                variant_id="regular",
                status=RunStatus.COMPLETED,
                transcript=["msg1", "msg2"],
                check_results=[
                    VerificationResult(
                        check_id="broken-1",
                        phase=CheckPhase.BROKEN_STATE,
                        status=CheckStatus.PASS,
                    ),
                    VerificationResult(
                        check_id="resolve-1",
                        phase=CheckPhase.RESOLUTION,
                        status=CheckStatus.PASS,
                    ),
                ],
            ),
            VariantArtifact(
                variant_id="magi_full",
                status=RunStatus.COMPLETED,
                transcript=["msg3"],
                check_results=[
                    VerificationResult(
                        check_id="broken-1",
                        phase=CheckPhase.BROKEN_STATE,
                        status=CheckStatus.PASS,
                    ),
                    VerificationResult(
                        check_id="resolve-1",
                        phase=CheckPhase.RESOLUTION,
                        status=CheckStatus.FAIL,
                    ),
                ],
            ),
        ],
    )

    baseline = asdict(pack)
    output = ExampleGrader().grade(pack)

    assert asdict(pack) == baseline
    assert output.plugin_name == "example_grader"
    assert output.artifact_pack_id == "pack-001"
    assert output.overall_score is None
    assert output.metrics["broken_state_pass_rate"] == 1.0
    assert output.metrics["resolution_pass_rate"] == 0.5
    assert output.metrics["repair_success_rate"] == 0.5
    assert output.metrics["transcript_message_count"] == 3.0
