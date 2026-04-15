from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def to_primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: to_primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    return value


class VerificationMatchMode(str, Enum):
    ALL = "all"
    ANY = "any"


class PlannerReviewOutcome(str, Enum):
    APPROVE = "approve"
    CORRECT = "correct"


class ScenarioLifecycleStatus(str, Enum):
    DRAFT = "draft"
    VERIFIED = "verified"
    FAILED_SETUP = "failed_setup"
    ARCHIVED = "archived"


class ScenarioSetupStatus(str, Enum):
    RUNNING = "running"
    NEEDS_CORRECTION = "needs_correction"
    VERIFIED = "verified"
    FAILED_MAX_CORRECTIONS = "failed_max_corrections"
    FAILED_INFRA = "failed_infra"


class BenchmarkRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EvaluationRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JudgeJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunEventType(str, Enum):
    STATE = "state"
    EVENT = "event"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"
    PAUSED = "paused"


@dataclass(frozen=True)
class TurnSeed:
    role: str
    content: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TurnSeed":
        return cls(role=str(payload.get("role", "")).strip(), content=str(payload.get("content", "")).strip())

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class VerificationCheck:
    name: str
    command: str
    expected_substrings: tuple[str, ...] = ()
    expected_exit_code: int | None = None
    match_mode: VerificationMatchMode = VerificationMatchMode.ALL
    timeout_seconds: int = 60

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VerificationCheck":
        match_mode = payload.get("match_mode", VerificationMatchMode.ALL.value)
        return cls(
            name=str(payload.get("name", "")).strip(),
            command=str(payload.get("command", "")).strip(),
            expected_substrings=tuple(str(item).strip() for item in payload.get("expected_substrings", []) or []),
            expected_exit_code=payload.get("expected_exit_code"),
            match_mode=VerificationMatchMode(str(match_mode)),
            timeout_seconds=int(payload.get("timeout_seconds", 60)),
        )

    def has_machine_expectation(self) -> bool:
        return bool(self.expected_substrings) or self.expected_exit_code is not None

    def is_satisfied_by(self, result: "CommandExecutionResult") -> bool:
        output = result.combined_output()
        success = True
        if self.expected_substrings:
            if self.match_mode == VerificationMatchMode.ALL:
                success = all(item in output for item in self.expected_substrings)
            else:
                success = any(item in output for item in self.expected_substrings)
        if self.expected_exit_code is not None:
            success = success and result.exit_code == self.expected_exit_code
        return success

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class CommandExecutionResult:
    command: str
    stdout: str
    stderr: str
    exit_code: int | None
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CommandExecutionResult":
        return cls(
            command=str(payload.get("command", "")).strip(),
            stdout=str(payload.get("stdout", "")),
            stderr=str(payload.get("stderr", "")),
            exit_code=payload.get("exit_code"),
            started_at=str(payload.get("started_at", "")),
            finished_at=str(payload.get("finished_at", "")),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def combined_output(self) -> str:
        stdout = self.stdout.strip()
        stderr = self.stderr.strip()
        if stdout and stderr:
            return f"{stdout}\n{stderr}"
        return stdout or stderr

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class SubjectSpec:
    subject_name: str
    adapter_type: str
    display_name: str = ""
    max_turns: int = 8
    adapter_config: dict[str, Any] = field(default_factory=dict)
    context_seed: tuple[TurnSeed, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SubjectSpec":
        return cls(
            subject_name=str(payload.get("subject_name", "")).strip(),
            adapter_type=str(payload.get("adapter_type", "")).strip(),
            display_name=str(payload.get("display_name", "")).strip(),
            max_turns=int(payload.get("max_turns", 8)),
            adapter_config=dict(payload.get("adapter_config", {}) or {}),
            context_seed=tuple(TurnSeed.from_dict(item) for item in payload.get("context_seed", []) or []),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_name: str
    title: str
    summary: str
    what_it_tests: tuple[str, ...]
    target_image: str
    observable_problem_statement: str
    sabotage_procedure: tuple[str, ...]
    verification_probes: tuple[VerificationCheck, ...]
    repair_checks: tuple[VerificationCheck, ...]
    judge_rubric: tuple[str, ...]
    turn_budget: int = 8
    context_seed: tuple[TurnSeed, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    planner_metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScenarioSpec":
        verification_payload = payload.get("verification_probes") or payload.get("broken_state_checks") or []
        repair_payload = payload.get("repair_checks") or payload.get("resolution_checks") or []
        what_it_tests = payload.get("what_it_tests", []) or []
        judge_rubric = payload.get("judge_rubric", []) or (payload.get("grader_hints", {}) or {}).get("rubric", []) or []
        return cls(
            scenario_name=str(payload.get("scenario_name") or payload.get("scenario_id") or payload.get("id") or "").strip(),
            title=str(payload.get("title", "")).strip(),
            summary=str(payload.get("summary", "")).strip(),
            what_it_tests=tuple(str(item).strip() for item in what_it_tests if str(item).strip()),
            target_image=str(payload.get("target_image", "")).strip(),
            observable_problem_statement=str(
                payload.get("observable_problem_statement") or payload.get("opening_user_message") or ""
            ).strip(),
            sabotage_procedure=tuple(
                str(step).strip()
                for step in payload.get("sabotage_procedure", []) or payload.get("setup_steps", []) or []
                if str(step).strip()
            ),
            verification_probes=tuple(VerificationCheck.from_dict(item) for item in verification_payload),
            repair_checks=tuple(VerificationCheck.from_dict(item) for item in repair_payload),
            judge_rubric=tuple(str(item).strip() for item in judge_rubric if str(item).strip()),
            turn_budget=int(payload.get("turn_budget", 8)),
            context_seed=tuple(TurnSeed.from_dict(item) for item in payload.get("context_seed", []) or []),
            metadata=dict(payload.get("metadata", {}) or {}),
            planner_metadata=dict(payload.get("planner_metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)

    @property
    def revision_ref(self) -> str:
        revision = self.planner_metadata.get("revision_number")
        if revision is None:
            return self.scenario_name
        return f"{self.scenario_name}@r{int(revision):03d}"


@dataclass(frozen=True)
class PlannerScenarioRequest:
    planning_brief: str
    target_image: str
    scenario_name_hint: str = ""
    constraints: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PlannerScenarioRequest":
        return cls(
            planning_brief=str(payload.get("planning_brief", "")).strip(),
            target_image=str(payload.get("target_image", "")).strip(),
            scenario_name_hint=str(payload.get("scenario_name_hint", "")).strip(),
            constraints=tuple(str(item).strip() for item in payload.get("constraints", []) or [] if str(item).strip()),
            tags=tuple(str(item).strip() for item in payload.get("tags", []) or [] if str(item).strip()),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class PlannerReviewDecision:
    outcome: PlannerReviewOutcome
    summary: str
    correction_instructions: tuple[str, ...] = ()
    updated_observable_problem_statement: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PlannerReviewDecision":
        raw_correction_instructions = payload.get("correction_instructions", [])
        if isinstance(raw_correction_instructions, str):
            correction_items = (raw_correction_instructions,)
        else:
            correction_items = tuple(raw_correction_instructions or ())
        return cls(
            outcome=PlannerReviewOutcome(str(payload.get("outcome", PlannerReviewOutcome.CORRECT.value))),
            summary=str(payload.get("summary", "")).strip(),
            correction_instructions=tuple(
                str(item).strip() for item in correction_items if str(item).strip()
            ),
            updated_observable_problem_statement=str(payload.get("updated_observable_problem_statement", "")).strip(),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class RunEvent:
    seq: int
    event_type: RunEventType
    code: str
    payload: dict[str, Any]
    created_at: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunEvent":
        event_type = payload.get("event_type") or payload.get("type") or RunEventType.EVENT.value
        return cls(
            seq=int(payload.get("seq", 0)),
            event_type=RunEventType(str(event_type)),
            code=str(payload.get("code", "")).strip() or str(event_type),
            payload=dict(payload.get("payload", {}) or {}),
            created_at=str(payload.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class TurnRecord:
    role: Literal["user", "assistant", "planner", "sabotage_agent", "user_proxy", "judge", "system"]
    content: str
    created_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TurnRecord":
        return cls(
            role=str(payload.get("role", "system")),  # type: ignore[arg-type]
            content=str(payload.get("content", "")),
            created_at=str(payload.get("created_at", "")),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class AdapterTurnResult:
    user_message: str
    assistant_message: str
    run_id: str | None = None
    status: str = "completed"
    terminal_event_type: str = ""
    events: tuple[RunEvent, ...] = ()
    debug: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AdapterTurnResult":
        return cls(
            user_message=str(payload.get("user_message", "")),
            assistant_message=str(payload.get("assistant_message", "")),
            run_id=(str(payload.get("run_id", "")).strip() or None),
            status=str(payload.get("status", "completed")),
            terminal_event_type=str(payload.get("terminal_event_type", "")),
            events=tuple(RunEvent.from_dict(item) for item in payload.get("events", []) or []),
            debug=dict(payload.get("debug", {}) or {}),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class BlindJudgeRequest:
    blind_label: str
    transcript: tuple[TurnRecord, ...]
    rubric: tuple[str, ...]
    repair_success: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BlindJudgeRequest":
        return cls(
            blind_label=str(payload.get("blind_label", "")).strip(),
            transcript=tuple(TurnRecord.from_dict(item) for item in payload.get("transcript", []) or []),
            rubric=tuple(str(item).strip() for item in payload.get("rubric", []) or [] if str(item).strip()),
            repair_success=payload.get("repair_success"),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class BlindJudgeResult:
    blind_label: str
    summary: str
    scores: dict[str, Any]
    raw_response: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BlindJudgeResult":
        return cls(
            blind_label=str(payload.get("blind_label", "")).strip(),
            summary=str(payload.get("summary", "")).strip(),
            scores=dict(payload.get("scores", {}) or {}),
            raw_response=dict(payload.get("raw_response", {}) or {}),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class CleanupRecord:
    resource_type: str
    resource_id: str
    action: str
    success: bool
    details: str = ""
    created_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CleanupRecord":
        return cls(
            resource_type=str(payload.get("resource_type", "")).strip(),
            resource_id=str(payload.get("resource_id", "")).strip(),
            action=str(payload.get("action", "")).strip(),
            success=bool(payload.get("success", False)),
            details=str(payload.get("details", "")),
            created_at=str(payload.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class EvaluationArtifact:
    evaluation_run_id: str
    subject_name: str
    blind_label: str = ""
    status: str = EvaluationRunStatus.QUEUED.value
    transcript: tuple[TurnRecord, ...] = ()
    run_ids: tuple[str, ...] = ()
    run_events: tuple[RunEvent, ...] = ()
    command_results: tuple[CommandExecutionResult, ...] = ()
    repair_success: bool | None = None
    repair_checks: tuple[VerificationCheck, ...] = ()
    judge_scores: dict[str, Any] = field(default_factory=dict)
    adapter_debug: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    error_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvaluationArtifact":
        return cls(
            evaluation_run_id=str(payload.get("evaluation_run_id", "")).strip(),
            subject_name=str(payload.get("subject_name", "")).strip(),
            blind_label=str(payload.get("blind_label", "")).strip(),
            status=str(payload.get("status", EvaluationRunStatus.QUEUED.value)),
            transcript=tuple(TurnRecord.from_dict(item) for item in payload.get("transcript", []) or []),
            run_ids=tuple(str(item).strip() for item in payload.get("run_ids", []) or [] if str(item).strip()),
            run_events=tuple(RunEvent.from_dict(item) for item in payload.get("run_events", []) or []),
            command_results=tuple(CommandExecutionResult.from_dict(item) for item in payload.get("command_results", []) or []),
            repair_success=payload.get("repair_success"),
            repair_checks=tuple(VerificationCheck.from_dict(item) for item in payload.get("repair_checks", []) or []),
            judge_scores=dict(payload.get("judge_scores", {}) or {}),
            adapter_debug=dict(payload.get("adapter_debug", {}) or {}),
            started_at=str(payload.get("started_at", "")),
            finished_at=str(payload.get("finished_at", "")),
            error_message=str(payload.get("error_message", "")),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class JudgeArtifact:
    judge_job_id: str
    blind_label: str
    summary: str
    scores: dict[str, Any]
    raw_response: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "JudgeArtifact":
        return cls(
            judge_job_id=str(payload.get("judge_job_id", "")).strip(),
            blind_label=str(payload.get("blind_label", "")).strip(),
            summary=str(payload.get("summary", "")).strip(),
            scores=dict(payload.get("scores", {}) or {}),
            raw_response=dict(payload.get("raw_response", {}) or {}),
            created_at=str(payload.get("created_at", "")),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class GraderOutput:
    plugin_name: str
    summary: str
    metrics: dict[str, Any]
    created_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GraderOutput":
        return cls(
            plugin_name=str(payload.get("plugin_name", "")).strip(),
            summary=str(payload.get("summary", "")).strip(),
            metrics=dict(payload.get("metrics", {}) or {}),
            created_at=str(payload.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class ArtifactPack:
    benchmark_run_id: str
    scenario_name: str
    scenario_revision_id: str
    setup_run_id: str
    backend_name: str
    controller_name: str
    subject_adapter_types: tuple[str, ...] = ()
    broken_image_id: str = ""
    setup_transcript: tuple[TurnRecord, ...] = ()
    setup_command_results: tuple[CommandExecutionResult, ...] = ()
    evaluations: tuple[EvaluationArtifact, ...] = ()
    judge_artifacts: tuple[JudgeArtifact, ...] = ()
    cleanup_records: tuple[CleanupRecord, ...] = ()
    plugin_outputs: tuple[GraderOutput, ...] = ()
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def export_id(self) -> str:
        return self.benchmark_run_id or self.setup_run_id or self.scenario_revision_id or self.scenario_name

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ArtifactPack":
        return cls(
            benchmark_run_id=str(payload.get("benchmark_run_id", "")).strip(),
            scenario_name=str(payload.get("scenario_name", "")).strip(),
            scenario_revision_id=str(payload.get("scenario_revision_id", "")).strip(),
            setup_run_id=str(payload.get("setup_run_id", "")).strip(),
            backend_name=str(payload.get("backend_name", "")).strip(),
            controller_name=str(payload.get("controller_name", "")).strip(),
            subject_adapter_types=tuple(
                str(item).strip() for item in payload.get("subject_adapter_types", []) or [] if str(item).strip()
            ),
            broken_image_id=str(payload.get("broken_image_id", "")).strip(),
            setup_transcript=tuple(TurnRecord.from_dict(item) for item in payload.get("setup_transcript", []) or []),
            setup_command_results=tuple(
                CommandExecutionResult.from_dict(item) for item in payload.get("setup_command_results", []) or []
            ),
            evaluations=tuple(EvaluationArtifact.from_dict(item) for item in payload.get("evaluations", []) or []),
            judge_artifacts=tuple(JudgeArtifact.from_dict(item) for item in payload.get("judge_artifacts", []) or []),
            cleanup_records=tuple(CleanupRecord.from_dict(item) for item in payload.get("cleanup_records", []) or []),
            plugin_outputs=tuple(GraderOutput.from_dict(item) for item in payload.get("plugin_outputs", []) or []),
            started_at=str(payload.get("started_at", "")),
            finished_at=str(payload.get("finished_at", "")),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)
