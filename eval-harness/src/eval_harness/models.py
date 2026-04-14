from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CheckPhase(str, Enum):
    BROKEN_STATE = "broken_state"
    RESOLUTION = "resolution"


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(slots=True)
class VerificationCheck:
    check_id: str
    instruction: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.check_id = self.check_id.strip()
        self.instruction = self.instruction.strip()
        if not self.check_id:
            raise ValueError("verification check_id must not be empty")
        if not self.instruction:
            raise ValueError("verification instruction must not be empty")


@dataclass(slots=True)
class VerificationResult:
    check_id: str
    phase: CheckPhase
    status: CheckStatus
    observed: str | None = None
    details: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status is CheckStatus.PASS


@dataclass(slots=True)
class GraderOutput:
    plugin_name: str
    artifact_pack_id: str
    metrics: dict[str, float] = field(default_factory=dict)
    overall_score: float | None = None
    notes: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.plugin_name = self.plugin_name.strip()
        self.artifact_pack_id = self.artifact_pack_id.strip()
        if not self.plugin_name:
            raise ValueError("grader output plugin_name must not be empty")
        if not self.artifact_pack_id:
            raise ValueError("grader output artifact_pack_id must not be empty")
