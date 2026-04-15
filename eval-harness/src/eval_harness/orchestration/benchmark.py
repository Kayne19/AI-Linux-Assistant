from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..adapters.base import SubjectAdapter, SubjectSession
from ..backends.base import SandboxBackend, SandboxHandle
from ..controllers.base import SandboxController, SandboxControllerFactory
from ..mapping import scenario_spec_from_records, subject_spec_from_record
from ..models import EvaluationRunStatus
from ..persistence.store import EvalHarnessStore

_PROXY_APPROVAL_RE = re.compile(r"/approve\s+[a-f0-9]+", re.IGNORECASE)
_HOST_RUN_RE = re.compile(r"```host-run\s*\n(.*?)```", re.DOTALL)


def _parse_host_run_commands(reply: str) -> tuple[str, ...]:
    commands = []
    for match in _HOST_RUN_RE.finditer(reply):
        cmd = match.group(1).strip()
        if cmd:
            commands.append(cmd)
        if len(commands) >= 3:
            break
    return tuple(commands)


def _render_command_outputs(command_results: tuple) -> str:
    parts = []
    for result in command_results:
        stdout = (result.stdout or "").strip()[:2000]
        stderr = (result.stderr or "").strip()[:2000]
        exit_code = result.exit_code
        parts.append(
            f"$ {result.command}\n"
            + (f"{stdout}\n" if stdout else "")
            + (f"{stderr}\n" if stderr else "")
            + f"[exit {exit_code}]"
        )
    return "\n\n".join(parts)


