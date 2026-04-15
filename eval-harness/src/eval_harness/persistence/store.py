from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .postgres_models import (
    BenchmarkRunRecord,
    BenchmarkSubjectRecord,
    EvaluationEventRecord,
    EvaluationRunRecord,
    JudgeItemRecord,
    JudgeJobRecord,
    ScenarioRecord,
    ScenarioRevisionRecord,
    ScenarioSetupEventRecord,
    ScenarioSetupRunRecord,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_scenario_name(value: str) -> str:
    compact = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    compact = compact.strip("-")
    compact = re.sub(r"-{2,}", "-", compact)
    if not compact:
        compact = "scenario"
    return compact[:120]


class EvalHarnessStore:
    """Store helper for eval-harness benchmark persistence tables."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _allocate_scenario_name(self, session: Session, preferred: str) -> str:
        base = normalize_scenario_name(preferred)
        candidate = base
        index = 1
        while session.scalar(select(ScenarioRecord.id).where(ScenarioRecord.scenario_name == candidate)):
            index += 1
            suffix = f"-{index}"
            candidate = f"{base[: 120 - len(suffix)]}{suffix}"
        return candidate

    def create_scenario(
        self,
        *,
        title: str,
        scenario_name_hint: str,
        lifecycle_status: str = "draft",
        verification_status: str = "unverified",
    ) -> ScenarioRecord:
        with self._session_factory() as session:
            name = self._allocate_scenario_name(session, scenario_name_hint)
            row = ScenarioRecord(
                scenario_name=name,
                title=title.strip(),
                lifecycle_status=lifecycle_status,
                verification_status=verification_status,
            )
            session.add(row)
            session.commit()
            return row

    def get_scenario(self, scenario_id: str) -> ScenarioRecord | None:
        with self._session_factory() as session:
            return session.get(ScenarioRecord, scenario_id)

    def get_scenario_by_name(self, scenario_name: str) -> ScenarioRecord | None:
        with self._session_factory() as session:
            return session.scalar(select(ScenarioRecord).where(ScenarioRecord.scenario_name == scenario_name.strip()))

    def create_scenario_revision(
        self,
        *,
        scenario_id: str,
        target_image: str,
        summary: str,
        what_it_tests: dict,
        observable_problem_statement: str,
        sabotage_plan: dict,
        verification_plan: dict,
        judge_rubric: dict,
        planner_metadata: dict | None = None,
    ) -> ScenarioRevisionRecord:
        with self._session_factory() as session:
            next_revision = (
                session.scalar(
                    select(func.coalesce(func.max(ScenarioRevisionRecord.revision_number), 0)).where(
                        ScenarioRevisionRecord.scenario_id == scenario_id
                    )
                )
                or 0
            ) + 1
            row = ScenarioRevisionRecord(
                scenario_id=scenario_id,
                revision_number=next_revision,
                target_image=target_image.strip(),
                summary=summary,
                what_it_tests_json=dict(what_it_tests),
                observable_problem_statement=observable_problem_statement,
                sabotage_plan_json=dict(sabotage_plan),
                verification_plan_json=dict(verification_plan),
                judge_rubric_json=dict(judge_rubric),
                planner_metadata_json=dict(planner_metadata or {}),
            )
            session.add(row)
            session.commit()
            return row

    def get_scenario_revision(self, revision_id: str) -> ScenarioRevisionRecord | None:
        with self._session_factory() as session:
            return session.get(ScenarioRevisionRecord, revision_id)

    def get_current_verified_revision(self, scenario_id: str) -> ScenarioRevisionRecord | None:
        with self._session_factory() as session:
            scenario = session.get(ScenarioRecord, scenario_id)
            if scenario is None or not scenario.current_verified_revision_id:
                return None
            return session.get(ScenarioRevisionRecord, scenario.current_verified_revision_id)

    def mark_scenario_verified(
        self, *, scenario_id: str, revision_id: str, lifecycle_status: str = "verified", verification_status: str = "verified"
    ) -> None:
        with self._session_factory() as session:
            scenario = session.get(ScenarioRecord, scenario_id)
            if scenario is None:
                raise ValueError(f"Unknown scenario_id: {scenario_id}")
            revision = session.get(ScenarioRevisionRecord, revision_id)
            if revision is None:
                raise ValueError(f"Unknown revision_id: {revision_id}")
            if revision.scenario_id != scenario_id:
                raise ValueError(
                    f"revision_id {revision_id} does not belong to scenario_id {scenario_id}"
                )
            scenario.current_verified_revision_id = revision_id
            scenario.lifecycle_status = lifecycle_status
            scenario.verification_status = verification_status
            scenario.last_verified_at = _utc_now()
            session.add(scenario)
            session.commit()

    def update_scenario_status(
        self,
        *,
        scenario_id: str,
        lifecycle_status: str | None = None,
        verification_status: str | None = None,
    ) -> None:
        with self._session_factory() as session:
            scenario = session.get(ScenarioRecord, scenario_id)
            if scenario is None:
                raise ValueError(f"Unknown scenario_id: {scenario_id}")
            if lifecycle_status is not None:
                scenario.lifecycle_status = lifecycle_status
            if verification_status is not None:
                scenario.verification_status = verification_status
            session.add(scenario)
            session.commit()

    def update_scenario_revision_observable_problem_statement(
        self,
        *,
        revision_id: str,
        observable_problem_statement: str,
    ) -> None:
        with self._session_factory() as session:
            row = session.get(ScenarioRevisionRecord, revision_id)
            if row is None:
                raise ValueError(f"Unknown revision_id: {revision_id}")
            row.observable_problem_statement = observable_problem_statement
            session.add(row)
            session.commit()

    def create_setup_run(
        self,
        *,
        scenario_revision_id: str,
        status: str = "running",
        staging_handle_id: str | None = None,
        max_corrections: int = 2,
        backend_metadata: dict | None = None,
    ) -> ScenarioSetupRunRecord:
        with self._session_factory() as session:
            row = ScenarioSetupRunRecord(
                scenario_revision_id=scenario_revision_id,
                status=status,
                staging_handle_id=staging_handle_id,
                max_corrections=max_corrections,
                backend_metadata_json=dict(backend_metadata or {}),
            )
            session.add(row)
            session.commit()
            return row

    def get_setup_run(self, setup_run_id: str) -> ScenarioSetupRunRecord | None:
        with self._session_factory() as session:
            return session.get(ScenarioSetupRunRecord, setup_run_id)

    def update_setup_run_status(
        self,
        *,
        setup_run_id: str,
        status: str,
        staging_handle_id: str | None = None,
        correction_count: int | None = None,
        broken_image_id: str | None = None,
        failure_reason: str | None = None,
        planner_approved: bool = False,
        backend_metadata: dict | None = None,
    ) -> None:
        with self._session_factory() as session:
            row = session.get(ScenarioSetupRunRecord, setup_run_id)
            if row is None:
                raise ValueError(f"Unknown setup_run_id: {setup_run_id}")
            row.status = status
            if staging_handle_id is not None:
                row.staging_handle_id = staging_handle_id
            if correction_count is not None:
                row.correction_count = correction_count
            if broken_image_id is not None:
                row.broken_image_id = broken_image_id
            if failure_reason is not None:
                row.failure_reason = failure_reason
            if planner_approved:
                row.planner_approved_at = _utc_now()
            if backend_metadata is not None:
                merged = dict(row.backend_metadata_json or {})
                merged.update(dict(backend_metadata))
                row.backend_metadata_json = merged
            session.add(row)
            session.commit()

    def append_setup_event(
        self,
        *,
        setup_run_id: str,
        round_index: int,
        seq: int,
        actor_role: str,
        event_kind: str,
        payload: dict | None = None,
    ) -> ScenarioSetupEventRecord:
        with self._session_factory() as session:
            row = ScenarioSetupEventRecord(
                setup_run_id=setup_run_id,
                round_index=round_index,
                seq=seq,
                actor_role=actor_role,
                event_kind=event_kind,
                payload_json=dict(payload or {}),
            )
            session.add(row)
            session.commit()
            return row

    def list_setup_events(self, setup_run_id: str) -> list[ScenarioSetupEventRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ScenarioSetupEventRecord)
                .where(ScenarioSetupEventRecord.setup_run_id == setup_run_id)
                .order_by(ScenarioSetupEventRecord.round_index.asc(), ScenarioSetupEventRecord.seq.asc())
            )
            return list(rows)

    def upsert_subject(
        self,
        *,
        subject_name: str,
        adapter_type: str,
        display_name: str,
        adapter_config: dict | None = None,
        is_active: bool = True,
    ) -> BenchmarkSubjectRecord:
        normalized_subject_name = subject_name.strip()
        with self._session_factory() as session:
            row = session.scalar(
                select(BenchmarkSubjectRecord).where(BenchmarkSubjectRecord.subject_name == normalized_subject_name)
            )
            if row is None:
                row = BenchmarkSubjectRecord(
                    subject_name=normalized_subject_name,
                    adapter_type=adapter_type.strip(),
                    display_name=display_name.strip(),
                    adapter_config_json=dict(adapter_config or {}),
                    is_active=is_active,
                )
            else:
                row.adapter_type = adapter_type.strip()
                row.display_name = display_name.strip()
                row.adapter_config_json = dict(adapter_config or {})
                row.is_active = is_active
            session.add(row)
            session.commit()
            return row

    def list_active_subjects(self) -> list[BenchmarkSubjectRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(BenchmarkSubjectRecord).where(BenchmarkSubjectRecord.is_active.is_(True)).order_by(
                    BenchmarkSubjectRecord.subject_name.asc()
                )
            )
            return list(rows)

    def get_subject(self, subject_id: str) -> BenchmarkSubjectRecord | None:
        with self._session_factory() as session:
            return session.get(BenchmarkSubjectRecord, subject_id)

    def get_subject_by_name(self, subject_name: str) -> BenchmarkSubjectRecord | None:
        with self._session_factory() as session:
            return session.scalar(select(BenchmarkSubjectRecord).where(BenchmarkSubjectRecord.subject_name == subject_name.strip()))

    def create_benchmark_run(
        self,
        *,
        scenario_revision_id: str,
        verified_setup_run_id: str,
        subject_ids: Iterable[str],
        metadata: dict | None = None,
        status: str = "running",
    ) -> BenchmarkRunRecord:
        subject_id_list = list(subject_ids)
        with self._session_factory() as session:
            row = BenchmarkRunRecord(
                scenario_revision_id=scenario_revision_id,
                verified_setup_run_id=verified_setup_run_id,
                status=status,
                subject_count=len(subject_id_list),
                started_at=_utc_now(),
                metadata_json=dict(metadata or {}),
            )
            session.add(row)

            scenario_id = session.scalar(
                select(ScenarioRevisionRecord.scenario_id).where(ScenarioRevisionRecord.id == scenario_revision_id)
            )
            if scenario_id:
                scenario = session.get(ScenarioRecord, scenario_id)
                if scenario is not None:
                    scenario.benchmark_run_count += 1
                    session.add(scenario)

            session.commit()
            return row

    def update_benchmark_run_status(self, *, benchmark_run_id: str, status: str, finished: bool = False) -> None:
        with self._session_factory() as session:
            row = session.get(BenchmarkRunRecord, benchmark_run_id)
            if row is None:
                raise ValueError(f"Unknown benchmark_run_id: {benchmark_run_id}")
            row.status = status
            if finished:
                row.finished_at = _utc_now()
            session.add(row)
            session.commit()

    def get_benchmark_run(self, benchmark_run_id: str) -> BenchmarkRunRecord | None:
        with self._session_factory() as session:
            return session.get(BenchmarkRunRecord, benchmark_run_id)

    def create_evaluation_run(
        self,
        *,
        benchmark_run_id: str,
        subject_id: str,
        clone_handle_id: str | None = None,
        status: str = "running",
        subject_metadata: dict | None = None,
        adapter_session_metadata: dict | None = None,
    ) -> EvaluationRunRecord:
        with self._session_factory() as session:
            row = EvaluationRunRecord(
                benchmark_run_id=benchmark_run_id,
                subject_id=subject_id,
                clone_handle_id=clone_handle_id,
                status=status,
                subject_metadata_json=dict(subject_metadata or {}),
                adapter_session_metadata_json=dict(adapter_session_metadata or {}),
                started_at=_utc_now(),
            )
            session.add(row)
            session.commit()
            return row

    def update_evaluation_run_status(
        self,
        *,
        evaluation_run_id: str,
        status: str,
        repair_success: bool | None = None,
        resolution_result: dict | None = None,
        subject_metadata: dict | None = None,
        adapter_session_metadata: dict | None = None,
        finished: bool = False,
    ) -> None:
        with self._session_factory() as session:
            row = session.get(EvaluationRunRecord, evaluation_run_id)
            if row is None:
                raise ValueError(f"Unknown evaluation_run_id: {evaluation_run_id}")
            row.status = status
            if repair_success is not None:
                row.repair_success = repair_success
            if resolution_result is not None:
                row.resolution_result_json = dict(resolution_result)
            if subject_metadata is not None:
                row.subject_metadata_json = dict(subject_metadata)
            if adapter_session_metadata is not None:
                row.adapter_session_metadata_json = dict(adapter_session_metadata)
            if finished:
                row.finished_at = _utc_now()
            session.add(row)
            session.commit()

    def get_evaluation_run(self, evaluation_run_id: str) -> EvaluationRunRecord | None:
        with self._session_factory() as session:
            return session.get(EvaluationRunRecord, evaluation_run_id)

    def list_evaluation_runs(self, benchmark_run_id: str) -> list[EvaluationRunRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(EvaluationRunRecord)
                .where(EvaluationRunRecord.benchmark_run_id == benchmark_run_id)
                .order_by(EvaluationRunRecord.started_at.asc(), EvaluationRunRecord.id.asc())
            )
            return list(rows)

    def append_evaluation_event(
        self,
        *,
        evaluation_run_id: str,
        seq: int,
        actor_role: str,
        event_kind: str,
        payload: dict | None = None,
    ) -> EvaluationEventRecord:
        with self._session_factory() as session:
            row = EvaluationEventRecord(
                evaluation_run_id=evaluation_run_id,
                seq=seq,
                actor_role=actor_role,
                event_kind=event_kind,
                payload_json=dict(payload or {}),
            )
            session.add(row)
            session.commit()
            return row

    def list_evaluation_events(self, evaluation_run_id: str) -> list[EvaluationEventRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(EvaluationEventRecord)
                .where(EvaluationEventRecord.evaluation_run_id == evaluation_run_id)
                .order_by(EvaluationEventRecord.seq.asc())
            )
            return list(rows)

    def create_judge_job(
        self,
        *,
        benchmark_run_id: str,
        judge_adapter_type: str,
        rubric: dict,
        metadata: dict | None = None,
        status: str = "queued",
    ) -> JudgeJobRecord:
        with self._session_factory() as session:
            row = JudgeJobRecord(
                benchmark_run_id=benchmark_run_id,
                status=status,
                rubric_json=dict(rubric),
                judge_adapter_type=judge_adapter_type.strip(),
                judge_metadata_json=dict(metadata or {}),
            )
            session.add(row)
            session.commit()
            return row

    def get_judge_job(self, judge_job_id: str) -> JudgeJobRecord | None:
        with self._session_factory() as session:
            return session.get(JudgeJobRecord, judge_job_id)

    def update_judge_job_status(self, *, judge_job_id: str, status: str, started: bool = False, finished: bool = False) -> None:
        with self._session_factory() as session:
            row = session.get(JudgeJobRecord, judge_job_id)
            if row is None:
                raise ValueError(f"Unknown judge_job_id: {judge_job_id}")
            row.status = status
            if started:
                row.started_at = _utc_now()
            if finished:
                row.finished_at = _utc_now()
            session.add(row)
            session.commit()

    def create_judge_item(
        self,
        *,
        judge_job_id: str,
        evaluation_run_id: str,
        blind_label: str,
        blinded_transcript: dict,
        raw_judge_response: dict | None = None,
        parsed_scores: dict | None = None,
        summary: str = "",
    ) -> JudgeItemRecord:
        with self._session_factory() as session:
            row = JudgeItemRecord(
                judge_job_id=judge_job_id,
                evaluation_run_id=evaluation_run_id,
                blind_label=blind_label.strip(),
                blinded_transcript_json=dict(blinded_transcript),
                raw_judge_response_json=dict(raw_judge_response or {}),
                parsed_scores_json=dict(parsed_scores or {}),
                summary=summary,
            )
            session.add(row)
            session.commit()
            return row

    def list_judge_items(self, judge_job_id: str) -> list[JudgeItemRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(JudgeItemRecord)
                .where(JudgeItemRecord.judge_job_id == judge_job_id)
                .order_by(JudgeItemRecord.blind_label.asc())
            )
            return list(rows)

    def list_judge_items_for_benchmark(self, benchmark_run_id: str) -> list[JudgeItemRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(JudgeItemRecord)
                .join(JudgeJobRecord, JudgeJobRecord.id == JudgeItemRecord.judge_job_id)
                .where(JudgeJobRecord.benchmark_run_id == benchmark_run_id)
                .order_by(JudgeItemRecord.blind_label.asc())
            )
            return list(rows)
