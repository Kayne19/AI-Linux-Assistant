from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..adapters.base import SubjectAdapter, SubjectSession
from ..backends.base import SandboxBackend, SandboxHandle
from ..controllers.base import SandboxController, SandboxControllerFactory
from ..mapping import scenario_spec_from_records, subject_spec_from_record
from ..models import EvaluationRunStatus
from ..persistence.store import EvalHarnessStore


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

    def _build_proxy_message(self, scenario_problem: str, transcript: tuple[tuple[str, str], ...], subject_reply: str) -> str:
        rendered_transcript = "\n".join(f"{role}: {content}" for role, content in transcript)
        return (
            "You are the benchmark user proxy. You do not know how the environment was sabotaged. "
            "You only know what is observably broken and any observations you can gather directly. "
            "Respond as the user in the next turn. Do not reveal hidden causes.\n\n"
            f"Observable problem:\n{scenario_problem}\n\n"
            f"Conversation so far:\n{rendered_transcript}\n\n"
            f"Latest subject response:\n{subject_reply}"
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
            controller = self.controller_factory.open(clone_handle, purpose=f"evaluation-{evaluation_run_id}")
            subject_spec = subject_spec_from_record(subject_row)
            session = adapter.create_session(benchmark_run_id, subject_spec)
            session.seed_context(scenario.context_seed)

            user_message = scenario.observable_problem_statement
            for _ in range(subject_spec.max_turns):
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

                if all(check.is_satisfied_by(result) for check, result in zip(scenario.repair_checks, repair_results, strict=True)):
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
                        scenario.observable_problem_statement,
                        tuple(transcript_pairs),
                        turn_result.assistant_message,
                    ),
                    session_key=f"{evaluation_run_id}-proxy",
                )

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
        except Exception as exc:
            if session is not None:
                try:
                    session_metadata = session.close()
                except Exception:
                    session_metadata = {}
            else:
                session_metadata = {}
            self.store.update_evaluation_run_status(
                evaluation_run_id=evaluation_run_id,
                status=EvaluationRunStatus.FAILED.value,
                repair_success=False,
                resolution_result={"reason": str(exc)},
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
        clone_handles = self.backend.launch_subject_clones(
            benchmark_run.id,
            scenario_row.scenario_name,
            setup_run.broken_image_id,
            [item.subject_name for item in subject_rows],
        )
        evaluation_run_ids: list[str] = []
        futures = []
        with ThreadPoolExecutor(max_workers=max(1, len(subject_rows))) as executor:
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
            failure: Exception | None = None
            for future in futures:
                try:
                    future.result()
                except Exception as exc:  # pragma: no cover - exercised in tests through failure paths
                    failure = exc
        if failure is not None:
            self.store.update_benchmark_run_status(benchmark_run_id=benchmark_run.id, status="failed", finished=True)
            raise failure
        self.store.update_benchmark_run_status(benchmark_run_id=benchmark_run.id, status="completed", finished=True)
        return BenchmarkRunResult(benchmark_run_id=benchmark_run.id, evaluation_run_ids=tuple(evaluation_run_ids))
