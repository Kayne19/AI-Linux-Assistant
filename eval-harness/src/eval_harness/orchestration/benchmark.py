from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Any

from ..adapters.base import SubjectAdapter, SubjectSession
from ..backends.base import SandboxBackend, SandboxHandle
from ..controllers.base import SandboxController, SandboxControllerFactory
from ..mapping import scenario_spec_from_records, subject_spec_from_record
from ..models import EvaluationRunStatus
from ..persistence.store import EvalHarnessStore
from .progress import FsmProgressSink
from .user_proxy_fsm import UserProxyFSM
from .user_proxy_llm import UserProxyLLMClient, UserProxyLLMClientConfig

# Kept for closure detection (re-exported for UserProxyFSM internal use)
_PROXY_CLOSURE_RE = re.compile(
    r"\b("
    r"thanks|thank you|all good|looks good|working now|works now|fixed|resolved|that fixed it|problem solved|we're good"
    r")\b",
    re.IGNORECASE,
)


def _role_for_subject_message() -> str:
    return "assistant"


@dataclass(frozen=True)
class BenchmarkRunResult:
    benchmark_run_id: str
    evaluation_run_ids: tuple[str, ...]


class BenchmarkRunOrchestrator:
    def __init__(
        self,
        *,
        backend: SandboxBackend,
        controller_factory: SandboxControllerFactory,
        subject_adapters: dict[str, SubjectAdapter],
        store: EvalHarnessStore,
        user_proxy_llm: UserProxyLLMClient | None = None,
        progress: FsmProgressSink | None = None,
    ):
        self.backend = backend
        self.controller_factory = controller_factory
        self.subject_adapters = dict(subject_adapters)
        self.store = store
        self._user_proxy_llm = user_proxy_llm
        self.progress = progress

    def _get_user_proxy_llm(self) -> UserProxyLLMClient:
        """Return the injected client or build one from backend config."""
        if self._user_proxy_llm is not None:
            return self._user_proxy_llm
        # Try to get config from AWS backend
        cfg = getattr(getattr(self.backend, "config", None), "user_proxy_runtime", None)
        if cfg is not None:
            return UserProxyLLMClient(cfg)
        raise RuntimeError(
            "No user_proxy_llm client configured. "
            "Pass user_proxy_llm= to BenchmarkRunOrchestrator or set "
            "backend.config.user_proxy_runtime."
        )

    def _repair_result_payload(self, scenario, command_results) -> dict:
        return {
            "checks": [
                {
                    "check": check.to_dict(),
                    "result": result.to_dict(),
                    "passed": check.is_satisfied_by(result),
                }
                for check, result in zip(scenario.repair_checks, command_results, strict=True)
            ]
        }

    def _exception_reason(self, exc: BaseException) -> str:
        message = str(exc).strip()
        if message:
            return message
        return exc.__class__.__name__

    def _repair_checks_pass(self, scenario, command_results) -> bool:
        if len(command_results) != len(scenario.repair_checks):
            return False
        pairs = tuple(zip(scenario.repair_checks, command_results, strict=True))
        if not pairs:
            return False
        return all(check.is_satisfied_by(result) for check, result in pairs)

    def _record_command_results(self, *, evaluation_run_id: str, seq: int, actor_role: str, command_results: tuple) -> int:
        for result in command_results:
            self.store.append_evaluation_event(
                evaluation_run_id=evaluation_run_id,
                seq=seq,
                actor_role=actor_role,
                event_kind="command_result",
                payload=result.to_dict(),
            )
            seq += 1
        return seq

    def _execute_repair_checks(
        self,
        *,
        controller: SandboxController,
        scenario,
        evaluation_run_id: str,
        seq: int,
        verification_agent_id: str,
    ) -> tuple[tuple, int]:
        repair_results = controller.execute_commands(
            tuple(item.command for item in scenario.repair_checks),
            agent_id=verification_agent_id,
            session_key=f"{evaluation_run_id}-repair",
        )
        seq = self._record_command_results(
            evaluation_run_id=evaluation_run_id,
            seq=seq,
            actor_role="controller",
            command_results=repair_results,
        )
        return repair_results, seq

    def _benchmark_status_and_summary(self, benchmark_run_id: str, *, interrupted: bool = False) -> tuple[str, dict]:
        evaluation_runs = self.store.list_evaluation_runs(benchmark_run_id)
        status_counts = Counter(run.status for run in evaluation_runs)
        repair_success_count = sum(1 for run in evaluation_runs if run.repair_success is True)
        failed_evaluation_count = sum(1 for run in evaluation_runs if run.status != EvaluationRunStatus.COMPLETED.value)
        summary = {
            "evaluation_count": len(evaluation_runs),
            "repair_success_count": repair_success_count,
            "failed_evaluation_count": failed_evaluation_count,
            "evaluation_status_counts": dict(status_counts),
        }
        if interrupted:
            return "interrupted", summary
        if repair_success_count == len(evaluation_runs) and failed_evaluation_count == 0:
            return "completed", summary
        return "completed_with_failures", summary

    def _run_subject(
        self,
        *,
        benchmark_run_id: str,
        scenario,
        subject_row,
        clone_handle: SandboxHandle,
        evaluation_run_id: str,
        user_proxy_agent_id: str,
        verification_agent_id: str,
    ) -> None:
        adapter = self.subject_adapters.get(subject_row.adapter_type)
        if adapter is None:
            raise RuntimeError(f"No subject adapter registered for {subject_row.adapter_type}")
        controller: SandboxController | None = None
        session: SubjectSession | None = None
        seq = 1
        transcript_pairs: list[tuple[str, str]] = []
        try:
            self.backend.wait_until_ready(clone_handle)
            self.backend.configure_controller_runtime(clone_handle)
            controller = self.controller_factory.open(clone_handle, purpose=f"evaluation-{evaluation_run_id}")
            subject_spec = subject_spec_from_record(subject_row)
            session = adapter.create_session(benchmark_run_id, subject_spec)
            session.seed_context(scenario.context_seed)

            # Build the LLM client for the user proxy FSM
            llm_client = self._get_user_proxy_llm()

            user_message = scenario.observable_problem_statement
            if scenario.initial_diagnostic_commands:
                diag_results = controller.execute_commands(
                    scenario.initial_diagnostic_commands,
                    agent_id=verification_agent_id,
                    session_key=f"{evaluation_run_id}-initial-diag",
                )
                seq = self._record_command_results(
                    evaluation_run_id=evaluation_run_id,
                    seq=seq,
                    actor_role="controller",
                    command_results=diag_results,
                )
                diag_lines = ["\n\nHere's what I'm seeing on the machine:"]
                for result in diag_results:
                    diag_lines.append(f"\n\n$ {result.command}")
                    if result.stdout:
                        diag_lines.append(f"\n{result.stdout}")
                    if result.stderr:
                        diag_lines.append(f"\n{result.stderr}")
                user_message = user_message + "".join(diag_lines)

            scenario_turn_budget = scenario.turn_budget if scenario.turn_budget > 0 else subject_spec.max_turns
            subject_max = subject_spec.max_turns if subject_spec.max_turns > 0 else scenario_turn_budget
            effective_max_turns = min(scenario_turn_budget, subject_max)
            if effective_max_turns <= 0:
                effective_max_turns = 8  # absolute fallback

            consecutive_stalled_turns = 0
            _PROXY_STALL_LIMIT = 3
            turn_index = 0

            for _ in range(effective_max_turns):
                self.store.append_evaluation_event(
                    evaluation_run_id=evaluation_run_id,
                    seq=seq,
                    actor_role="user_proxy",
                    event_kind="message",
                    payload={"role": "user", "content": user_message},
                )
                transcript_pairs.append(("user", user_message))
                seq += 1

                turn_result = session.submit_user_message(user_message)
                self.store.append_evaluation_event(
                    evaluation_run_id=evaluation_run_id,
                    seq=seq,
                    actor_role="subject",
                    event_kind="message",
                    payload={
                        "role": _role_for_subject_message(),
                        "content": turn_result.assistant_message,
                        "run_id": turn_result.run_id,
                        "metadata": turn_result.metadata,
                    },
                )
                transcript_pairs.append(("assistant", turn_result.assistant_message))
                seq += 1

                for run_event in turn_result.events:
                    self.store.append_evaluation_event(
                        evaluation_run_id=evaluation_run_id,
                        seq=seq,
                        actor_role="adapter",
                        event_kind="run_event",
                        payload=run_event.to_dict(),
                    )
                    seq += 1

                repair_results, seq = self._execute_repair_checks(
                    controller=controller,
                    scenario=scenario,
                    evaluation_run_id=evaluation_run_id,
                    seq=seq,
                    verification_agent_id=verification_agent_id,
                )

                if self._repair_checks_pass(scenario, repair_results):
                    session_metadata = session.close()
                    session = None
                    self.store.update_evaluation_run_status(
                        evaluation_run_id=evaluation_run_id,
                        status=EvaluationRunStatus.COMPLETED.value,
                        repair_success=True,
                        resolution_result=self._repair_result_payload(scenario, repair_results),
                        adapter_session_metadata=session_metadata,
                        finished=True,
                    )
                    if self.progress is not None:
                        try:
                            self.progress(
                                fsm_name="benchmark",
                                scenario_name=getattr(scenario, "scenario_name", ""),
                                details={"event": "evaluation_completed", "repair_success": True},
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    return

                # Run the user-proxy FSM to generate the next user message
                proxy_fsm = UserProxyFSM(
                    llm_client=llm_client,
                    controller=controller,
                    evaluation_run_id=evaluation_run_id,
                    observable_problem_statement=scenario.observable_problem_statement,
                    scenario_name=getattr(scenario, "scenario_name", ""),
                    progress=self.progress,
                    turn=turn_index,
                )
                proxy_result = proxy_fsm.run_turn(
                    list(transcript_pairs),
                    turn_result.assistant_message,
                )

                # Record any commands the proxy ran
                if proxy_result.tool_results:
                    seq = self._record_command_results(
                        evaluation_run_id=evaluation_run_id,
                        seq=seq,
                        actor_role="user_proxy_exec",
                        command_results=proxy_result.tool_results,
                    )

                turn_index += 1

                if proxy_result.stalled:
                    consecutive_stalled_turns += 1
                    if consecutive_stalled_turns >= _PROXY_STALL_LIMIT:
                        session_metadata = session.close()
                        session = None
                        self.store.update_evaluation_run_status(
                            evaluation_run_id=evaluation_run_id,
                            status=EvaluationRunStatus.FAILED.value,
                            repair_success=False,
                            resolution_result={"reason": "proxy_stalled"},
                            adapter_session_metadata=session_metadata,
                            finished=True,
                        )
                        if self.progress is not None:
                            try:
                                self.progress(
                                    fsm_name="benchmark",
                                    scenario_name=getattr(scenario, "scenario_name", ""),
                                    details={"event": "evaluation_completed", "repair_success": False},
                                )
                            except Exception:  # noqa: BLE001
                                pass
                        return
                    # Use the problem statement as the fallback message and continue
                    user_message = scenario.observable_problem_statement
                    continue

                consecutive_stalled_turns = 0

                if proxy_result.closure:
                    # Proxy declared repair confirmed — verify with repair checks
                    repair_results, seq = self._execute_repair_checks(
                        controller=controller,
                        scenario=scenario,
                        evaluation_run_id=evaluation_run_id,
                        seq=seq,
                        verification_agent_id=verification_agent_id,
                    )
                    session_metadata = session.close()
                    session = None
                    repair_success = self._repair_checks_pass(scenario, repair_results)
                    self.store.update_evaluation_run_status(
                        evaluation_run_id=evaluation_run_id,
                        status=EvaluationRunStatus.COMPLETED.value if repair_success else EvaluationRunStatus.FAILED.value,
                        repair_success=repair_success,
                        resolution_result=self._repair_result_payload(scenario, repair_results),
                        adapter_session_metadata=session_metadata,
                        finished=True,
                    )
                    if self.progress is not None:
                        try:
                            self.progress(
                                fsm_name="benchmark",
                                scenario_name=getattr(scenario, "scenario_name", ""),
                                details={"event": "evaluation_completed", "repair_success": repair_success},
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    return

                user_message = proxy_result.user_message

            session_metadata = session.close()
            session = None
            self.store.update_evaluation_run_status(
                evaluation_run_id=evaluation_run_id,
                status=EvaluationRunStatus.FAILED.value,
                repair_success=False,
                resolution_result={"reason": "turn_budget_exhausted"},
                adapter_session_metadata=session_metadata,
                finished=True,
            )
        except BaseException as exc:
            if session is not None:
                try:
                    session_metadata = session.abort()
                except Exception:
                    session_metadata = {}
            else:
                session_metadata = {}
            self.store.update_evaluation_run_status(
                evaluation_run_id=evaluation_run_id,
                status=EvaluationRunStatus.FAILED.value,
                repair_success=False,
                resolution_result={"reason": self._exception_reason(exc), "exception_type": exc.__class__.__name__},
                adapter_session_metadata=session_metadata,
                finished=True,
            )
            raise
        finally:
            if controller is not None:
                controller.close()
            self.backend.destroy_handle(clone_handle)

    def run(
        self,
        *,
        scenario_revision_id: str,
        verified_setup_run_id: str,
        user_proxy_agent_id: str = "proxy",
        verification_agent_id: str = "verifier",
    ) -> BenchmarkRunResult:
        revision = self.store.get_scenario_revision(scenario_revision_id)
        if revision is None:
            raise ValueError(f"Unknown scenario revision {scenario_revision_id}")
        scenario_row = self.store.get_scenario(revision.scenario_id)
        if scenario_row is None:
            raise ValueError(f"Unknown scenario {revision.scenario_id}")
        scenario = scenario_spec_from_records(scenario_row, revision)
        setup_run = self.store.get_setup_run(verified_setup_run_id)
        if setup_run is None:
            raise ValueError(f"Unknown setup run {verified_setup_run_id}")
        if setup_run.status != "verified":
            raise ValueError(f"Setup run {verified_setup_run_id} is not verified")
        if not setup_run.broken_image_id:
            raise ValueError(f"Setup run {verified_setup_run_id} is missing broken_image_id")

        subject_rows = self.store.list_active_subjects()
        if not subject_rows:
            raise RuntimeError("No active benchmark subjects are registered")

        benchmark_run = self.store.create_benchmark_run(
            scenario_revision_id=scenario_revision_id,
            verified_setup_run_id=verified_setup_run_id,
            subject_ids=[item.id for item in subject_rows],
            metadata={"scenario_name": scenario_row.scenario_name},
        )
        try:
            clone_handles = self.backend.launch_subject_clones(
                benchmark_run.id,
                scenario_row.scenario_name,
                setup_run.broken_image_id,
                [item.subject_name for item in subject_rows],
            )
        except Exception as exc:
            self.store.update_benchmark_run_status(
                benchmark_run_id=benchmark_run.id,
                status="interrupted",
                finished=True,
                metadata={
                    "summary": {
                        "evaluation_count": 0,
                        "repair_success_count": 0,
                        "failed_evaluation_count": 0,
                        "evaluation_status_counts": {},
                    },
                    "interruption_reason": self._exception_reason(exc),
                    "exception_type": exc.__class__.__name__,
                },
            )
            raise
        evaluation_run_ids: list[str] = []
        futures = []
        executor = ThreadPoolExecutor(max_workers=max(1, len(subject_rows)))
        try:
            for subject_row in subject_rows:
                clone_handle = clone_handles[subject_row.subject_name]
                evaluation_run = self.store.create_evaluation_run(
                    benchmark_run_id=benchmark_run.id,
                    subject_id=subject_row.id,
                    clone_handle_id=clone_handle.remote_id,
                    subject_metadata={"display_name": subject_row.display_name},
                )
                evaluation_run_ids.append(evaluation_run.id)
                futures.append(
                    executor.submit(
                        self._run_subject,
                        benchmark_run_id=benchmark_run.id,
                        scenario=scenario,
                        subject_row=subject_row,
                        clone_handle=clone_handle,
                        evaluation_run_id=evaluation_run.id,
                        user_proxy_agent_id=user_proxy_agent_id,
                        verification_agent_id=verification_agent_id,
                    )
                )
            failure: BaseException | None = None
            for future in futures:
                try:
                    future.result()
                except BaseException as exc:  # pragma: no cover - exercised in tests through failure paths
                    failure = exc
                    break
            if failure is not None:
                for evaluation_run in self.store.list_evaluation_runs(benchmark_run.id):
                    if evaluation_run.finished_at is not None:
                        continue
                    self.store.update_evaluation_run_status(
                        evaluation_run_id=evaluation_run.id,
                        status=EvaluationRunStatus.FAILED.value,
                        repair_success=False,
                        resolution_result={
                            "reason": self._exception_reason(failure),
                            "exception_type": failure.__class__.__name__,
                            "benchmark_interrupted": True,
                        },
                        finished=True,
                    )
                benchmark_status, summary = self._benchmark_status_and_summary(benchmark_run.id, interrupted=True)
                self.store.update_benchmark_run_status(
                    benchmark_run_id=benchmark_run.id,
                    status=benchmark_status,
                    finished=True,
                    metadata={
                        "summary": summary,
                        "interruption_reason": self._exception_reason(failure),
                        "exception_type": failure.__class__.__name__,
                    },
                )
                raise failure
            benchmark_status, summary = self._benchmark_status_and_summary(benchmark_run.id)
            self.store.update_benchmark_run_status(
                benchmark_run_id=benchmark_run.id,
                status=benchmark_status,
                finished=True,
                metadata={"summary": summary},
            )
            return BenchmarkRunResult(benchmark_run_id=benchmark_run.id, evaluation_run_ids=tuple(evaluation_run_ids))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
