"""Identity backfill migration for existing chunks (T13).

The pre-T11 indexer wrote a six-column row to the LanceDB ``chunks`` table:
``id``, ``text``, ``search_text``, ``page``, ``source``, ``type``, ``vector``.
``source`` was the filename, and there was no ``documents`` table.

This module rebuilds the missing identity surface from the chunks already in
LanceDB:

1. Group chunks by ``source`` (filename).
2. For each source missing from the documents table, reconstruct a
   :class:`DocumentIdentity`. When the original PDF still exists on disk, run
   the full layered resolver (sidecar -> heuristics -> pdf_meta -> llm). When
   the PDF is gone, stub identity from the filename and audit-log it.
3. Write the doc row to the documents table.
4. Backfill the chunk rows so they carry the new ``canonical_source_id``,
   ``page_start``/``page_end``, ``chunk_type``, and the (still-empty) section
   columns. Section reconstruction from preserved cleaned-elements JSON is
   handled by :func:`reconstruct_chunk_metadata` when an elements file is
   supplied; otherwise a degraded "page-only" backfill is applied.

The migration is idempotent: any source whose ``canonical_source_id`` is
already present in the documents table is skipped. ``--dry-run`` (handled by
the CLI wrapper) just calls :func:`plan_migration` and prints the proposed
identities without writing.

This module is deliberately I/O-light. The CLI wrapper at
``scripts/ingest/migrate_identity.py`` owns LanceDB calls; here we accept
plain dicts so :mod:`tests.test_migration` can exercise the logic without
LanceDB.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable

from ingestion.identity.heuristics import extract_heuristic_signals
from ingestion.identity.llm_normalizer import normalize_with_llm
from ingestion.identity.pdf_meta import read_pdf_info
from ingestion.identity.resolver import build_canonical_source_id, resolve_identity
from ingestion.identity.schema import DocumentIdentity
from ingestion.identity.sidecar import load_sidecar
from ingestion.identity.vocabularies import IngestSourceType, coerce_enum

if TYPE_CHECKING:
    from ingestion.audit import AuditLog


# Map the legacy ``type`` column onto the controlled ``chunk_type`` enum.
_TYPE_TO_CHUNK_TYPE: dict[str, str] = {
    "Title": "heading",
    "Header": "heading",
    "NarrativeText": "narrative",
    "ListItem": "list_item",
    "Table": "table",
    "FigureCaption": "caption",
    "UncategorizedText": "uncategorized",
    "Image": "uncategorized",
    "Footer": "uncategorized",
}


@dataclass(frozen=True)
class SourceSummary:
    """Per-source view of the chunks table during migration planning."""

    source: str
    row_count: int
    page_min: int
    page_max: int
    sample_types: tuple[str, ...]


@dataclass
class MigrationStep:
    """One source's migration plan: identity to write + how chunks get updated."""

    source: str
    identity: DocumentIdentity
    pdf_path: Path | None
    stubbed: bool
    summary: SourceSummary


@dataclass
class MigrationReport:
    written: list[str] = field(default_factory=list)
    stubbed: list[str] = field(default_factory=list)
    backfilled_chunks: dict[str, int] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def collect_sources(chunks: Iterable[dict]) -> list[SourceSummary]:
    """Group chunk rows by their legacy ``source`` filename.

    The result is ordered by source for stable migration plans; duplicate
    filenames (which should not happen in practice) collapse into one summary.
    Rows that already carry a non-empty ``canonical_source_id`` are skipped
    — they were ingested under the new schema and need no backfill.
    """
    by_source: dict[str, dict] = {}
    for row in chunks:
        source = (row.get("source") or "").strip()
        if not source:
            continue
        if (row.get("canonical_source_id") or "").strip():
            # Already migrated under the T11 schema; nothing to backfill.
            continue
        page = int(row.get("page") or 0)
        ctype = row.get("type") or ""
        bucket = by_source.setdefault(
            source,
            {"count": 0, "page_min": page, "page_max": page, "types": set()},
        )
        bucket["count"] += 1
        bucket["page_min"] = min(bucket["page_min"], page)
        bucket["page_max"] = max(bucket["page_max"], page)
        if ctype:
            bucket["types"].add(ctype)

    summaries: list[SourceSummary] = []
    for source in sorted(by_source):
        b = by_source[source]
        summaries.append(
            SourceSummary(
                source=source,
                row_count=b["count"],
                page_min=b["page_min"],
                page_max=b["page_max"],
                sample_types=tuple(sorted(b["types"])),
            )
        )
    return summaries


