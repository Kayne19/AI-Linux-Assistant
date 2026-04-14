from __future__ import annotations

from eval_harness.artifacts import ArtifactPack
from eval_harness.models import CheckPhase, CheckStatus, GraderOutput
from eval_harness.plugins.base import GraderPlugin


class ExampleGrader(GraderPlugin):
    """Reference-only grader. Core harness does not require this plugin."""

    name = "example_grader"
    version = "0.1.0"

    def grade(self, artifact_pack: ArtifactPack) -> GraderOutput:
        broken_total = 0
        broken_pass = 0
        resolution_total = 0
        resolution_pass = 0
        successful_repairs = 0
        attempted_repairs = 0
        transcript_message_count = 0

        for variant in artifact_pack.variants:
            transcript_message_count += len(variant.transcript)
            resolution_for_variant = []

            for result in variant.check_results:
                if result.phase is CheckPhase.BROKEN_STATE:
                    broken_total += 1
                    if result.status is CheckStatus.PASS:
                        broken_pass += 1
                if result.phase is CheckPhase.RESOLUTION:
                    resolution_total += 1
                    resolution_for_variant.append(result)
                    if result.status is CheckStatus.PASS:
                        resolution_pass += 1

            if resolution_for_variant:
                attempted_repairs += 1
                if all(result.status is CheckStatus.PASS for result in resolution_for_variant):
                    successful_repairs += 1

        metrics = {
            "broken_state_pass_rate": _ratio(broken_pass, broken_total),
            "resolution_pass_rate": _ratio(resolution_pass, resolution_total),
            "repair_success_rate": _ratio(successful_repairs, attempted_repairs),
            "transcript_message_count": float(transcript_message_count),
            "variant_count": float(len(artifact_pack.variants)),
        }

        return GraderOutput(
            plugin_name=self.name,
            artifact_pack_id=artifact_pack.artifact_pack_id,
            metrics=metrics,
            overall_score=None,
            notes=[
                "Example grader is optional and operates on stored artifacts only.",
                "No canonical score is produced by core contracts.",
            ],
        )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator
