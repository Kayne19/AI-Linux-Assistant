from __future__ import annotations

from abc import ABC, abstractmethod

from eval_harness.artifacts import ArtifactPack
from eval_harness.models import GraderOutput


class GraderPlugin(ABC):
    """Optional plugin contract for post-hoc grading over stored artifacts."""

    name = "grader_plugin"
    version = "0.1.0"

    @abstractmethod
    def grade(self, artifact_pack: ArtifactPack) -> GraderOutput:
        raise NotImplementedError
