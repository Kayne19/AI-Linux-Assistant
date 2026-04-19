from dataclasses import dataclass, field, asdict
from ingestion.identity.vocabularies import (
    ALL_ENUMS,
    coerce_enum,
    coerce_enum_list,
)

_SCALAR_ENUM_FIELDS = {
    "source_family",
    "vendor_or_project",
    "doc_kind",
    "trust_tier",
    "freshness_status",
    "os_family",
    "ingest_source_type",
}

_LIST_ENUM_FIELDS = {
    "init_systems",
    "package_managers",
    "major_subsystems",
}


@dataclass(slots=True)
class DocumentIdentity:
    canonical_source_id: str
    canonical_title: str
    title_aliases: list[str] = field(default_factory=list)
    source_family: str = "other"
    product: str | None = None
    vendor_or_project: str = "unknown"
    version: str | None = None
    release_date: str | None = None
    doc_kind: str = "other"
    trust_tier: str = "unknown"
    freshness_status: str = "unknown"
    os_family: str = "unknown"
    init_systems: list[str] = field(default_factory=lambda: ["unknown"])
    package_managers: list[str] = field(default_factory=lambda: ["unknown"])
    major_subsystems: list[str] = field(default_factory=list)
    applies_to: list[str] = field(default_factory=list)
    source_url: str | None = None
    ingest_source_type: str = "pdf_operator"
    operator_override_present: bool = False
    ingested_at: str | None = None
    pipeline_version: str = "1.0.0"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.canonical_source_id:
            errors.append("canonical_source_id is required and must be non-empty")
        if not self.canonical_title:
            errors.append("canonical_title is required and must be non-empty")
        for f in _SCALAR_ENUM_FIELDS:
            val = getattr(self, f)
            coerced = coerce_enum(f, val)
            if coerced == "unknown" and val not in (None, "", "unknown"):
                errors.append(f"{f}: invalid value {val!r}")
        for f in _LIST_ENUM_FIELDS:
            for val in getattr(self, f):
                coerced = coerce_enum(f, val)
                if coerced == "unknown" and val not in (None, "", "unknown"):
                    errors.append(f"{f}: invalid value {val!r}")
        return errors

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DocumentIdentity":
        data = dict(d)
        for f in _SCALAR_ENUM_FIELDS:
            if f in data:
                data[f] = coerce_enum(f, data[f])
        for f in _LIST_ENUM_FIELDS:
            if f in data:
                data[f] = coerce_enum_list(f, data[f])
        return cls(**data)


@dataclass(slots=True)
class ChunkMetadata:
    canonical_source_id: str
    page_start: int
    page_end: int
    section_path: list[str] = field(default_factory=list)
    section_title: str | None = None
    chunk_type: str = "uncategorized"
    local_subsystems: list[str] = field(default_factory=list)
    entities: dict[str, list[str]] = field(default_factory=dict)
    applies_to_override: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.canonical_source_id:
            errors.append("canonical_source_id is required and must be non-empty")
        if self.page_end < self.page_start:
            errors.append(
                f"page_end ({self.page_end}) must be >= page_start ({self.page_start})"
            )
        coerced = coerce_enum("chunk_type", self.chunk_type)
        if coerced == "unknown" and self.chunk_type not in (None, "", "unknown"):
            errors.append(f"chunk_type: invalid value {self.chunk_type!r}")
        return errors

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChunkMetadata":
        data = dict(d)
        if "chunk_type" in data:
            data["chunk_type"] = coerce_enum("chunk_type", data["chunk_type"])
        return cls(**data)