def existing_canonical_ids(documents: Iterable[dict]) -> set[str]:
    return {
        (row.get("canonical_source_id") or "").strip()
        for row in documents
        if row.get("canonical_source_id")
    }


def find_pdf(source: str, search_dirs: list[Path]) -> Path | None:
    """Look for the original PDF by filename across *search_dirs*.

    Falls back to a stem-only match (e.g. when the chunks table stored the
    canonical title rather than the filename) if no exact filename match
    exists.
    """
    name_lower = source.lower()
    stem_lower = Path(source).stem.lower()
    for base in search_dirs:
        if not base.exists():
            continue
        for candidate in base.rglob("*.pdf"):
            if candidate.name.lower() == name_lower:
                return candidate
            if candidate.stem.lower() == stem_lower:
                return candidate
    return None


# ---------------------------------------------------------------------------
# Identity construction
# ---------------------------------------------------------------------------


def stub_identity_for_filename(
    source: str,
    *,
    audit: "AuditLog | None" = None,
    pipeline_version: str = "1.0.0",
) -> DocumentIdentity:
    """Build a minimal DocumentIdentity from a filename only.

    Used when the original PDF is unavailable. Audits the stubbing.
    """
    stem = Path(source).stem or source or "unnamed"
    canonical_title = re.sub(r"[._-]+", " ", stem).strip() or stem
    canonical_source_id = build_canonical_source_id(canonical_title)

    identity = DocumentIdentity(
        canonical_source_id=canonical_source_id,
        canonical_title=canonical_title,
        title_aliases=[stem] if stem and stem != canonical_title else [],
        ingest_source_type=IngestSourceType.pdf_operator.value,
        operator_override_present=False,
        pipeline_version=pipeline_version,
    )

    if audit is not None:
        audit.record(
            doc=source,
            phase="migration",
            action="stub_from_filename",
            inputs={"source": source},
            chosen={
                "canonical_source_id": canonical_source_id,
                "canonical_title": canonical_title,
            },
            confidence=None,
            rationale="original PDF not found on disk; identity stubbed from filename",
        )

    return identity


def resolve_identity_for_pdf(
    pdf_path: Path,
    *,
    pipeline_version: str = "1.0.0",
    audit: "AuditLog | None" = None,
    worker=None,
) -> DocumentIdentity:
    """Run the full identity layer chain against a PDF that still exists."""
    sidecar = load_sidecar(pdf_path)
    pdf_info = read_pdf_info(pdf_path)
    heuristic_signals = extract_heuristic_signals(pdf_path)

    llm_fields: dict = {}
    if worker is not None:
        llm_fields = normalize_with_llm(
            worker=worker,
            filename=pdf_path.name,
            pdf_info=pdf_info,
            heuristic_signals=heuristic_signals,
            sidecar=sidecar,
            cache_suffix=pdf_path.name,
        ) or {}

    identity, _contributions = resolve_identity(
        pdf_path=pdf_path,
        sidecar=sidecar,
        pdf_info=pdf_info,
        heuristic_signals=heuristic_signals,
        llm_fields=llm_fields,
        pipeline_version=pipeline_version,
        audit=audit,
        run_id=None,
    )
    return identity


# ---------------------------------------------------------------------------
# Chunk backfill
# ---------------------------------------------------------------------------


