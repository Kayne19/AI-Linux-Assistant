from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import (
    CommandExecutionResult,
    InitialUserMessageDraft,
    InitialUserMessageReview,
    PlannerReviewDecision,
    PlannerScenarioRequest,
    ScenarioSpec,
)


class ScenarioPlanner(ABC):
    """Planner owns scenario generation and sabotage review policy."""

    name: str

    @abstractmethod
    def generate_scenario(self, request: PlannerScenarioRequest) -> ScenarioSpec:
        raise NotImplementedError

    @abstractmethod
    def generate_initial_user_message(
        self,
        *,
        scenario: ScenarioSpec,
        hidden_context: dict[str, Any],
    ) -> InitialUserMessageDraft:
        raise NotImplementedError

    @abstractmethod
    def review_initial_user_message(
        self,
        *,
        scenario: ScenarioSpec,
        draft_message: str,
    ) -> InitialUserMessageReview:
        raise NotImplementedError

    @abstractmethod
    def review_sabotage(
        self,
        scenario: ScenarioSpec,
        *,
        round_index: int,
        command_results: tuple[CommandExecutionResult, ...],
        correction_count: int,
        verification_snapshot: dict[str, Any] | None = None,
    ) -> PlannerReviewDecision:
        raise NotImplementedError

    @abstractmethod
    def plan_rectification(
        self,
        scenario: ScenarioSpec,
        *,
        failed_command_results: tuple[CommandExecutionResult, ...],
        correction_instructions: tuple[str, ...],
        round_index: int,
    ) -> tuple[str, ...]:
        """Return a concrete list of shell commands that should rectify failed verification."""
        raise NotImplementedError
