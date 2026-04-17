from __future__ import annotations

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
from .user_proxy_fsm import USER_PROXY_MODES, UserProxyFSM
from .user_proxy_llm import UserProxyLLMClient, UserProxyLLMClientConfig

_STATE_CHANGING_PROXY_TOOLS = frozenset({"run_command", "apply_text_edit", "interactive_send"})
_SOFT_CLOSURE_PHRASES = (
    "thanks",
    "thank you",
    "looks good",
    "all good",
    "all set",
    "working now",
    "works now",
    "seems fixed",
    "it started",
    "it works",
    "it's back",
    "its back",
    "that fixed it",
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
        user_proxy_mode: str = "strict_relay",
        progress: FsmProgressSink | None = None,
    ):
        self.backend = backend
        self.controller_factory = controller_factory
        self.subject_adapters = dict(subject_adapters)
        self.store = store
        self._user_proxy_llm = user_proxy_llm
        normalized_mode = str(user_proxy_mode or "strict_relay").strip().lower() or "strict_relay"
        if normalized_mode not in USER_PROXY_MODES:
            raise ValueError(f"Unsupported user proxy mode {user_proxy_mode!r}")
        self.user_proxy_mode = normalized_mode
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
        snapshot = self._repair_snapshot(scenario, command_results)
        return {
            "checks": list(snapshot["last_repair_check_results"]),
            **snapshot,
        }

    def _repair_snapshot(self, scenario, command_results) -> dict:
        checks: list[dict[str, Any]] = []
        failed_check_names: list[str] = []
        for check, result in zip(scenario.repair_checks, command_results, strict=True):
            passed = check.is_satisfied_by(result)
            checks.append(
                {
                    "check": check.to_dict(),
                    "result": result.to_dict(),
                    "passed": passed,
                }
            )
            if not passed:
                failed_check_names.append(check.name or result.command)
        return {
            "last_repair_check_results": checks,
            "passed_check_count": len(checks) - len(failed_check_names),
            "failed_check_names": failed_check_names,
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

    def _subject_message_summary(self, message: str) -> str:
        compact = " ".join(str(message or "").split())
        if len(compact) <= 200:
            return compact
        return f"{compact[:197]}..."

    def _failure_resolution_payload(
        self,
        *,
        reason: str,
        last_repair_snapshot: dict | None,
        last_subject_message_summary: str,
        extra: dict[str, Any] | None = None,
    ) -> dict:
        payload = {
            "reason": reason,
            "last_repair_check_results": list((last_repair_snapshot or {}).get("last_repair_check_results", [])),
            "passed_check_count": int((last_repair_snapshot or {}).get("passed_check_count", 0)),
            "failed_check_names": list((last_repair_snapshot or {}).get("failed_check_names", [])),
            "last_subject_message_summary": last_subject_message_summary,
        }
        if extra:
            payload.update(extra)
        return payload

    def _soft_closure_detected(self, message: str) -> bool:
        lowered = str(message or "").strip().lower()
        if not lowered:
            return False
        return any(phrase in lowered for phrase in _SOFT_CLOSURE_PHRASES)

    def _complete_success(
        self,
        *,
        evaluation_run_id: str,
        session: SubjectSession | None = None,
        session_metadata: dict | None,
        scenario,
        repair_results: tuple,
    ) -> None:
        if session_metadata is not None:
            resolved_session_metadata = session_metadata
        elif session is not None:
            resolved_session_metadata = session.close()
        else:
            resolved_session_metadata = {}
        self.store.update_evaluation_run_status(
            evaluation_run_id=evaluation_run_id,
            status=EvaluationRunStatus.COMPLETED.value,
            repair_success=True,
            resolution_result=self._repair_result_payload(scenario, repair_results),
            adapter_session_metadata=resolved_session_metadata,
            finished=True,
        )

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
        last_repair_snapshot: dict | None = None
        last_subject_message_summary = ""
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

            scenario_turn_budget = scenario.turn_budget if scenario.turn_budget > 0 else 0
            subject_max = subject_spec.max_turns if subject_spec.max_turns is not None and subject_spec.max_turns > 0 else 0
            if scenario_turn_budget > 0 and subject_max > 0:
                effective_max_turns = min(scenario_turn_budget, subject_max)
            elif scenario_turn_budget > 0:
                effective_max_turns = scenario_turn_budget
            elif subject_max > 0:
                effective_max_turns = subject_max
            else:
                effective_max_turns = 8  # absolute fallback

            consecutive_stalled_turns = 0
            _PROXY_STALL_LIMIT = 3
            turn_index = 0

            _scenario_name = getattr(scenario, "scenario_name", "")
            _subject_name = subject_row.subject_name

            def _emit(event: str, extra: dict | None = None) -> None:
                if self.progress is None:
                    return
                try:
                    details: dict = {"event": event, "subject_name": _subject_name}
                    if extra:
                        details.update(extra)
                    self.progress(
                        fsm_name="benchmark",
                        scenario_name=_scenario_name,
                        details=details,
                    )
                except Exception:  # noqa: BLE001
                    pass

            for turn_index_outer in range(effective_max_turns):
                _emit("user_turn_start", {"turn": turn_index_outer, "msg_len": len(user_message)})
                self.store.append_evaluation_event(
                    evaluation_run_id=evaluation_run_id,
                    seq=seq,
                    actor_role="user_proxy",
                    event_kind="message",
                    payload={"role": "user", "content": user_message},
                )
                transcript_pairs.append(("user", user_message))
                seq += 1

                _emit("subject_wait", {"turn": turn_index_outer})
                turn_result = session.submit_user_message(user_message)
                _reply_snippet = (turn_result.assistant_message or "")[:120].replace("\n", " ")
                _emit("subject_replied", {"turn": turn_index_outer, "reply_snippet": _reply_snippet})
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
                last_subject_message_summary = self._subject_message_summary(turn_result.assistant_message)
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

                _emit("repair_check_start", {"turn": turn_index_outer, "check_count": len(scenario.repair_checks)})
                repair_results, seq = self._execute_repair_checks(
                    controller=controller,
                    scenario=scenario,
                    evaluation_run_id=evaluation_run_id,
                    seq=seq,
                    verification_agent_id=verification_agent_id,
                )
                _passed = self._repair_checks_pass(scenario, repair_results)
                last_repair_snapshot = self._repair_snapshot(scenario, repair_results)
                _emit("repair_check_done", {"turn": turn_index_outer, "passed": _passed})

                if _passed:
                    session_metadata = session.close()
                    session = None
                    self._complete_success(
                        evaluation_run_id=evaluation_run_id,
                        session_metadata=session_metadata,
                        scenario=scenario,
                        repair_results=repair_results,
                    )
                    _emit("evaluation_completed", {"repair_success": True})
                    return

                # Run the user-proxy FSM to generate the next user message
                proxy_fsm = UserProxyFSM(
                    llm_client=llm_client,
                    controller=controller,
                    evaluation_run_id=evaluation_run_id,
                    observable_problem_statement=scenario.observable_problem_statement,
                    scenario_name=_scenario_name,
                    subject_name=_subject_name,
                    user_proxy_mode=self.user_proxy_mode,
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
                    ran_state_changing_tool = any(
                        result.metadata.get("user_proxy_tool_name") in _STATE_CHANGING_PROXY_TOOLS
                        for result in proxy_result.tool_results
                    )
                    if ran_state_changing_tool:
                        repair_results, seq = self._execute_repair_checks(
                            controller=controller,
                            scenario=scenario,
                            evaluation_run_id=evaluation_run_id,
                            seq=seq,
                            verification_agent_id=verification_agent_id,
                        )
                        last_repair_snapshot = self._repair_snapshot(scenario, repair_results)
                        if self._repair_checks_pass(scenario, repair_results):
                            session_metadata = session.close()
                            self._complete_success(
                                evaluation_run_id=evaluation_run_id,
                                session=session,
                                session_metadata=session_metadata,
                                scenario=scenario,
                                repair_results=repair_results,
                            )
                            session = None
                            _emit("evaluation_completed", {"repair_success": True, "source": "post_proxy_action"})
                            return

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
                            resolution_result=self._failure_resolution_payload(
                                reason="proxy_stalled",
                                last_repair_snapshot=last_repair_snapshot,
                                last_subject_message_summary=last_subject_message_summary,
                            ),
                            adapter_session_metadata=session_metadata,
                            finished=True,
                        )
                        _emit("evaluation_completed", {"repair_success": False, "reason": "proxy_stalled"})
                        return
                    # Use the problem statement as the fallback message and continue
                    user_message = scenario.observable_problem_statement
                    continue

                consecutive_stalled_turns = 0

                if self._soft_closure_detected(proxy_result.user_message):
                    repair_results, seq = self._execute_repair_checks(
                        controller=controller,
                        scenario=scenario,
                        evaluation_run_id=evaluation_run_id,
                        seq=seq,
                        verification_agent_id=verification_agent_id,
                    )
                    last_repair_snapshot = self._repair_snapshot(scenario, repair_results)
                    if self._repair_checks_pass(scenario, repair_results):
                        session_metadata = session.close()
                        self._complete_success(
                            evaluation_run_id=evaluation_run_id,
                            session=session,
                            session_metadata=session_metadata,
                            scenario=scenario,
                            repair_results=repair_results,
                        )
                        session = None
                        _emit("evaluation_completed", {"repair_success": True, "source": "soft_closure"})
                        return

                user_message = proxy_result.user_message

            session_metadata = session.close()
            session = None
            self.store.update_evaluation_run_status(
                evaluation_run_id=evaluation_run_id,
                status=EvaluationRunStatus.FAILED.value,
                repair_success=False,
                resolution_result=self._failure_resolution_payload(
                    reason="turn_budget_exhausted",
                    last_repair_snapshot=last_repair_snapshot,
                    last_subject_message_summary=last_subject_message_summary,
                ),
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
                resolution_result=self._failure_resolution_payload(
                    reason=self._exception_reason(exc),
                    last_repair_snapshot=last_repair_snapshot,
                    last_subject_message_summary=last_subject_message_summary,
                    extra={"exception_type": exc.__class__.__name__},
                ),
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
