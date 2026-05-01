"""ScenarioSetupOrchestrator — thin wrapper that delegates to ScenarioBuilderFSM."""
from __future__ import annotations

from typing import Callable, Any

from ..backends.base import SandboxBackend
from ..controllers.base import SandboxControllerFactory
from ..models import PlannerScenarioRequest, ScenarioSetupStatus
from ..persistence.store import EvalHarnessStore
from ..planners.base import ScenarioPlanner
from .scenario_fsm import ScenarioBuilderFSM, ScenarioSetupFailedError, ScenarioSetupResult

# Keep for use by cleanup / exception handlers elsewhere in the harness.
_TERMINAL_SETUP_STATUSES = frozenset(
    {
        ScenarioSetupStatus.VERIFIED.value,
        ScenarioSetupStatus.FAILED_MAX_CORRECTIONS.value,
        ScenarioSetupStatus.FAILED_INFRA.value,
    }
)

__all__ = [
    "ScenarioSetupFailedError",
    "ScenarioSetupOrchestrator",
    "ScenarioSetupResult",
    "_TERMINAL_SETUP_STATUSES",
]


class ScenarioSetupOrchestrator:
    """Orchestrates scenario setup by delegating to ScenarioBuilderFSM."""

    def __init__(
        self,
        *,
        backend: SandboxBackend,
        controller_factory: SandboxControllerFactory,
        planner: ScenarioPlanner,
        store: EvalHarnessStore,
        progress: Callable[..., None] | None = None,
    ):
        self.backend = backend
        self.controller_factory = controller_factory
        self.planner = planner
        self.store = store
        self.progress = progress

    def run(
        self,
        request: PlannerScenarioRequest,
        *,
        group_id: str,
        sabotage_agent_id: str = "setup",
        verification_agent_id: str = "verifier",
        max_corrections: int = 2,
    ) -> ScenarioSetupResult:
        # sabotage_agent_id / verification_agent_id are legacy params kept for
        # signature compatibility; the FSM does not use agent IDs.
        fsm = ScenarioBuilderFSM(
            backend=self.backend,
            controller_factory=self.controller_factory,
            planner=self.planner,
            store=self.store,
            request=request,
            group_id=group_id,
            max_corrections=max_corrections,
            progress=self.progress,
        )
        fsm_result = fsm.run()
        return ScenarioSetupResult(
            scenario_id=fsm_result.scenario_id,
            scenario_name=fsm_result.scenario_name,
            scenario_revision_id=fsm_result.scenario_revision_id,
            setup_run_id=fsm_result.setup_run_id,
            broken_image_id=fsm_result.broken_image_id,
        )
