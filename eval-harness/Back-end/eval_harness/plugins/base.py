from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ArtifactPack, GraderOutput


class GraderPlugin(ABC):
    """Optional grading hook over stored artifacts."""

    name: str

    @abstractmethod
    def grade(self, pack: ArtifactPack) -> GraderOutput:
        raise NotImplementedError
