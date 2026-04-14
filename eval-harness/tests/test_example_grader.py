from eval_harness.models import ArtifactPack, EvaluationArtifact, EvaluationRunStatus, JudgeArtifact, TurnRecord
from eval_harness.plugins.example_grader import ExampleArtifactGrader


def test_example_grader_is_optional_and_operates_on_artifacts():
    pack = ArtifactPack(
        benchmark_run_id="benchmark-1",
        scenario_name="nginx-recovery",
        scenario_revision_id="revision-1",
        setup_run_id="setup-1",
        backend_name="aws_ec2",
        controller_name="openclaw",
        subject_adapter_types=("ai_linux_assistant_http",),
        broken_image_id="ami-broken",
        evaluations=(
            EvaluationArtifact(
                evaluation_run_id="eval-1",
                subject_name="system-a",
                status=EvaluationRunStatus.COMPLETED.value,
                transcript=(TurnRecord(role="user", content="nginx is down"), TurnRecord(role="assistant", content="restart nginx")),
                repair_success=True,
            ),
            EvaluationArtifact(
                evaluation_run_id="eval-2",
                subject_name="system-b",
                status=EvaluationRunStatus.FAILED.value,
                transcript=(TurnRecord(role="user", content="nginx is down"),),
                repair_success=False,
            ),
        ),
        judge_artifacts=(
            JudgeArtifact(judge_job_id="judge-1", blind_label="candidate-1", summary="good", scores={"diagnosis": 4}),
        ),
    )

    output = ExampleArtifactGrader().grade(pack)

    assert output.plugin_name == "example_artifact_grader"
    assert output.metrics["evaluation_count"] == 2
    assert output.metrics["successful_repairs"] == 1
    assert output.metrics["failed_evaluations"] == 1