def _user_proxy_system_prompt(observable_problem_statement: str) -> str:
    return (
        "You are a frustrated human user at a Linux terminal. You do not know why your machine is broken or what caused it.\n\n"
        f"Your situation: {observable_problem_statement}\n\n"
        "Rules:\n"
        "- Stay in character as a non-expert user who has shell access but limited Linux knowledge.\n"
        "- When the assistant asks you to run a command, request execution using a host-run fenced block:\n\n"
        "```host-run\n"
        "<command here>\n"
        "```\n\n"
        "  The harness will run it and give you the real output on the next turn.\n"
        "- Only request commands the assistant explicitly asked you to run. Do not invent diagnostics.\n"
        "- Never fabricate command output. Never say \"Fixed\" or \"Done\" unless you have seen actual output confirming repair.\n"
        "- Do not send /approve tokens, slash commands, or tool-routing syntax.\n"
        "- Do not write like an AI assistant. Write like a confused user.\n"
        "- When you have observed output that confirms the problem stated above is resolved, reply with exactly: REPAIR_CONFIRMED"
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
    ):
        self.backend = backend
        self.controller_factory = controller_factory
        self.subject_adapters = dict(subject_adapters)
        self.store = store

    def _build_proxy_message(self, transcript: tuple[tuple[str, str], ...], subject_reply: str) -> str:
        rendered = "\n".join(f"{role}: {content}" for role, content in transcript)
        return (
            f"Conversation so far:\n{rendered}\n\n"
            f"Assistant just said:\n{subject_reply}"
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

    def _proxy_reply_has_approval_leak(self, reply: str) -> bool:
        return bool(_PROXY_APPROVAL_RE.search(reply))

    def _repair_checks_pass(self, scenario, command_results) -> bool:
        pairs = tuple(zip(scenario.repair_checks, command_results, strict=True))
        if not pairs:
            return False
        return all(check.is_satisfied_by(result) for check, result in pairs)

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

            user_message = scenario.observable_problem_statement
            scenario_turn_budget = scenario.turn_budget if scenario.turn_budget > 0 else subject_spec.max_turns
            subject_max = subject_spec.max_turns if subject_spec.max_turns > 0 else scenario_turn_budget
            effective_max_turns = min(scenario_turn_budget, subject_max)
            if effective_max_turns <= 0:
                effective_max_turns = 8  # absolute fallback
            proxy_exec_calls_remaining = effective_max_turns * 3
            consecutive_stalled_turns = 0
            _PROXY_STALL_LIMIT = 3
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

                repair_results = controller.execute_commands(
                    tuple(item.command for item in scenario.repair_checks),
                    agent_id=verification_agent_id,
                    session_key=f"{evaluation_run_id}-repair",
                )
                for result in repair_results:
                    self.store.append_evaluation_event(
                        evaluation_run_id=evaluation_run_id,
                        seq=seq,
                        actor_role="controller",
                        event_kind="command_result",
                        payload=result.to_dict(),
                    )
                    seq += 1

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
                    return

                user_message = controller.send(
                    agent_id=user_proxy_agent_id,
                    message=self._build_proxy_message(
                        tuple(transcript_pairs),
                        turn_result.assistant_message,
                    ),
                    session_key=f"{evaluation_run_id}-proxy",
                    system_prompt=_user_proxy_system_prompt(scenario.observable_problem_statement),
                )

                if self._proxy_reply_has_approval_leak(user_message):
                    reminder = (
                        "Do not send /approve commands or slash commands. "
                        "You are a human user. If you need to run a command, use a host-run block."
                    )
                    user_message = controller.send(
                        agent_id=user_proxy_agent_id,
                        message=reminder,
                        session_key=f"{evaluation_run_id}-proxy",
                        system_prompt=_user_proxy_system_prompt(scenario.observable_problem_statement),
                    )
                    if self._proxy_reply_has_approval_leak(user_message):
                        self.store.append_evaluation_event(
                            evaluation_run_id=evaluation_run_id,
                            seq=seq,
                            actor_role="user_proxy",
                            event_kind="skipped_turn",
                            payload={"reason": "proxy_approval_leak_suppressed"},
                        )
                        seq += 1
                        continue

                if user_message.strip() == "REPAIR_CONFIRMED":
                    repair_results = controller.execute_commands(
                        tuple(item.command for item in scenario.repair_checks),
                        agent_id=verification_agent_id,
                        session_key=f"{evaluation_run_id}-repair",
                    )
                    for result in repair_results:
                        self.store.append_evaluation_event(
                            evaluation_run_id=evaluation_run_id,
                            seq=seq,
                            actor_role="controller",
                            event_kind="command_result",
                            payload=result.to_dict(),
                        )
                        seq += 1
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
                    return

                # Execute any HOST_RUN commands the proxy requested
                host_run_commands = _parse_host_run_commands(user_message)
                if host_run_commands and proxy_exec_calls_remaining > 0:
                    n = min(len(host_run_commands), proxy_exec_calls_remaining)
                    commands_to_run = host_run_commands[:n]
                    proxy_exec_calls_remaining -= n
                    proxy_exec_results = controller.execute_commands(
                        commands_to_run,
                        agent_id="proxy-exec",
                        session_key=f"{evaluation_run_id}-proxy-exec",
                    )
                    for exec_result in proxy_exec_results:
                        self.store.append_evaluation_event(
                            evaluation_run_id=evaluation_run_id,
                            seq=seq,
                            actor_role="user_proxy_exec",
                            event_kind="command_result",
                            payload=exec_result.to_dict(),
                        )
                        seq += 1
                    # Append real output to the user message that goes to the subject
                    user_message = user_message + "\n\n" + _render_command_outputs(proxy_exec_results)

                # Stall detection: no productive output and repair still failing
                turn_had_exec = bool(host_run_commands)  # True if any host-run commands were parsed
                if not turn_had_exec and not self._repair_checks_pass(scenario, repair_results):
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
                        return
                else:
                    consecutive_stalled_turns = 0

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
        except Exception:
            self.store.update_benchmark_run_status(benchmark_run_id=benchmark_run.id, status="failed", finished=True)
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
                self.store.update_benchmark_run_status(benchmark_run_id=benchmark_run.id, status="failed", finished=True)
                raise failure
            self.store.update_benchmark_run_status(benchmark_run_id=benchmark_run.id, status="completed", finished=True)
            return BenchmarkRunResult(benchmark_run_id=benchmark_run.id, evaluation_run_ids=tuple(evaluation_run_ids))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
