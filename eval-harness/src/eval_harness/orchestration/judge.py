from __future__ import annotations

from dataclasses import dataclass

from ..judges.base import BlindJudge
from ..mapping import turn_record_from_evaluation_event
from ..models import BlindJudgeRequest, JudgeJobStatus
from ..persistence.store import EvalHarnessStore


@dataclass(frozen=True)
class JudgeJobResult:
    judge_job_id: str
    judge_item_ids: tuple[str, ...]


class JudgeJobOrchestrator:
    def __init__(self, *, judge: BlindJudge, store: EvalHarnessStore):
        self.judge = judge
        self.store = store

    def _build_transcript(self, evaluation_run_id: str):
        turns = []
        for event in self.store.list_evaluation_events(evaluation_run_id):
            turn = turn_record_from_evaluation_event(event)
            if turn is not None:
                turns.append(
                    turn.__class__(
                        role=turn.role,
                        content=turn.content,
                        created_at=turn.created_at,
                        metadata={},
                    )
                )
        return tuple(turns)

    def run(self, *, benchmark_run_id: str, rubric: tuple[str, ...] | None = None) -> JudgeJobResult:
        benchmark_run = self.store.get_benchmark_run(benchmark_run_id)
        if benchmark_run is None:
            raise ValueError(f"Unknown benchmark run {benchmark_run_id}")
        revision = self.store.get_scenario_revision(benchmark_run.scenario_revision_id)
        if revision is None:
            raise ValueError(f"Unknown scenario revision {benchmark_run.scenario_revision_id}")
        rubric_items = tuple(rubric or tuple(str(item) for item in (revision.judge_rubric_json or {}).get("items", [])))
        judge_job = self.store.create_judge_job(
            benchmark_run_id=benchmark_run_id,
            judge_adapter_type=self.judge.name,
            rubric={"items": list(rubric_items)},
        )
        self.store.update_judge_job_status(judge_job_id=judge_job.id, status=JudgeJobStatus.RUNNING.value, started=True)
        item_ids: list[str] = []
        try:
            for index, evaluation_run in enumerate(self.store.list_evaluation_runs(benchmark_run_id), start=1):
                blind_label = f"candidate-{index}"
                transcript = self._build_transcript(evaluation_run.id)
                request = BlindJudgeRequest(blind_label=blind_label, transcript=transcript, rubric=rubric_items)
                result = self.judge.grade(request)
                item = self.store.create_judge_item(
                    judge_job_id=judge_job.id,
                    evaluation_run_id=evaluation_run.id,
                    blind_label=blind_label,
                    blinded_transcript=request.to_dict(),
                    raw_judge_response=result.raw_response,
                    parsed_scores=result.scores,
                    summary=result.summary,
                )
                item_ids.append(item.id)
            self.store.update_judge_job_status(
                judge_job_id=judge_job.id,
                status=JudgeJobStatus.COMPLETED.value,
                finished=True,
            )
            return JudgeJobResult(judge_job_id=judge_job.id, judge_item_ids=tuple(item_ids))
        except Exception:
            self.store.update_judge_job_status(
                judge_job_id=judge_job.id,
                status=JudgeJobStatus.FAILED.value,
                finished=True,
            )
            raise
