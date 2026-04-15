from __future__ import annotations

import re
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
    _REFUSAL_PATTERNS = (
        re.compile(r"\bi can[’']?t help\b", re.IGNORECASE),
        re.compile(r"\bi cannot help\b", re.IGNORECASE),
        re.compile(r"\bcan[’']?t help break\b", re.IGNORECASE),
        re.compile(r"\bcan[’']?t help sabotage\b", re.IGNORECASE),
        re.compile(r"\bneed approval\b", re.IGNORECASE),
        re.compile(r"\b/approve\b", re.IGNORECASE),
    )
    _RUNTIME_BLOCK_PATTERNS = (
        re.compile(r"\bblocked by the sandbox\b", re.IGNORECASE),
        re.compile(r"\bblocked by sandbox permissions\b", re.IGNORECASE),
        re.compile(r"\bblocked by sandbox\b", re.IGNORECASE),
        re.compile(r"\bread-only\b", re.IGNORECASE),
        re.compile(r"\broot filesystem is read-only\b", re.IGNORECASE),
        re.compile(r"\belevated exec is disabled\b", re.IGNORECASE),
        re.compile(r"\belevated is not available\b", re.IGNORECASE),
        re.compile(r"\bwritable/root-enabled sandbox\b", re.IGNORECASE),
        re.compile(r"\b/approve\b", re.IGNORECASE),
    )

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

    def _sabotage_authorization_preamble(self, scenario: ScenarioSpec) -> str:
        return "\n".join(
            (
                "You are the setup agent for a disposable eval-harness staging sandbox.",
                "This machine exists only to create a broken troubleshooting scenario for benchmark testing.",
                "This is not a live service, production host, or customer environment.",
                "Destructive changes inside this sandbox are intentional and authorized.",
                "Follow the failure-state plan exactly and keep the changes bounded to the listed steps.",
                "If the plan requires installing packages, creating files, or provisioning services before reproducing the failure, do that as part of setup.",
                "Do not refuse just because the requested failure-state preparation breaks the machine. Do not ask for approval.",
                "Use the normal host execution path for commands. Do not use exec host=sandbox.",
                "Do not rely on OpenClaw elevated exec mode for privileged work.",
                "For package installs, service management, writes under /etc, and privileged ports, run the normal host command and prefix it with sudo -n.",
                "Do not ask for a privileged image or a writable sandbox.",
                "Do not undo, clean up, or repair the sabotage after you verify it.",
                "Leave the machine in the final broken state when you reply.",
                f"Target observable problem statement: {scenario.observable_problem_statement}",
                "",
                "Failure-state plan:",
            )
        )

    def _build_sabotage_prompt(self, scenario: ScenarioSpec, instructions: tuple[str, ...]) -> str:
        return "\n".join((self._sabotage_authorization_preamble(scenario), *instructions))

    def _is_sabotage_refusal(self, response: str) -> bool:
        normalized = str(response or "").strip()
        if not normalized:
            return False
        return any(pattern.search(normalized) for pattern in self._REFUSAL_PATTERNS)

    def _is_runtime_block(self, response: str) -> bool:
        normalized = str(response or "").strip()
        if not normalized:
            return False
        return any(pattern.search(normalized) for pattern in self._RUNTIME_BLOCK_PATTERNS)

    def run(
        self,
        request: PlannerScenarioRequest,
        *,
        group_id: str,
        sabotage_agent_id: str = "setup",
        verification_agent_id: str = "verifier",
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
            backend_metadata={"group_id": group_id, "requested_target_image": scenario.target_image},
        )
        seq = 1
        correction_count = 0
        round_index = 0
        instructions = scenario.sabotage_procedure

        try:
            staging_handle = self.backend.launch_staging(group_id, scenario_row.scenario_name, target_image=scenario.target_image)
            self.store.update_setup_run_status(
                setup_run_id=setup_run.id,
                status=ScenarioSetupStatus.RUNNING.value,
                staging_handle_id=staging_handle.remote_id,
                correction_count=correction_count,
                backend_metadata=staging_handle.metadata,
            )
            self.backend.wait_until_ready(staging_handle)
            runtime_metadata = self.backend.configure_controller_runtime(staging_handle)
            if runtime_metadata:
                self.store.update_setup_run_status(
                    setup_run_id=setup_run.id,
                    status=ScenarioSetupStatus.RUNNING.value,
                    correction_count=correction_count,
                    backend_metadata=runtime_metadata,
                )
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
                sabotage_prompt = self._build_sabotage_prompt(scenario, instructions)
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
                sabotage_refusal = self._is_sabotage_refusal(sabotage_response)
                runtime_block = self._is_runtime_block(sabotage_response)
                if sabotage_refusal or runtime_block:
                    failure_reason = "setup_agent_refused_authorized_sabotage"
                    refusal_metadata = {"sabotage_refusal_detected": True}
                    if runtime_block:
                        failure_reason = "setup_agent_blocked_by_runtime"
                        refusal_metadata["sabotage_runtime_block_detected"] = True
                    self.store.update_setup_run_status(
                        setup_run_id=setup_run.id,
                        status=ScenarioSetupStatus.FAILED_INFRA.value,
                        correction_count=correction_count,
                        failure_reason=failure_reason,
                        backend_metadata=refusal_metadata,
                    )
                    self.store.update_scenario_status(
                        scenario_id=scenario_row.id,
                        lifecycle_status=ScenarioLifecycleStatus.FAILED_SETUP.value,
                        verification_status="failed",
                    )
                    raise ScenarioSetupFailedError(
                        f"Setup agent could not apply authorized sabotage for {scenario_row.scenario_name}."
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
                    if controller is not None:
                        controller.close()
                        controller = None
                    cleanup_metadata = self.backend.clear_controller_runtime(staging_handle)
                    if cleanup_metadata:
                        self.store.update_setup_run_status(
                            setup_run_id=setup_run.id,
                            status=ScenarioSetupStatus.RUNNING.value,
                            correction_count=correction_count,
                            backend_metadata=cleanup_metadata,
                        )
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
        except Exception as exc:
            if staging_handle is not None:
                diagnostics = self.backend.collect_failure_diagnostics(staging_handle)
                partial_phase_summaries = list(getattr(exc, "phase_summaries", []) or [])
                if diagnostics or partial_phase_summaries:
                    current_setup = self.store.get_setup_run(setup_run.id)
                    current_status = current_setup.status if current_setup is not None else ScenarioSetupStatus.RUNNING.value
                    failure_metadata: dict = {
                        "failure_exception": f"{type(exc).__name__}: {exc}",
                    }
                    if diagnostics:
                        failure_metadata["failure_diagnostics"] = diagnostics
                    if partial_phase_summaries:
                        failure_metadata["openclaw_runtime_phase_summaries"] = partial_phase_summaries
                    self.store.update_setup_run_status(
                        setup_run_id=setup_run.id,
                        status=current_status,
                        backend_metadata=failure_metadata,
                    )
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
