"""ScenarioBuilderFSM — host-side FSM that drives the scenario setup loop.

All commands are executed via SandboxController.execute_commands() (SSM or
fake in tests).  No agent messaging (controller.send) is used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable

from ..backends.base import SandboxBackend, SandboxHandle
from ..controllers.base import SandboxController, SandboxControllerFactory
from ..models import (
    CommandExecutionResult,
    PlannerReviewDecision,
    PlannerScenarioRequest,
    ScenarioLifecycleStatus,
    ScenarioSetupStatus,
    ScenarioSpec,
)
from ..persistence.store import EvalHarnessStore
from ..planners.base import ScenarioPlanner
from ..scenario import validate_sabotage_step, validate_scenario


# ---------------------------------------------------------------------------
# Public exceptions and result type (re-exported by setup.py)
# ---------------------------------------------------------------------------


class ScenarioSetupFailedError(RuntimeError):
    """Raised when planner-driven scenario setup does not converge."""


@dataclass(frozen=True)
class ScenarioSetupResult:
    scenario_id: str
    scenario_name: str
    scenario_revision_id: str
    setup_run_id: str
    broken_image_id: str


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


class ScenarioBuilderState(Enum):
    DESIGN = auto()
    LAUNCH_STAGING = auto()
    BUILD = auto()
    VERIFY = auto()
    REVIEW = auto()
    FIX_PLAN = auto()
    FIX_EXECUTE = auto()
    SCRAP = auto()
    CREATE_IMAGE = auto()
    DONE = auto()
    FAILED = auto()


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class ScenarioBuilderContext:
    # Persistent inputs
    request: PlannerScenarioRequest
    group_id: str
    max_corrections: int
    scrap_budget: int

    # Mutable run state — populated as the FSM progresses
    scenario: ScenarioSpec | None = None
    scenario_row: Any = None
    revision_row: Any = None
    setup_run: Any = None

    staging_handle: SandboxHandle | None = None
    controller: SandboxController | None = None

    seq: int = 1
    round_index: int = 0
    correction_count: int = 0
    scrap_count: int = 0

    last_command_results: tuple[CommandExecutionResult, ...] = ()
    last_review_decision: PlannerReviewDecision | None = None
    fix_commands: tuple[str, ...] = ()
    # sabotage commands for current round (sabotage_procedure initially, then rectification)
    instructions: tuple[str, ...] = ()

    broken_image_id: str = ""
    failure_reason: str = ""
    failure_metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# FSM
# ---------------------------------------------------------------------------

_TERMINAL_SETUP_STATUSES = frozenset(
    {
        ScenarioSetupStatus.VERIFIED.value,
        ScenarioSetupStatus.FAILED_MAX_CORRECTIONS.value,
        ScenarioSetupStatus.FAILED_INFRA.value,
    }
)


class ScenarioBuilderFSM:
    """Host-side FSM that builds, verifies, and images a broken scenario sandbox."""

    def __init__(
        self,
        *,
        backend: SandboxBackend,
        controller_factory: SandboxControllerFactory,
        planner: ScenarioPlanner,
        store: EvalHarnessStore,
        request: PlannerScenarioRequest,
        group_id: str,
        max_corrections: int = 2,
        scrap_budget: int = 1,
        progress: Callable[[str, str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.backend = backend
        self.controller_factory = controller_factory
        self.planner = planner
        self.store = store
        self.progress = progress or (lambda fsm_name, scenario_name, details: None)

        self._ctx = ScenarioBuilderContext(
            request=request,
            group_id=group_id,
            max_corrections=max_corrections,
            scrap_budget=scrap_budget,
        )

        self.state_actions: dict[
            ScenarioBuilderState,
            Callable[[ScenarioBuilderContext], ScenarioBuilderState],
        ] = {
            ScenarioBuilderState.DESIGN: self._handle_design,
            ScenarioBuilderState.LAUNCH_STAGING: self._handle_launch_staging,
            ScenarioBuilderState.BUILD: self._handle_build,
            ScenarioBuilderState.VERIFY: self._handle_verify,
            ScenarioBuilderState.REVIEW: self._handle_review,
            ScenarioBuilderState.FIX_PLAN: self._handle_fix_plan,
            ScenarioBuilderState.FIX_EXECUTE: self._handle_fix_execute,
            ScenarioBuilderState.SCRAP: self._handle_scrap,
            ScenarioBuilderState.CREATE_IMAGE: self._handle_create_image,
            ScenarioBuilderState.DONE: self._handle_done,
            ScenarioBuilderState.FAILED: self._handle_failed,
        }

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> ScenarioSetupResult:
        ctx = self._ctx
        current = ScenarioBuilderState.DESIGN
        try:
            while current not in (ScenarioBuilderState.DONE, ScenarioBuilderState.FAILED):
                next_state = self.state_actions[current](ctx)
                self._emit_transition(ctx, from_state=current, to_state=next_state)
                current = next_state

            # Handle terminal states
            if current == ScenarioBuilderState.DONE:
                self._handle_done(ctx)
            else:
                self._handle_failed(ctx)

        except ScenarioSetupFailedError:
            raise
        except Exception as exc:
            # Collect diagnostics before teardown
            if ctx.staging_handle is not None:
                diagnostics = self.backend.collect_failure_diagnostics(ctx.staging_handle)
                if diagnostics and ctx.setup_run is not None:
                    failure_metadata: dict[str, Any] = {
                        "failure_exception": f"{type(exc).__name__}: {exc}",
                        "failure_diagnostics": diagnostics,
                    }
                    current_setup = self.store.get_setup_run(ctx.setup_run.id)
                    current_status = current_setup.status if current_setup is not None else ScenarioSetupStatus.RUNNING.value
                    self.store.update_setup_run_status(
                        setup_run_id=ctx.setup_run.id,
                        status=current_status,
                        backend_metadata=failure_metadata,
                    )
            # Ensure non-terminal status gets set to failed_infra
            if ctx.setup_run is not None:
                current_setup = self.store.get_setup_run(ctx.setup_run.id)
                if current_setup and current_setup.status not in _TERMINAL_SETUP_STATUSES:
                    self.store.update_setup_run_status(
                        setup_run_id=ctx.setup_run.id,
                        status=ScenarioSetupStatus.FAILED_INFRA.value,
                        correction_count=ctx.correction_count,
                        failure_reason="infra_failure",
                    )
                    if ctx.scenario_row is not None:
                        self.store.update_scenario_status(
                            scenario_id=ctx.scenario_row.id,
                            lifecycle_status=ScenarioLifecycleStatus.FAILED_SETUP.value,
                            verification_status="failed",
                        )
            raise
        finally:
            if ctx.controller is not None:
                ctx.controller.close()
                ctx.controller = None
            if ctx.staging_handle is not None:
                self.backend.destroy_handle(ctx.staging_handle)
                ctx.staging_handle = None

        # Return result (only reached from DONE path)
        return ScenarioSetupResult(
            scenario_id=ctx.scenario_row.id,
            scenario_name=ctx.scenario_row.scenario_name,
            scenario_revision_id=ctx.revision_row.id,
            setup_run_id=ctx.setup_run.id,
            broken_image_id=ctx.broken_image_id,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _scenario_name(self, ctx: ScenarioBuilderContext) -> str:
        if ctx.scenario_row is not None:
            return ctx.scenario_row.scenario_name
        return "pending"

    def _emit_transition(
        self,
        ctx: ScenarioBuilderContext,
        *,
        from_state: ScenarioBuilderState,
        to_state: ScenarioBuilderState,
    ) -> None:
        details = {
            "from": from_state.name,
            "to": to_state.name,
            "round_index": ctx.round_index,
            "correction_count": ctx.correction_count,
            "scrap_count": ctx.scrap_count,
        }
        self.progress(fsm_name="scenario-builder", scenario_name=self._scenario_name(ctx), details=details)
        if ctx.setup_run is not None:
            self.store.append_setup_event(
                setup_run_id=ctx.setup_run.id,
                round_index=ctx.round_index,
                seq=ctx.seq,
                actor_role="fsm",
                event_kind="state_transition",
                payload={"from": from_state.name, "to": to_state.name},
            )
            ctx.seq += 1

    def _append_command_results(
        self,
        ctx: ScenarioBuilderContext,
        command_results: tuple[CommandExecutionResult, ...],
    ) -> None:
        for result in command_results:
            self.store.append_setup_event(
                setup_run_id=ctx.setup_run.id,
                round_index=ctx.round_index,
                seq=ctx.seq,
                actor_role="controller",
                event_kind="command_result",
                payload=result.to_dict(),
            )
            ctx.seq += 1

    def _persisted_scenario(
        self,
        draft: ScenarioSpec,
        scenario_id: str,
        revision_id: str,
        revision_number: int,
    ) -> ScenarioSpec:
        from dataclasses import replace

        return replace(
            draft,
            planner_metadata={
                **draft.planner_metadata,
                "scenario_id": scenario_id,
                "revision_id": revision_id,
                "revision_number": revision_number,
                "repair_checks": [item.to_dict() for item in draft.repair_checks],
                "turn_budget": draft.turn_budget,
                "metadata": draft.metadata,
            },
        )

    def _generate_initial_user_message(self, draft: ScenarioSpec) -> tuple[str, dict[str, Any]]:
        hidden_context = {
            "sabotage_procedure": list(draft.sabotage_procedure),
            "verification_probes": [item.to_dict() for item in draft.verification_probes],
            "repair_checks": [item.to_dict() for item in draft.repair_checks],
        }
        draft_message = ""
        try:
            draft_message = self.planner.generate_initial_user_message(
                scenario=draft,
                hidden_context=hidden_context,
            ).message
            review = self.planner.review_initial_user_message(
                scenario=draft,
                draft_message=draft_message,
            )
            return review.final_message, {
                "draft": draft_message,
                "review_outcome": review.outcome,
                "review_notes": review.notes,
                "final_message": review.final_message,
                "used_fallback": False,
            }
        except Exception as exc:
            fallback = draft.observable_problem_statement
            return fallback, {
                "draft": draft_message,
                "review_outcome": "fallback",
                "review_notes": f"{type(exc).__name__}: {exc}",
                "final_message": fallback,
                "used_fallback": True,
            }

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _handle_design(self, ctx: ScenarioBuilderContext) -> ScenarioBuilderState:
        from dataclasses import replace

        draft = self.planner.generate_scenario(ctx.request)
        validate_scenario(draft)
        initial_user_message, generation_metadata = self._generate_initial_user_message(draft)
        draft = replace(
            draft,
            initial_user_message=initial_user_message,
            planner_metadata={
                **draft.planner_metadata,
                "initial_user_message_generation": generation_metadata,
            },
        )

        scenario_row = self.store.create_scenario(
            title=draft.title,
            scenario_name_hint=draft.scenario_name or ctx.request.scenario_name_hint or draft.title,
        )
        revision_row = self.store.create_scenario_revision(
            scenario_id=scenario_row.id,
            target_image=draft.target_image,
            summary=draft.summary,
            what_it_tests={"items": list(draft.what_it_tests)},
            observable_problem_statement=draft.observable_problem_statement,
            initial_user_message=draft.initial_user_message,
            sabotage_plan={"steps": list(draft.sabotage_procedure)},
            verification_plan={"probes": [item.to_dict() for item in draft.verification_probes]},
            judge_rubric={"items": list(draft.judge_rubric)},
            planner_metadata={
                **draft.planner_metadata,
                "repair_checks": [item.to_dict() for item in draft.repair_checks],
                "turn_budget": draft.turn_budget,
                "metadata": draft.metadata,
            },
        )
        scenario = self._persisted_scenario(draft, scenario_row.id, revision_row.id, revision_row.revision_number)

        if ctx.setup_run is None:
            # First design pass — create the setup run
            setup_run = self.store.create_setup_run(
                scenario_revision_id=revision_row.id,
                max_corrections=ctx.max_corrections,
                backend_metadata={"group_id": ctx.group_id, "requested_target_image": scenario.target_image},
            )
            ctx.setup_run = setup_run
        else:
            # Scrap-and-restart: reuse the existing setup_run but record new revision
            self.store.update_setup_run_status(
                setup_run_id=ctx.setup_run.id,
                status=ScenarioSetupStatus.RUNNING.value,
                correction_count=ctx.correction_count,
                backend_metadata={"scrap_restart": True, "scrap_count": ctx.scrap_count},
            )

        ctx.scenario = scenario
        ctx.scenario_row = scenario_row
        ctx.revision_row = revision_row
        ctx.instructions = scenario.sabotage_procedure
        ctx.correction_count = 0

        return ScenarioBuilderState.LAUNCH_STAGING

    def _handle_launch_staging(self, ctx: ScenarioBuilderContext) -> ScenarioBuilderState:
        staging_handle = self.backend.launch_staging(
            ctx.group_id,
            ctx.scenario_row.scenario_name,
            target_image=ctx.scenario.target_image,
        )
        ctx.staging_handle = staging_handle

        self.store.update_setup_run_status(
            setup_run_id=ctx.setup_run.id,
            status=ScenarioSetupStatus.RUNNING.value,
            staging_handle_id=staging_handle.remote_id,
            correction_count=ctx.correction_count,
            backend_metadata=staging_handle.metadata,
        )
        self.backend.wait_until_ready(staging_handle)

        # SSM needs no runtime setup; skip configure_controller_runtime for the FSM path
        ctx.controller = self.controller_factory.open(staging_handle, purpose=f"setup-{ctx.setup_run.id}")

        return ScenarioBuilderState.BUILD

    def _handle_build(self, ctx: ScenarioBuilderContext) -> ScenarioBuilderState:
        results = ctx.controller.execute_commands(
            tuple(ctx.instructions),
            session_key=f"{ctx.setup_run.id}-sabotage-{ctx.round_index}",
        )
        ctx.last_command_results = results
        self._append_command_results(ctx, results)
        return ScenarioBuilderState.VERIFY

    def _handle_verify(self, ctx: ScenarioBuilderContext) -> ScenarioBuilderState:
        results = ctx.controller.execute_commands(
            tuple(item.command for item in ctx.scenario.verification_probes),
            session_key=f"{ctx.setup_run.id}-verify-{ctx.round_index}",
        )
        ctx.last_command_results = results
        self._append_command_results(ctx, results)
        return ScenarioBuilderState.REVIEW

    def _handle_review(self, ctx: ScenarioBuilderContext) -> ScenarioBuilderState:
        from dataclasses import replace

        decision = self.planner.review_sabotage(
            ctx.scenario,
            round_index=ctx.round_index,
            command_results=ctx.last_command_results,
            correction_count=ctx.correction_count,
        )
        ctx.last_review_decision = decision

        if decision.updated_observable_problem_statement:
            updated_problem_statement = decision.updated_observable_problem_statement
            self.store.update_scenario_revision_observable_problem_statement(
                revision_id=ctx.revision_row.id,
                observable_problem_statement=updated_problem_statement,
            )
            opener_message = ctx.scenario.initial_user_message
            reviewed_opening_message = opener_message
            try:
                reviewed_opening_message = self.planner.review_initial_user_message(
                    scenario=replace(
                        ctx.scenario,
                        observable_problem_statement=updated_problem_statement,
                    ),
                    draft_message=updated_problem_statement,
                ).final_message
            except Exception:
                reviewed_opening_message = opener_message
            else:
                self.store.update_scenario_revision_opening_message(
                    revision_id=ctx.revision_row.id,
                    initial_user_message=reviewed_opening_message,
                )
            ctx.scenario = replace(
                ctx.scenario,
                observable_problem_statement=updated_problem_statement,
                initial_user_message=reviewed_opening_message,
            )

        self.store.append_setup_event(
            setup_run_id=ctx.setup_run.id,
            round_index=ctx.round_index,
            seq=ctx.seq,
            actor_role="planner",
            event_kind="decision",
            payload=decision.to_dict(),
        )
        ctx.seq += 1

        if decision.outcome.value == "approve":
            return ScenarioBuilderState.CREATE_IMAGE

        # outcome == "correct"
        if ctx.correction_count < ctx.max_corrections:
            return ScenarioBuilderState.FIX_PLAN

        # Exhausted corrections → scrap
        return ScenarioBuilderState.SCRAP

    def _handle_fix_plan(self, ctx: ScenarioBuilderContext) -> ScenarioBuilderState:
        fix_commands = self.planner.plan_rectification(
            ctx.scenario,
            failed_command_results=ctx.last_command_results,
            correction_instructions=ctx.last_review_decision.correction_instructions,
            round_index=ctx.round_index,
        )
        if not fix_commands:
            ctx.failure_reason = "planner_returned_empty_rectification"
            ctx.failure_metadata = {}
            return ScenarioBuilderState.FAILED

        validation_errors: list[str] = []
        for index, command in enumerate(fix_commands, start=1):
            validation_error = validate_sabotage_step(command, index, field_name="rectification_commands")
            if validation_error is not None:
                validation_errors.append(validation_error)

        if validation_errors:
            ctx.failure_reason = "invalid_rectification_commands"
            ctx.failure_metadata = {
                "invalid_rectification_commands": list(fix_commands),
                "validation_errors": validation_errors,
                "round_index": ctx.round_index,
            }
            return ScenarioBuilderState.FAILED

        ctx.fix_commands = fix_commands
        return ScenarioBuilderState.FIX_EXECUTE

    def _handle_fix_execute(self, ctx: ScenarioBuilderContext) -> ScenarioBuilderState:
        results = ctx.controller.execute_commands(
            ctx.fix_commands,
            session_key=f"{ctx.setup_run.id}-fix-{ctx.round_index}",
        )
        ctx.last_command_results = results
        self._append_command_results(ctx, results)
        ctx.correction_count += 1
        ctx.round_index += 1
        # Reset instructions to sabotage_procedure so BUILD re-runs full sabotage if needed
        # (actually for FIX flow we go directly back to VERIFY, not BUILD)
        return ScenarioBuilderState.VERIFY

    def _handle_scrap(self, ctx: ScenarioBuilderContext) -> ScenarioBuilderState:
        if ctx.scrap_count >= ctx.scrap_budget:
            ctx.failure_reason = "exhausted_scrap_budget"
            ctx.failure_metadata = {"scrap_count": ctx.scrap_count, "scrap_budget": ctx.scrap_budget}
            return ScenarioBuilderState.FAILED

        # Close controller and destroy staging instance
        if ctx.controller is not None:
            ctx.controller.close()
            ctx.controller = None
        if ctx.staging_handle is not None:
            self.backend.destroy_handle(ctx.staging_handle)
            ctx.staging_handle = None

        # Reset for fresh attempt
        ctx.correction_count = 0
        ctx.round_index = 0
        ctx.scrap_count += 1

        return ScenarioBuilderState.DESIGN

    def _handle_create_image(self, ctx: ScenarioBuilderContext) -> ScenarioBuilderState:
        # Close the controller before snapshotting
        if ctx.controller is not None:
            ctx.controller.close()
            ctx.controller = None

        # Clear controller runtime (no-op for SSM; kept for interface compatibility)
        cleanup_metadata = self.backend.clear_controller_runtime(ctx.staging_handle)
        if cleanup_metadata:
            self.store.update_setup_run_status(
                setup_run_id=ctx.setup_run.id,
                status=ScenarioSetupStatus.RUNNING.value,
                correction_count=ctx.correction_count,
                backend_metadata=cleanup_metadata,
            )

        self.store.update_setup_run_status(
            setup_run_id=ctx.setup_run.id,
            status=ScenarioSetupStatus.CREATING_BROKEN_IMAGE.value,
            correction_count=ctx.correction_count,
            planner_approved=True,
        )

        broken_image_id = ""
        try:
            broken_image_id = self.backend.request_broken_image(
                ctx.staging_handle,
                ctx.group_id,
                ctx.scenario_row.scenario_name,
            )
            requested_at = datetime.now(timezone.utc).isoformat()
            self.store.update_setup_run_status(
                setup_run_id=ctx.setup_run.id,
                status=ScenarioSetupStatus.CREATING_BROKEN_IMAGE.value,
                correction_count=ctx.correction_count,
                broken_image_id=broken_image_id,
                backend_metadata={
                    "broken_image_id": broken_image_id,
                    "broken_image_state": "pending",
                    "broken_image_requested_at": requested_at,
                    "broken_image_last_checked_at": requested_at,
                },
            )
            self.backend.wait_for_broken_image(
                broken_image_id,
                progress_callback=lambda metadata: self.store.update_setup_run_status(
                    setup_run_id=ctx.setup_run.id,
                    status=ScenarioSetupStatus.CREATING_BROKEN_IMAGE.value,
                    correction_count=ctx.correction_count,
                    broken_image_id=broken_image_id,
                    backend_metadata=metadata,
                ),
            )
        except TimeoutError as exc:
            self.store.update_setup_run_status(
                setup_run_id=ctx.setup_run.id,
                status=ScenarioSetupStatus.FAILED_INFRA.value,
                correction_count=ctx.correction_count,
                broken_image_id=broken_image_id or None,
                failure_reason="broken_image_creation_timeout",
                backend_metadata={
                    "broken_image_id": broken_image_id,
                    "broken_image_state": "timeout",
                },
            )
            self.store.update_scenario_status(
                scenario_id=ctx.scenario_row.id,
                lifecycle_status=ScenarioLifecycleStatus.FAILED_SETUP.value,
                verification_status="failed",
            )
            raise ScenarioSetupFailedError(
                f"Timed out waiting for broken image creation for {ctx.scenario_row.scenario_name}."
            ) from exc
        except Exception as exc:
            self.store.update_setup_run_status(
                setup_run_id=ctx.setup_run.id,
                status=ScenarioSetupStatus.FAILED_INFRA.value,
                correction_count=ctx.correction_count,
                broken_image_id=broken_image_id or None,
                failure_reason="broken_image_creation_failed",
                backend_metadata={
                    "broken_image_id": broken_image_id,
                    "broken_image_state": "failed",
                },
            )
            self.store.update_scenario_status(
                scenario_id=ctx.scenario_row.id,
                lifecycle_status=ScenarioLifecycleStatus.FAILED_SETUP.value,
                verification_status="failed",
            )
            raise ScenarioSetupFailedError(
                f"Broken image creation failed for {ctx.scenario_row.scenario_name}."
            ) from exc

        # Success
        self.store.update_setup_run_status(
            setup_run_id=ctx.setup_run.id,
            status=ScenarioSetupStatus.VERIFIED.value,
            correction_count=ctx.correction_count,
            broken_image_id=broken_image_id,
            backend_metadata={"broken_image_state": "available"},
        )
        self.store.mark_scenario_verified(
            scenario_id=ctx.scenario_row.id,
            revision_id=ctx.revision_row.id,
            lifecycle_status=ScenarioLifecycleStatus.VERIFIED.value,
            verification_status="verified",
        )
        ctx.broken_image_id = broken_image_id
        return ScenarioBuilderState.DONE

    def _handle_done(self, ctx: ScenarioBuilderContext) -> ScenarioBuilderState:
        # No-op — result is assembled in run()
        return ScenarioBuilderState.DONE

    def _handle_failed(self, ctx: ScenarioBuilderContext) -> ScenarioBuilderState:
        reason = ctx.failure_reason or "fsm_internal_failure"

        if ctx.setup_run is not None:
            current_setup = self.store.get_setup_run(ctx.setup_run.id)
            if current_setup and current_setup.status not in _TERMINAL_SETUP_STATUSES:
                status = (
                    ScenarioSetupStatus.FAILED_MAX_CORRECTIONS.value
                    if "correction" in reason or "scrap" in reason
                    else ScenarioSetupStatus.FAILED_INFRA.value
                )
                self.store.update_setup_run_status(
                    setup_run_id=ctx.setup_run.id,
                    status=status,
                    correction_count=ctx.correction_count,
                    failure_reason=reason,
                    backend_metadata=ctx.failure_metadata if ctx.failure_metadata else None,
                )

            if ctx.scenario_row is not None:
                self.store.update_scenario_status(
                    scenario_id=ctx.scenario_row.id,
                    lifecycle_status=ScenarioLifecycleStatus.FAILED_SETUP.value,
                    verification_status="failed",
                )

        scenario_name = ctx.scenario_row.scenario_name if ctx.scenario_row is not None else "unknown"
        raise ScenarioSetupFailedError(
            f"Scenario setup failed ({reason}) for {scenario_name}."
        )
