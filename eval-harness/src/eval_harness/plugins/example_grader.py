from __future__ import annotations

from .base import GraderPlugin
from ..models import ArtifactPack, GraderOutput, VariantLifecycle


class ExampleArtifactGrader(GraderPlugin):
    """
    Optional reference grader.

    This is intentionally non-canonical: it summarizes artifacts and emits
    convenience metrics without imposing a harness-wide pass/fail contract.
    """

    name = "example_artifact_grader"

    def grade(self, pack: ArtifactPack) -> GraderOutput:
        variants = list(pack.variant_artifacts)
        total_variants = len(variants)
        completed_variants = [item for item in variants if item.lifecycle == VariantLifecycle.COMPLETED]
        failed_variants = [item for item in variants if item.lifecycle == VariantLifecycle.FAILED]
        successful_repairs = [
            item for item in completed_variants if item.resolution_results and all(result.success for result in item.resolution_results)
        ]
        average_turns = 0.0
        if variants:
            average_turns = sum(len(item.transcript) for item in variants) / float(total_variants)

        metrics = {
            "variant_count": total_variants,
            "completed_variants": len(completed_variants),
            "failed_variants": len(failed_variants),
            "successful_repairs": len(successful_repairs),
            "repair_success_rate": (len(successful_repairs) / float(total_variants)) if total_variants else 0.0,
            "average_transcript_turns": average_turns,
            "broken_state_verified": bool(pack.broken_state_results) and all(item.success for item in pack.broken_state_results),
        }
        summary = (
            f"{len(successful_repairs)}/{total_variants} variants passed all resolution checks; "
            f"{len(failed_variants)} variants ended in a failed lifecycle."
        )
        return GraderOutput(plugin_name=self.name, summary=summary, metrics=metrics)
