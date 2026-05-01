from __future__ import annotations

import json
from pathlib import Path

from .mapping import (
    command_result_from_payload,
    run_event_from_payload,
    turn_record_from_evaluation_event,
    turn_record_from_setup_event,
)
from .models import ArtifactPack, EvaluationArtifact, GraderOutput, JudgeArtifact
from .persistence.store import EvalHarnessStore


class ArtifactStore:
    """File-backed export helper for replayable post-run analysis."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def export_directory(self, export_id: str) -> Path:
        directory = self.root / export_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def pack_path(self, export_id: str) -> Path:
        return self.export_directory(export_id) / "artifact-pack.json"

    def plugin_directory(self, export_id: str) -> Path:
        directory = self.export_directory(export_id) / "plugins"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def save_pack(self, pack: ArtifactPack, export_id: str | None = None) -> Path:
        resolved_id = export_id or pack.export_id
        path = self.pack_path(resolved_id)
        path.write_text(json.dumps(pack.to_dict(), indent=2), encoding="utf-8")
        return path

    def load_pack(self, path: str | Path) -> ArtifactPack:
        pack_path = Path(path)
        payload = json.loads(pack_path.read_text(encoding="utf-8"))
        return ArtifactPack.from_dict(payload)

    def save_plugin_output(self, export_id: str, output: GraderOutput) -> Path:
        path = self.plugin_directory(export_id) / f"{output.plugin_name}.json"
        path.write_text(json.dumps(output.to_dict(), indent=2), encoding="utf-8")
        return path


class PostgresArtifactExporter:
    """Build file export artifacts from Postgres-backed benchmark records."""

    def __init__(self, store: EvalHarnessStore):
        self.store = store

    def export_benchmark_run(
        self,
        benchmark_run_id: str,
        *,
        backend_name: str,
        controller_name: str,
    ) -> ArtifactPack:
        benchmark_run = self.store.get_benchmark_run(benchmark_run_id)
        if benchmark_run is None:
            raise ValueError(f"Unknown benchmark run {benchmark_run_id}")
        setup_run = self.store.get_setup_run(benchmark_run.verified_setup_run_id)
        if setup_run is None:
            raise ValueError(f"Unknown setup run {benchmark_run.verified_setup_run_id}")
        scenario_revision = self.store.get_scenario_revision(benchmark_run.scenario_revision_id)
        if scenario_revision is None:
            raise ValueError(f"Unknown scenario revision {benchmark_run.scenario_revision_id}")
        scenario = self.store.get_scenario(scenario_revision.scenario_id)
        if scenario is None:
            raise ValueError(f"Unknown scenario {scenario_revision.scenario_id}")

        setup_transcript = tuple(
            turn
            for event in self.store.list_setup_events(setup_run.id)
            if (turn := turn_record_from_setup_event(event)) is not None
        )
        setup_command_results = tuple(
            command_result_from_payload(event.payload_json)
            for event in self.store.list_setup_events(setup_run.id)
            if event.event_kind == "command_result"
        )
        evaluation_rows = self.store.list_evaluation_runs(benchmark_run_id)
        judge_items_by_evaluation = {
            item.evaluation_run_id: item for item in self.store.list_judge_items_for_benchmark(benchmark_run_id)
        }
        subject_adapter_types: set[str] = set()
        evaluations: list[EvaluationArtifact] = []
        for evaluation_row in evaluation_rows:
            subject = self.store.get_subject(evaluation_row.subject_id)
            subject_name = subject.subject_name if subject is not None else evaluation_row.subject_id
            if subject is not None:
                subject_adapter_types.add(subject.adapter_type)
            events = self.store.list_evaluation_events(evaluation_row.id)
            judge_item = judge_items_by_evaluation.get(evaluation_row.id)
            evaluations.append(
                EvaluationArtifact(
                    evaluation_run_id=evaluation_row.id,
                    subject_name=subject_name,
                    blind_label=judge_item.blind_label if judge_item is not None else "",
                    status=evaluation_row.status,
                    transcript=tuple(
                        turn
                        for event in events
                        if (turn := turn_record_from_evaluation_event(event)) is not None
                    ),
                    run_ids=tuple(
                        str(event.payload_json.get("run_id", "")).strip()
                        for event in events
                        if event.event_kind == "message" and event.actor_role == "subject"
                        if str(event.payload_json.get("run_id", "")).strip()
                    ),
                    run_events=tuple(
                        run_event_from_payload(event.payload_json)
                        for event in events
                        if event.event_kind == "run_event"
                    ),
                    command_results=tuple(
                        command_result_from_payload(event.payload_json)
                        for event in events
                        if event.event_kind == "command_result"
                    ),
                    repair_success=evaluation_row.repair_success,
                    repair_checks=(),
                    judge_scores=dict(judge_item.parsed_scores_json if judge_item is not None else {}),
                    adapter_debug=dict(evaluation_row.adapter_session_metadata_json or {}),
                    started_at=evaluation_row.started_at.isoformat() if evaluation_row.started_at else "",
                    finished_at=evaluation_row.finished_at.isoformat() if evaluation_row.finished_at else "",
                    metadata=dict(evaluation_row.subject_metadata_json or {}),
                )
            )

        judge_artifacts = [
            JudgeArtifact(
                judge_job_id=item.judge_job_id,
                blind_label=item.blind_label,
                summary=item.summary,
                scores=dict(item.parsed_scores_json or {}),
                raw_response=dict(item.raw_judge_response_json or {}),
                created_at=item.created_at.isoformat(),
            )
            for item in self.store.list_judge_items_for_benchmark(benchmark_run_id)
        ]

        return ArtifactPack(
            benchmark_run_id=benchmark_run.id,
            scenario_name=scenario.scenario_name,
            scenario_revision_id=scenario_revision.id,
            setup_run_id=setup_run.id,
            backend_name=backend_name,
            controller_name=controller_name,
            subject_adapter_types=tuple(sorted(subject_adapter_types)),
            broken_image_id=setup_run.broken_image_id or "",
            setup_transcript=setup_transcript,
            setup_command_results=setup_command_results,
            evaluations=tuple(evaluations),
            judge_artifacts=tuple(judge_artifacts),
            started_at=benchmark_run.started_at.isoformat() if benchmark_run.started_at else "",
            finished_at=benchmark_run.finished_at.isoformat() if benchmark_run.finished_at else "",
            metadata=dict(benchmark_run.metadata_json or {}),
        )
