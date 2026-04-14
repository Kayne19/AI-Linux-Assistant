from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import AdapterTurnResult, SubjectSpec, TurnSeed


class AdapterError(RuntimeError):
    """Raised when a subject adapter cannot complete a turn."""


class SubjectSession(ABC):
    """Session contract for one benchmark subject."""

    @abstractmethod
    def seed_context(self, context_seed: tuple[TurnSeed, ...]) -> None:
        raise NotImplementedError

    @abstractmethod
    def submit_user_message(self, message: str) -> AdapterTurnResult:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> dict[str, Any]:
        raise NotImplementedError


class SubjectAdapter(ABC):
    """Adapter owns product-specific subject semantics only."""

    name: str

    @abstractmethod
    def create_session(self, benchmark_run_id: str, subject: SubjectSpec) -> SubjectSession:
        raise NotImplementedError


# Backward-compatibility aliases for old scaffold naming.
SolverSession = SubjectSession
SolverAdapter = SubjectAdapter
