from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import BlindJudgeRequest, BlindJudgeResult


class BlindJudge(ABC):
    """Judge owns blinded transcript grading only."""

    name: str

    @abstractmethod
    def grade(self, request: BlindJudgeRequest) -> BlindJudgeResult:
        raise NotImplementedError
