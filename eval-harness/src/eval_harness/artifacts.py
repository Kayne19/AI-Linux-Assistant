from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .models import CheckPhase, CheckStatus, RunStatus, VerificationResult, utc_now


@dataclass(slots=True)
class VariantArtifact:
    variant_id: str
    status: RunStatus
    check_results: list[VerificationResult] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)
    command_outputs: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def resolution_passed(self) -> bool | None:
        resolution = [
            result for result in self.check_results if result.phase is CheckPhase.RESOLUTION
        ]
        if not resolution:
            return None
        return all(result.status is CheckStatus.PASS for result in resolution)


@dataclass(slots=True)
class ArtifactPack:
    artifact_pack_id: str
    scenario_id: str
    run_group_id: str
    variants: list[VariantArtifact] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_variant(self, variant_id: str) -> VariantArtifact | None:
        for variant in self.variants:
            if variant.variant_id == variant_id:
                return variant
        return None
