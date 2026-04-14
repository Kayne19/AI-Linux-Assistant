from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import AdapterTurnResult, ScenarioSpec, TurnSeed, VariantSpec


class AdapterError(RuntimeError):
    """Raised when a solver adapter cannot complete a turn."""


class SolverSession(ABC):
    """Session contract for one scenario/variant pair."""

    @abstractmethod
    def seed_context(self, context_seed: tuple[TurnSeed, ...]) -> None:
        raise NotImplementedError

    @abstractmethod
    def submit_user_message(self, message: str, *, mode_override: str | None = None) -> AdapterTurnResult:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> dict[str, Any]:
        raise NotImplementedError


class SolverAdapter(ABC):
    """Adapter owns product-specific solver semantics only."""

    name: str

    @abstractmethod
    def create_session(self, scenario: ScenarioSpec, group_id: str, variant: VariantSpec) -> SolverSession:
        raise NotImplementedError