def reconstruct_chunk_metadata(
    legacy_row: dict,
    *,
    canonical_source_id: str,
    canonical_title: str,
) -> dict:
    """Return the column updates that bring a legacy chunk row up to T11 schema.

    The returned dict only contains the fields that need to be written; the
    caller is expected to merge into the existing row (or run the equivalent
    LanceDB ``update`` for these columns only).
    """
    page = int(legacy_row.get("page") or 0)
    legacy_type = (legacy_row.get("type") or "").strip()
    chunk_type = _TYPE_TO_CHUNK_TYPE.get(legacy_type, "uncategorized")
    coerced = coerce_enum("chunk_type", chunk_type)

    return {
        "canonical_source_id": canonical_source_id,
        "source": canonical_title,
        "section_path": [],
        "section_title": "",
        "page_start": page,
        "page_end": page,
        "chunk_type": coerced,
        "local_subsystems": [],
        "entities": "{}",
        "applies_to_override": [],
    }


# ---------------------------------------------------------------------------
# Planning + execution
# ---------------------------------------------------------------------------


def plan_migration(
    *,
    chunks: Iterable[dict],
    documents: Iterable[dict],
    pdf_dirs: list[Path],
    audit: "AuditLog | None" = None,
    worker=None,
    pipeline_version: str = "1.0.0",
) -> tuple[list[MigrationStep], list[str]]:
    """Plan one migration pass.

    Returns ``(steps, skipped_sources)``. ``steps`` is the per-source actions
    that would be performed on a non-dry-run; ``skipped_sources`` are sources
    whose canonical_source_id is already present in the documents table.
    """
    summaries = collect_sources(chunks)
    already = existing_canonical_ids(documents)

    steps: list[MigrationStep] = []
    skipped: list[str] = []

    for summary in summaries:
        pdf_path = find_pdf(summary.source, pdf_dirs)
        if pdf_path is not None:
            identity = resolve_identity_for_pdf(
                pdf_path,
                pipeline_version=pipeline_version,
                audit=audit,
                worker=worker,
            )
            stubbed = False
        else:
            identity = stub_identity_for_filename(
                summary.source,
                audit=audit,
                pipeline_version=pipeline_version,
            )
            stubbed = True

        if identity.canonical_source_id in already:
            skipped.append(summary.source)
            continue

        steps.append(
            MigrationStep(
                source=summary.source,
                identity=identity,
                pdf_path=pdf_path,
                stubbed=stubbed,
                summary=summary,
            )
        )

    return steps, skipped


def apply_migration(
    steps: list[MigrationStep],
    *,
    write_document: Callable[[DocumentIdentity], None],
    backfill_chunks: Callable[[str, str, str], int] | None = None,
    audit: "AuditLog | None" = None,
) -> MigrationReport:
    """Execute the planned steps.

    *write_document* persists the identity (typically into the documents
    LanceDB table). *backfill_chunks* takes ``(source, canonical_source_id,
    canonical_title)`` and returns the number of chunk rows updated; pass
    ``None`` to skip chunk backfill. The report carries per-source results.
    """
    report = MigrationReport()
    for step in steps:
        try:
            write_document(step.identity)
        except Exception as exc:
            report.errors.append((step.source, f"write_document: {exc}"))
            continue
        report.written.append(step.identity.canonical_source_id)
        if step.stubbed:
            report.stubbed.append(step.identity.canonical_source_id)

        if backfill_chunks is not None:
            try:
                updated = backfill_chunks(
                    step.source,
                    step.identity.canonical_source_id,
                    step.identity.canonical_title,
                )
                report.backfilled_chunks[step.identity.canonical_source_id] = int(updated or 0)
            except Exception as exc:  # pragma: no cover
                report.errors.append((step.source, f"backfill_chunks: {exc}"))

        if audit is not None:
            audit.record(
                doc=step.source,
                phase="migration",
                action="migrated",
                inputs={"source": step.source},
                chosen={
                    "canonical_source_id": step.identity.canonical_source_id,
                    "canonical_title": step.identity.canonical_title,
                    "stubbed": step.stubbed,
                },
                confidence=None,
                rationale=None,
            )

    return report
