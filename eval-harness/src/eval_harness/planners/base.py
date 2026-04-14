from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import (
    CommandExecutionResult,
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
    def review_sabotage(
        self,
        scenario: ScenarioSpec,
        *,
        round_index: int,
        command_results: tuple[CommandExecutionResult, ...],
        correction_count: int,
    ) -> PlannerReviewDecision:
        raise NotImplementedError
