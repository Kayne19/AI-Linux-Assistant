from __future__ import annotations

from dataclasses import dataclass, replace

from ..backends.base import SandboxBackend, SandboxHandle
from ..controllers.base import SandboxController, SandboxControllerFactory
from ..models import PlannerScenarioRequest, ScenarioLifecycleStatus, ScenarioSetupStatus, ScenarioSpec
from ..persistence.store import EvalHarnessStore
from ..planners.base import ScenarioPlanner
from ..scenario import validate_scenario


class ScenarioSetupFailedError(RuntimeError):
    """Raised when planner-driven scenario setup does not converge."""


@dataclass(frozen=True)
class ScenarioSetupResult:
    scenario_id: str
    scenario_name: str
    scenario_revision_id: str
    setup_run_id: str
    broken_image_id: str


class ScenarioSetupOrchestrator:
    def __init__(
        self,
        *,
        backend: SandboxBackend,
        controller_factory: SandboxControllerFactory,
        planner: ScenarioPlanner,
        store: EvalHarnessStore,
    ):
        self.backend = backend
        self.controller_factory = controller_factory
        self.planner = planner
        self.store = store

    def _persisted_scenario(self, draft: ScenarioSpec, scenario_id: str, revision_id: str, revision_number: int) -> ScenarioSpec:
        persisted = replace(
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
        return persisted

    def _append_message(self, setup_run_id: str, round_index: int, seq: int, *, actor_role: str, role: str, content: str) -> int:
        self.store.append_setup_event(
            setup_run_id=setup_run_id,
            round_index=round_index,
            seq=seq,
            actor_role=actor_role,
            event_kind="message",
            payload={"role": role, "content": content},
        )
        return seq + 1

    def _append_command_results(self, setup_run_id: str, round_index: int, seq: int, command_results: tuple) -> int:
        next_seq = seq
        for result in command_results:
            self.store.append_setup_event(
                setup_run_id=setup_run_id,
                round_index=round_index,
                seq=next_seq,
                actor_role="controller",
                event_kind="command_result",
                payload=result.to_dict(),
            )
            next_seq += 1
        return next_seq

    def run(
        self,
        request: PlannerScenarioRequest,
        *,
        group_id: str,
        sabotage_agent_id: str = "sabotage_agent",
        verification_agent_id: str = "verification_executor",
        max_corrections: int = 2,
    ) -> ScenarioSetupResult:
        draft = self.planner.generate_scenario(request)
        validate_scenario(draft)

        scenario_row = self.store.create_scenario(
            title=draft.title,
            scenario_name_hint=draft.scenario_name or request.scenario_name_hint or draft.title,
        )
        revision_row = self.store.create_scenario_revision(
            scenario_id=scenario_row.id,
            target_image=draft.target_image,
            summary=draft.summary,
            what_it_tests={"items": list(draft.what_it_tests)},
            observable_problem_statement=draft.observable_problem_statement,
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
        staging_handle: SandboxHandle | None = None
        controller: SandboxController | None = None
        setup_run = self.store.create_setup_run(
            scenario_revision_id=revision_row.id,
            max_corrections=max_corrections,
            backend_metadata={"group_id": group_id},
        )
        seq = 1
        correction_count = 0
        round_index = 0
        instructions = scenario.sabotage_procedure

        try:
            staging_handle = self.backend.launch_staging(group_id, scenario_row.scenario_name)
            self.store.update_setup_run_status(
                setup_run_id=setup_run.id,
                status=ScenarioSetupStatus.RUNNING.value,
                staging_handle_id=staging_handle.remote_id,
                correction_count=correction_count,
            )
            self.backend.wait_until_ready(staging_handle)
            controller = self.controller_factory.open(staging_handle, purpose=f"setup-{setup_run.id}")
            seq = self._append_message(
                setup_run.id,
                round_index,
                seq,
                actor_role="planner",
                role="planner",
                content=f"Generated scenario {scenario.revision_ref}",
            )
            while True:
                sabotage_prompt = "\n".join(instructions)
                seq = self._append_message(
                    setup_run.id,
                    round_index,
                    seq,
                    actor_role="planner",
                    role="planner",
                    content=sabotage_prompt,
                )
                sabotage_response = controller.send(
                    agent_id=sabotage_agent_id,
                    message=sabotage_prompt,
                    session_key=f"{setup_run.id}-sabotage-{round_index}",
                )
                seq = self._append_message(
                    setup_run.id,
                    round_index,
                    seq,
                    actor_role="sabotage_agent",
                    role="sabotage_agent",
                    content=sabotage_response,
                )
                command_results = controller.execute_commands(
                    tuple(item.command for item in scenario.verification_probes),
                    agent_id=verification_agent_id,
                    session_key=f"{setup_run.id}-verify-{round_index}",
                )
                seq = self._append_command_results(setup_run.id, round_index, seq, command_results)
                decision = self.planner.review_sabotage(
                    scenario,
                    round_index=round_index,
                    command_results=command_results,
                    correction_count=correction_count,
                )
                if decision.updated_observable_problem_statement:
                    self.store.update_scenario_revision_observable_problem_statement(
                        revision_id=revision_row.id,
                        observable_problem_statement=decision.updated_observable_problem_statement,
                    )
                    scenario = replace(
                        scenario,
                        observable_problem_statement=decision.updated_observable_problem_statement,
                    )
                self.store.append_setup_event(
                    setup_run_id=setup_run.id,
                    round_index=round_index,
                    seq=seq,
                    actor_role="planner",
                    event_kind="decision",
                    payload=decision.to_dict(),
                )
                seq += 1

                if decision.outcome.value == "approve":
                    broken_image_id = self.backend.create_broken_image(
                        staging_handle,
                        group_id,
                        scenario_row.scenario_name,
                    )
                    self.store.update_setup_run_status(
                        setup_run_id=setup_run.id,
                        status=ScenarioSetupStatus.VERIFIED.value,
                        correction_count=correction_count,
                        broken_image_id=broken_image_id,
                        planner_approved=True,
                    )
                    self.store.mark_scenario_verified(
                        scenario_id=scenario_row.id,
                        revision_id=revision_row.id,
                        lifecycle_status=ScenarioLifecycleStatus.VERIFIED.value,
                        verification_status="verified",
                    )
                    return ScenarioSetupResult(
                        scenario_id=scenario_row.id,
                        scenario_name=scenario_row.scenario_name,
                        scenario_revision_id=revision_row.id,
                        setup_run_id=setup_run.id,
                        broken_image_id=broken_image_id,
                    )

                correction_count += 1
                if correction_count >= max_corrections:
                    self.store.update_setup_run_status(
                        setup_run_id=setup_run.id,
                        status=ScenarioSetupStatus.FAILED_MAX_CORRECTIONS.value,
                        correction_count=correction_count,
                        failure_reason=decision.summary,
                    )
                    self.store.update_scenario_status(
                        scenario_id=scenario_row.id,
                        lifecycle_status=ScenarioLifecycleStatus.FAILED_SETUP.value,
                        verification_status="failed",
                    )
                    raise ScenarioSetupFailedError(
                        f"Scenario setup failed after {correction_count} planner corrections for {scenario_row.scenario_name}."
                    )

                self.store.update_setup_run_status(
                    setup_run_id=setup_run.id,
                    status=ScenarioSetupStatus.NEEDS_CORRECTION.value,
                    correction_count=correction_count,
                    failure_reason=decision.summary,
                )
                if not decision.correction_instructions:
                    self.store.update_setup_run_status(
                        setup_run_id=setup_run.id,
                        status=ScenarioSetupStatus.FAILED_INFRA.value,
                        correction_count=correction_count,
                        failure_reason="planner_returned_empty_correction",
                    )
                    self.store.update_scenario_status(
                        scenario_id=scenario_row.id,
                        lifecycle_status=ScenarioLifecycleStatus.FAILED_SETUP.value,
                        verification_status="failed",
                    )
                    raise ScenarioSetupFailedError(
                        f"Planner requested a correction without instructions for {scenario_row.scenario_name}."
                    )
                instructions = decision.correction_instructions
                round_index += 1
        except Exception:
            current_setup = self.store.get_setup_run(setup_run.id)
            if current_setup and current_setup.status == ScenarioSetupStatus.RUNNING.value:
                self.store.update_setup_run_status(
                    setup_run_id=setup_run.id,
                    status=ScenarioSetupStatus.FAILED_INFRA.value,
                    correction_count=correction_count,
                    failure_reason="infra_failure",
                )
                self.store.update_scenario_status(
                    scenario_id=scenario_row.id,
                    lifecycle_status=ScenarioLifecycleStatus.FAILED_SETUP.value,
                    verification_status="failed",
                )
            raise
        finally:
            if controller is not None:
                controller.close()
            if staging_handle is not None:
                self.backend.destroy_handle(staging_handle)
