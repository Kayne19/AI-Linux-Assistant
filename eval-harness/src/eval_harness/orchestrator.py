from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .adapters.base import SolverAdapter
from .artifacts import ArtifactStore
from .backends.base import SandboxBackend, SandboxHandle
from .controllers.base import SandboxController
from .models import (
    ArtifactPack,
    CleanupRecord,
    ScenarioSpec,
    TurnRecord,
    VariantArtifact,
    VariantLifecycle,
    VariantSpec,
    VerificationResult,
    new_id,
    utc_now_iso,
)
from .scenario import validate_scenario


class EvalOrchestrator:
    """Workflow owner for one scenario group run."""

    def __init__(
        self,
        *,
        backend: SandboxBackend,
        controller_factory: Callable[[SandboxHandle, str], SandboxController],
        adapter: SolverAdapter,
        artifact_store: ArtifactStore | None = None,
        verification_agent_id: str = "setup",
    ):
        self.backend = backend
        self.controller_factory = controller_factory
        self.adapter = adapter
        self.artifact_store = artifact_store
        self.verification_agent_id = verification_agent_id

    def _current_status(
        self,
        *,
        setup_failed: bool,
        variant_artifacts: list[VariantArtifact],
        cleanup_complete: bool,
        orchestrator_error: str,
    ) -> str:
        if orchestrator_error:
            return VariantLifecycle.FAILED.value
        if setup_failed:
            return VariantLifecycle.SETUP_FAILED.value
        if not cleanup_complete and not variant_artifacts:
            return VariantLifecycle.RUNNING.value
        if any(item.lifecycle == VariantLifecycle.FAILED for item in variant_artifacts):
            return VariantLifecycle.FAILED.value
        return VariantLifecycle.COMPLETED.value

    def _build_pack(
        self,
        *,
        group_id: str,
        scenario: ScenarioSpec,
        started_at: str,
        staging_handle_id: str,
        broken_image_id: str,
        setup_log: list[dict[str, object]],
        broken_state_results: tuple[VerificationResult, ...],
        variant_artifacts: list[VariantArtifact],
        cleanup_records: list[CleanupRecord],
        controller_name: str,
        setup_failed: bool,
        cleanup_complete: bool,
        orchestrator_error: str,
    ) -> ArtifactPack:
        status = self._current_status(
            setup_failed=setup_failed,
            variant_artifacts=variant_artifacts,
            cleanup_complete=cleanup_complete,
            orchestrator_error=orchestrator_error,
        )
        metadata = {
            "status": status,
            "cleanup_complete": cleanup_complete,
            "broken_state_verified": bool(broken_state_results) and all(item.success for item in broken_state_results),
            "variant_failures": sum(1 for item in variant_artifacts if item.lifecycle == VariantLifecycle.FAILED),
        }
        if orchestrator_error:
            metadata["orchestrator_error"] = orchestrator_error
        return ArtifactPack(
            group_id=group_id,
            scenario=scenario,
            backend_name=self.backend.name,
            controller_name=controller_name,
            adapter_name=self.adapter.name,
            staging_handle_id=staging_handle_id,
            broken_image_id=broken_image_id,
            setup_log=tuple(setup_log),
            broken_state_results=broken_state_results,
            variant_artifacts=tuple(variant_artifacts),
            cleanup_records=tuple(cleanup_records),
            started_at=started_at,
            finished_at=utc_now_iso(),
            metadata=metadata,
        )

    def _persist_pack(self, pack: ArtifactPack) -> None:
        if self.artifact_store is not None:
            self.artifact_store.save_pack(pack)

    def _build_proxy_prompt(self, assistant_message: str) -> str:
        return (
            "The assistant said:\n\n"
            f"{assistant_message}\n\n"
            "Reply as the sandbox proxy. If the assistant asked for a command to be run, "
            "run it and return the raw output without extra narration."
        )

    def _extract_mode_override(self, message: str, variant: VariantSpec) -> tuple[str, str | None]:
        if not variant.allow_proxy_mode_override:
            return message, None
        mode_prefixes = dict(variant.metadata.get("proxy_mode_prefixes", {}) or {})
        for prefix, mode in mode_prefixes.items():
            normalized_prefix = str(prefix).strip()
            if normalized_prefix and message.startswith(normalized_prefix):
                normalized_mode = str(mode).strip() or None
                return message[len(normalized_prefix):].strip(), normalized_mode
        return message, None

    def _is_terminal_response(self, assistant_message: str, scenario: ScenarioSpec) -> bool:
        markers = tuple(str(item).lower() for item in scenario.metadata.get("terminal_markers", []) or [])
        if not markers:
            return False
        normalized = assistant_message.lower()
        return any(marker in normalized for marker in markers)

    def _run_variant(self, group_id: str, scenario: ScenarioSpec, variant: VariantSpec, handle: SandboxHandle) -> VariantArtifact:
        started_at = utc_now_iso()
        controller = None
        session = None
        transcript: list[TurnRecord] = []
        run_ids: list[str] = []
        run_events = []
        adapter_debug: dict[str, object] = {}
        error_message = ""
        lifecycle = VariantLifecycle.COMPLETED
        resolution_results: tuple[VerificationResult, ...] = ()
        repair_success = False

        try:
            controller = self.controller_factory(handle, f"{group_id}-{variant.name}")
            session = self.adapter.create_session(scenario, group_id, variant)
            if scenario.context_seed:
                session.seed_context(scenario.context_seed)

            user_message = scenario.opening_user_message
            for _turn in range(scenario.turn_budget):
                user_message, mode_override = self._extract_mode_override(user_message, variant)
                proxy_mode_signal = mode_override

                result = session.submit_user_message(user_message, mode_override=mode_override)
                transcript.append(
                    TurnRecord(
                        role="user",
                        content=user_message,
                        metadata={"proxy_mode_signal": proxy_mode_signal} if proxy_mode_signal else {},
                    )
                )
                transcript.append(
                    TurnRecord(
                        role="assistant",
                        content=result.assistant_message,
                        metadata={
                            "run_id": result.run_id or "",
                            "status": result.status,
                            "terminal_event_type": result.terminal_event_type,
                        },
                    )
                )
                if result.run_id:
                    run_ids.append(result.run_id)
                run_events.extend(result.events)
                if result.debug:
                    adapter_debug[result.run_id or new_id("debug")] = result.debug

                if result.status != "completed":
                    lifecycle = VariantLifecycle.FAILED
                    error_message = f"solver run ended with status {result.status}"
                    break

                if self._is_terminal_response(result.assistant_message, scenario):
                    break

                user_message = controller.send(
                    agent_id=variant.proxy_agent_id,
                    message=self._build_proxy_prompt(result.assistant_message),
                    session_key=f"{group_id}-{variant.name}-proxy",
                )
                if not user_message.strip():
                    break
        except Exception as exc:
            lifecycle = VariantLifecycle.FAILED
            error_message = str(exc)
        finally:
            if controller is not None:
                try:
                    resolution_results = tuple(
                        controller.run_verification(
                            check,
                            agent_id=self.verification_agent_id,
                            session_key=f"{group_id}-{variant.name}-verify",
                        )
                        for check in scenario.resolution_checks
                    )
                    repair_success = bool(resolution_results) and all(result.success for result in resolution_results)
                except Exception as exc:
                    lifecycle = VariantLifecycle.FAILED
                    error_message = error_message or f"resolution verification failed: {exc}"

            if session is not None:
                try:
                    adapter_cleanup = session.close()
                    adapter_debug["adapter_cleanup"] = adapter_cleanup
                except Exception as exc:
                    lifecycle = VariantLifecycle.FAILED
                    error_message = error_message or f"adapter cleanup failed: {exc}"
            if controller is not None:
                try:
                    controller.close()
                except Exception as exc:
                    lifecycle = VariantLifecycle.FAILED
                    error_message = error_message or f"controller cleanup failed: {exc}"

        return VariantArtifact(
            variant_name=variant.name,
            lifecycle=lifecycle,
            transcript=tuple(transcript),
            run_ids=tuple(run_ids),
            run_events=tuple(run_events),
            adapter_debug=adapter_debug,
            resolution_results=resolution_results,
            error_message=error_message,
            started_at=started_at,
            finished_at=utc_now_iso(),
            metadata={
                **variant.metadata,
                "proxy_agent_id": variant.proxy_agent_id,
                "solver_mode": variant.solver_mode or "off",
                "repair_success": repair_success,
            },
        )

    def run_group(self, scenario: ScenarioSpec, *, group_id: str | None = None) -> ArtifactPack:
        validate_scenario(scenario)
        group_id = group_id or new_id("group")
        started_at = utc_now_iso()
        setup_log: list[dict[str, object]] = []
        cleanup_records: list[CleanupRecord] = []
        broken_state_results: tuple[VerificationResult, ...] = ()
        variant_artifacts: list[VariantArtifact] = []
        staging_handle_id = ""
        broken_image_id = ""
        staging = None
        variant_handles: dict[str, SandboxHandle] = {}
        staging_controller = None
        controller_name = "unknown"
        setup_failed = False
        orchestrator_error = ""

        try:
            staging = self.backend.launch_staging(group_id, scenario.scenario_id)
            staging_handle_id = staging.remote_id
            self.backend.wait_until_ready(staging)
            staging_controller = self.controller_factory(staging, f"{group_id}-setup")
            controller_name = staging_controller.name

            for step in scenario.setup_steps:
                response = staging_controller.send(
                    agent_id="setup",
                    message=step,
                    session_key=f"{group_id}-setup",
                )
                setup_log.append({"step": step, "response": response})

            broken_state_results = tuple(
                staging_controller.run_verification(
                    check,
                    agent_id=self.verification_agent_id,
                    session_key=f"{group_id}-broken",
                )
                for check in scenario.broken_state_checks
            )
            self._persist_pack(
                self._build_pack(
                    group_id=group_id,
                    scenario=scenario,
                    started_at=started_at,
                    staging_handle_id=staging_handle_id,
                    broken_image_id=broken_image_id,
                    setup_log=setup_log,
                    broken_state_results=broken_state_results,
                    variant_artifacts=variant_artifacts,
                    cleanup_records=cleanup_records,
                    controller_name=controller_name,
                    setup_failed=False,
                    cleanup_complete=False,
                    orchestrator_error="",
                )
            )
            if not all(result.success for result in broken_state_results):
                setup_failed = True
            else:
                broken_image_id = self.backend.create_broken_image(staging, group_id, scenario.scenario_id)
                variant_handles = self.backend.launch_variant_clones(
                    group_id,
                    scenario.scenario_id,
                    broken_image_id,
                    [variant.name for variant in scenario.variants],
                )
                for handle in variant_handles.values():
                    self.backend.wait_until_ready(handle)

                with ThreadPoolExecutor(max_workers=len(scenario.variants)) as executor:
                    futures = {
                        executor.submit(self._run_variant, group_id, scenario, variant, variant_handles[variant.name]): variant.name
                        for variant in scenario.variants
                    }
                    results_by_name: dict[str, VariantArtifact] = {}
                    for future in as_completed(futures):
                        variant_name = futures[future]
                        results_by_name[variant_name] = future.result()
                        partial_results = [results_by_name[name] for name in [variant.name for variant in scenario.variants] if name in results_by_name]
                        self._persist_pack(
                            self._build_pack(
                                group_id=group_id,
                                scenario=scenario,
                                started_at=started_at,
                                staging_handle_id=staging_handle_id,
                                broken_image_id=broken_image_id,
                                setup_log=setup_log,
                                broken_state_results=broken_state_results,
                                variant_artifacts=partial_results,
                                cleanup_records=cleanup_records,
                                controller_name=controller_name,
                                setup_failed=False,
                                cleanup_complete=False,
                                orchestrator_error="",
                            )
                        )
                    variant_artifacts = [results_by_name[variant.name] for variant in scenario.variants]
        except Exception as exc:
            orchestrator_error = str(exc)
        finally:
            if staging_controller is not None:
                try:
                    staging_controller.close()
                except Exception as exc:
                    cleanup_records.append(
                        CleanupRecord(
                            resource_type="controller",
                            resource_id=staging_handle_id,
                            action="close",
                            success=False,
                            details=str(exc),
                        )
                    )

            for variant_name, handle in variant_handles.items():
                try:
                    self.backend.destroy_handle(handle)
                    cleanup_records.append(
                        CleanupRecord(
                            resource_type="instance",
                            resource_id=handle.remote_id,
                            action=f"terminate:{variant_name}",
                            success=True,
                        )
                    )
                except Exception as exc:
                    cleanup_records.append(
                        CleanupRecord(
                            resource_type="instance",
                            resource_id=handle.remote_id,
                            action=f"terminate:{variant_name}",
                            success=False,
                            details=str(exc),
                        )
                    )

            if broken_image_id:
                try:
                    self.backend.destroy_broken_image(broken_image_id)
                    cleanup_records.append(
                        CleanupRecord(
                            resource_type="image",
                            resource_id=broken_image_id,
                            action="destroy-broken-image",
                            success=True,
                        )
                    )
                except Exception as exc:
                    cleanup_records.append(
                        CleanupRecord(
                            resource_type="image",
                            resource_id=broken_image_id,
                            action="destroy-broken-image",
                            success=False,
                            details=str(exc),
                        )
                    )

            if staging is not None:
                try:
                    self.backend.destroy_handle(staging)
                    cleanup_records.append(
                        CleanupRecord(
                            resource_type="instance",
                            resource_id=staging.remote_id,
                            action="terminate:staging",
                            success=True,
                        )
                    )
                except Exception as exc:
                    cleanup_records.append(
                        CleanupRecord(
                            resource_type="instance",
                            resource_id=staging.remote_id,
                            action="terminate:staging",
                            success=False,
                            details=str(exc),
                        )
                    )

        pack = self._build_pack(
            group_id=group_id,
            scenario=scenario,
            started_at=started_at,
            staging_handle_id=staging_handle_id,
            broken_image_id=broken_image_id,
            setup_log=setup_log,
            broken_state_results=broken_state_results,
            variant_artifacts=variant_artifacts,
            cleanup_records=cleanup_records,
            controller_name=controller_name,
            setup_failed=setup_failed,
            cleanup_complete=True,
            orchestrator_error=orchestrator_error,
        )
        self._persist_pack(pack)
        return pack
