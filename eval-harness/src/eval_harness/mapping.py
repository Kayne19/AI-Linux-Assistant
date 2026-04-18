from __future__ import annotations

from typing import Any

from .models import CommandExecutionResult, RunEvent, ScenarioSpec, SubjectSpec, TurnRecord, VerificationCheck
from .persistence.postgres_models import (
    BenchmarkSubjectRecord,
    EvaluationEventRecord,
    ScenarioRecord,
    ScenarioRevisionRecord,
    ScenarioSetupEventRecord,
)


def _as_string_items(value: Any, *, key: str = "items") -> tuple[str, ...]:
    if isinstance(value, dict):
        raw_items = value.get(key, [])
    else:
        raw_items = value or []
    return tuple(str(item).strip() for item in raw_items if str(item).strip())


def scenario_spec_from_records(scenario: ScenarioRecord, revision: ScenarioRevisionRecord) -> ScenarioSpec:
    return ScenarioSpec(
        scenario_name=scenario.scenario_name,
        title=scenario.title,
        summary=revision.summary,
        what_it_tests=_as_string_items(revision.what_it_tests_json),
        target_image=revision.target_image,
        observable_problem_statement=revision.observable_problem_statement,
        initial_user_message=revision.initial_user_message,
        sabotage_procedure=_as_string_items(revision.sabotage_plan_json, key="steps"),
        verification_probes=tuple(
            VerificationCheck.from_dict(item)
            for item in (revision.verification_plan_json.get("probes", []) if isinstance(revision.verification_plan_json, dict) else [])
        ),
        repair_checks=tuple(
            VerificationCheck.from_dict(item)
            for item in (revision.planner_metadata_json.get("repair_checks", []) if isinstance(revision.planner_metadata_json, dict) else [])
        ),
        judge_rubric=_as_string_items(revision.judge_rubric_json),
        turn_budget=int((revision.planner_metadata_json or {}).get("turn_budget", 8)),
        metadata=dict((revision.planner_metadata_json or {}).get("metadata", {}) or {}),
        planner_metadata={
            **dict(revision.planner_metadata_json or {}),
            "scenario_id": scenario.id,
            "revision_id": revision.id,
            "revision_number": revision.revision_number,
        },
    )


def subject_spec_from_record(subject: BenchmarkSubjectRecord) -> SubjectSpec:
    adapter_config = dict(subject.adapter_config_json or {})
    raw_max_turns = adapter_config.get("max_turns")
    return SubjectSpec(
        subject_name=subject.subject_name,
        adapter_type=subject.adapter_type,
        display_name=subject.display_name,
        max_turns=(int(raw_max_turns) if raw_max_turns is not None else None),
        adapter_config=adapter_config,
        metadata={},
    )


def command_result_from_payload(payload: dict[str, Any]) -> CommandExecutionResult:
    if "result" in payload and isinstance(payload["result"], dict):
        payload = payload["result"]
    return CommandExecutionResult.from_dict(payload)


def run_event_from_payload(payload: dict[str, Any]) -> RunEvent:
    return RunEvent.from_dict(payload)


def turn_record_from_setup_event(event: ScenarioSetupEventRecord) -> TurnRecord | None:
    payload = dict(event.payload_json or {})
    if event.event_kind == "message":
        return TurnRecord(
            role=str(payload.get("role", event.actor_role)),  # type: ignore[arg-type]
            content=str(payload.get("content", "")),
            created_at=event.created_at.isoformat(),
            metadata=dict(payload.get("metadata", {}) or {}),
        )
    if event.event_kind == "decision":
        return TurnRecord(
            role="planner",
            content=str(payload.get("summary", "")),
            created_at=event.created_at.isoformat(),
            metadata=payload,
        )
    return None


def turn_record_from_evaluation_event(event: EvaluationEventRecord) -> TurnRecord | None:
    payload = dict(event.payload_json or {})
    if event.event_kind != "message":
        return None
    role = str(payload.get("role", "system"))
    content = str(payload.get("content", ""))
    metadata = dict(payload.get("metadata", {}) or {})
    return TurnRecord(role=role, content=content, created_at=event.created_at.isoformat(), metadata=metadata)  # type: ignore[arg-type]
