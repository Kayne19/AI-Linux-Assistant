from __future__ import annotations

from .base import GraderPlugin
from ..models import ArtifactPack, EvaluationRunStatus, GraderOutput


class ExampleArtifactGrader(GraderPlugin):
    """
    Optional reference grader.

    This is intentionally non-canonical: it summarizes artifacts and emits
    convenience metrics without imposing a harness-wide pass/fail contract.
    """

    name = "example_artifact_grader"

    def grade(self, pack: ArtifactPack) -> GraderOutput:
        evaluations = list(pack.evaluations)
        total_evaluations = len(evaluations)
        completed_evaluations = [item for item in evaluations if item.status == EvaluationRunStatus.COMPLETED.value]
        failed_evaluations = [item for item in evaluations if item.status == EvaluationRunStatus.FAILED.value]
        successful_repairs = [item for item in completed_evaluations if item.repair_success]
        average_turns = 0.0
        if evaluations:
            average_turns = sum(len(item.transcript) for item in evaluations) / float(total_evaluations)

        metrics = {
            "evaluation_count": total_evaluations,
            "completed_evaluations": len(completed_evaluations),
            "failed_evaluations": len(failed_evaluations),
            "successful_repairs": len(successful_repairs),
            "repair_success_rate": (len(successful_repairs) / float(total_evaluations)) if total_evaluations else 0.0,
            "average_transcript_turns": average_turns,
            "judge_item_count": len(pack.judge_artifacts),
            "setup_probe_count": len(pack.setup_command_results),
        }
        summary = (
            f"{len(successful_repairs)}/{total_evaluations} evaluation runs passed repair checks; "
            f"{len(failed_evaluations)} evaluation runs ended failed."
        )
        return GraderOutput(plugin_name=self.name, summary=summary, metrics=metrics)
