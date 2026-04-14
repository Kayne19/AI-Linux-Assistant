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


class VariantLifecycle(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SETUP_FAILED = "setup_failed"


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
        return cls(role=str(payload.get("role", "")).strip(), content=str(payload.get("content", "")))

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
            expected_substrings=tuple(str(item) for item in payload.get("expected_substrings", []) or []),
            expected_exit_code=payload.get("expected_exit_code"),
            match_mode=VerificationMatchMode(str(match_mode)),
            timeout_seconds=int(payload.get("timeout_seconds", 60)),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class VariantSpec:
    name: str
    solver_mode: str | None = None
    proxy_agent_id: str = "proxy"
    allow_proxy_mode_override: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VariantSpec":
        return cls(
            name=str(payload.get("name", "")).strip(),
            solver_mode=payload.get("solver_mode"),
            proxy_agent_id=str(payload.get("proxy_agent_id", "proxy")).strip() or "proxy",
            allow_proxy_mode_override=bool(payload.get("allow_proxy_mode_override", False)),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


def default_variants() -> tuple[VariantSpec, ...]:
    return (
        VariantSpec(name="regular", solver_mode="off"),
        VariantSpec(name="magi_lite", solver_mode="lite"),
        VariantSpec(name="magi_full", solver_mode="full"),
        VariantSpec(
            name="hybrid",
            solver_mode=None,
            proxy_agent_id="proxy_hybrid",
            allow_proxy_mode_override=True,
            metadata={
                "proxy_mode_prefixes": {
                    "[MODE:regular]": "off",
                    "[MODE:lite]": "lite",
                    "[MODE:full]": "full",
                }
            },
        ),
    )


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    title: str
    summary: str
    target_image: str
    setup_steps: tuple[str, ...]
    broken_state_checks: tuple[VerificationCheck, ...]
    resolution_checks: tuple[VerificationCheck, ...]
    opening_user_message: str
    turn_budget: int = 8
    context_seed: tuple[TurnSeed, ...] = ()
    variants: tuple[VariantSpec, ...] = field(default_factory=default_variants)
    metadata: dict[str, Any] = field(default_factory=dict)
    grader_hints: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScenarioSpec":
        scenario_id = str(payload.get("scenario_id") or payload.get("id") or "").strip()
        variants_payload = payload.get("variants")
        variants = tuple(VariantSpec.from_dict(item) for item in variants_payload) if variants_payload else default_variants()
        return cls(
            scenario_id=scenario_id,
            title=str(payload.get("title", "")).strip(),
            summary=str(payload.get("summary", "")).strip(),
            target_image=str(payload.get("target_image", "")).strip(),
            setup_steps=tuple(str(step) for step in payload.get("setup_steps", []) or []),
            broken_state_checks=tuple(
                VerificationCheck.from_dict(item) for item in payload.get("broken_state_checks", []) or []
            ),
            resolution_checks=tuple(
                VerificationCheck.from_dict(item) for item in payload.get("resolution_checks", []) or []
            ),
            opening_user_message=str(payload.get("opening_user_message", "")).strip(),
            turn_budget=int(payload.get("turn_budget", 8)),
            context_seed=tuple(TurnSeed.from_dict(item) for item in payload.get("context_seed", []) or []),
            variants=variants,
            metadata=dict(payload.get("metadata", {}) or {}),
            grader_hints=dict(payload.get("grader_hints", {}) or {}),
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
        code = str(payload.get("code", ""))
        return cls(
            seq=int(payload.get("seq", 0)),
            event_type=RunEventType(str(event_type)),
            code=code,
            payload=dict(payload.get("payload", {}) or {}),
            created_at=str(payload.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event_type": self.event_type.value,
            "code": self.code,
            "payload": to_primitive(self.payload),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class VerificationResult:
    check_name: str
    command: str
    success: bool
    output: str
    expected_exit_code: int | None = None
    actual_exit_code: int | None = None
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VerificationResult":
        return cls(
            check_name=str(payload.get("check_name", "")),
            command=str(payload.get("command", "")),
            success=bool(payload.get("success", False)),
            output=str(payload.get("output", "")),
            expected_exit_code=payload.get("expected_exit_code"),
            actual_exit_code=payload.get("actual_exit_code"),
            started_at=str(payload.get("started_at", "")),
            finished_at=str(payload.get("finished_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class TurnRecord:
    role: Literal["user", "assistant"]
    content: str
    created_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TurnRecord":
        return cls(
            role=str(payload.get("role", "user")),  # type: ignore[arg-type]
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
            resource_type=str(payload.get("resource_type", "")),
            resource_id=str(payload.get("resource_id", "")),
            action=str(payload.get("action", "")),
            success=bool(payload.get("success", False)),
            details=str(payload.get("details", "")),
            created_at=str(payload.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class VariantArtifact:
    variant_name: str
    lifecycle: VariantLifecycle
    transcript: tuple[TurnRecord, ...] = ()
    run_ids: tuple[str, ...] = ()
    run_events: tuple[RunEvent, ...] = ()
    adapter_debug: dict[str, Any] = field(default_factory=dict)
    resolution_results: tuple[VerificationResult, ...] = ()
    error_message: str = ""
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VariantArtifact":
        return cls(
            variant_name=str(payload.get("variant_name", "")),
            lifecycle=VariantLifecycle(str(payload.get("lifecycle", VariantLifecycle.PENDING.value))),
            transcript=tuple(TurnRecord.from_dict(item) for item in payload.get("transcript", []) or []),
            run_ids=tuple(str(item) for item in payload.get("run_ids", []) or []),
            run_events=tuple(RunEvent.from_dict(item) for item in payload.get("run_events", []) or []),
            adapter_debug=dict(payload.get("adapter_debug", {}) or {}),
            resolution_results=tuple(
                VerificationResult.from_dict(item) for item in payload.get("resolution_results", []) or []
            ),
            error_message=str(payload.get("error_message", "")),
            started_at=str(payload.get("started_at", "")),
            finished_at=str(payload.get("finished_at", "")),
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
            plugin_name=str(payload.get("plugin_name", "")),
            summary=str(payload.get("summary", "")),
            metrics=dict(payload.get("metrics", {}) or {}),
            created_at=str(payload.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass(frozen=True)
class ArtifactPack:
    group_id: str
    scenario: ScenarioSpec
    backend_name: str
    controller_name: str
    adapter_name: str
    staging_handle_id: str = ""
    broken_image_id: str = ""
    setup_log: tuple[dict[str, Any], ...] = ()
    broken_state_results: tuple[VerificationResult, ...] = ()
    variant_artifacts: tuple[VariantArtifact, ...] = ()
    cleanup_records: tuple[CleanupRecord, ...] = ()
    plugin_outputs: tuple[GraderOutput, ...] = ()
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ArtifactPack":
        return cls(
            group_id=str(payload.get("group_id", "")),
            scenario=ScenarioSpec.from_dict(payload.get("scenario", {}) or {}),
            backend_name=str(payload.get("backend_name", "")),
            controller_name=str(payload.get("controller_name", "")),
            adapter_name=str(payload.get("adapter_name", "")),
            staging_handle_id=str(payload.get("staging_handle_id", "")),
            broken_image_id=str(payload.get("broken_image_id", "")),
            setup_log=tuple(dict(item) for item in payload.get("setup_log", []) or []),
            broken_state_results=tuple(
                VerificationResult.from_dict(item) for item in payload.get("broken_state_results", []) or []
            ),
            variant_artifacts=tuple(
                VariantArtifact.from_dict(item) for item in payload.get("variant_artifacts", []) or []
            ),
            cleanup_records=tuple(
                CleanupRecord.from_dict(item) for item in payload.get("cleanup_records", []) or []
            ),
            plugin_outputs=tuple(GraderOutput.from_dict(item) for item in payload.get("plugin_outputs", []) or []),
            started_at=str(payload.get("started_at", "")),
            finished_at=str(payload.get("finished_at", "")),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)
