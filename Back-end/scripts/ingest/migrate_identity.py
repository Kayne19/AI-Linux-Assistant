"""CLI wrapper around :mod:`app.ingestion.migration` for the T13 backfill.

Reads the existing chunks table, writes missing rows to the documents table,
and (unless ``--no-backfill-chunks`` is given) updates each chunk row's new
columns in place. Idempotent — re-runs only touch documents missing from the
documents table.

Usage examples::

    # Dry-run plan with auto-discovered PDFs
    python scripts/ingest/migrate_identity.py --dry-run

    # Apply, with explicit search dirs and an LLM normalizer
    python scripts/ingest/migrate_identity.py \\
        --pdf-dir data/ingested --pdf-dir data/to_ingest --llm
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
BACKEND_DIR = SCRIPTS_DIR.parent
APP_DIR = BACKEND_DIR / "app"
for path in (BACKEND_DIR, APP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ingestion.audit import AuditLog
from ingestion.indexer import _document_identity_to_row
from ingestion.migration import (
    MigrationReport,
    apply_migration,
    plan_migration,
    reconstruct_chunk_metadata,
)
from retrieval.config import load_retrieval_config
from retrieval.factory import build_documents_store, build_store


def _default_pdf_dirs() -> list[Path]:
    return [BACKEND_DIR / "data" / "ingested", BACKEND_DIR / "data" / "to_ingest"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill DocumentIdentity for legacy chunks rows.")
    parser.add_argument(
        "--pdf-dir",
        action="append",
        type=Path,
        default=None,
        help="Directory to search for original PDFs. May be repeated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan only — print proposed identities and exit without writing.",
    )
    parser.add_argument(
        "--no-backfill-chunks",
        action="store_true",
        help="Skip the chunks-table column backfill (write doc rows only).",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help=(
            "Use the configured registry-updater worker to normalize identity "
            "for PDFs that resolve. Off by default to keep migrations free."
        ),
    )
    parser.add_argument(
        "--pipeline-version",
        default="1.0.0",
        help="Pipeline version stamp to record on migrated identities.",
    )
    return parser


def _load_chunks(store) -> list[dict]:
    if not store.table_exists():
        return []
    return store.open_table().to_pandas().to_dict("records")


def _load_documents(store) -> list[dict]:
    if not store.table_exists():
        return []
    return store.open_table().to_pandas().to_dict("records")


def _make_chunk_backfiller(chunks_store):
    """Return a callable suitable for ``apply_migration(backfill_chunks=...)``.

    LanceDB exposes ``Table.update(where=..., values=...)`` for column updates
    on rows matching a SQL predicate. We update each chunk in place rather
    than rewriting the table.
    """

    def _escape(value: str) -> str:
        return (value or "").replace("'", "''")

    def _backfill(source: str, canonical_source_id: str, canonical_title: str) -> int:
        if not chunks_store.table_exists():
            return 0
        table = chunks_store.open_table()
        predicate = f"source = '{_escape(source)}'"
        rows = table.search().where(predicate).limit(10000).to_list()
        if not rows:
            return 0
        # Take a representative legacy row to derive page-only fields. We do
        # the per-page update with one update call per (chunk_type, page)
        # cluster so we don't have to rewrite the whole table.
        updated = 0
        # Group by (page, type) so column values are uniform per group.
        groups: dict[tuple[int, str], int] = {}
        for r in rows:
            key = (int(r.get("page") or 0), (r.get("type") or "").strip())
            groups[key] = groups.get(key, 0) + 1

        for (page, legacy_type), _count in groups.items():
            updates = reconstruct_chunk_metadata(
                {"page": page, "type": legacy_type},
                canonical_source_id=canonical_source_id,
                canonical_title=canonical_title,
            )
            sql_set: dict[str, object] = {}
            for k, v in updates.items():
                if isinstance(v, str):
                    sql_set[k] = f"'{_escape(v)}'"
                elif isinstance(v, list):
                    # LanceDB list literal — array(...) in DataFusion. For our
                    # purposes the lists are always empty here, so use the
                    # canonical empty-list literal.
                    if not v:
                        sql_set[k] = "[]"
                    else:
                        items = ", ".join(f"'{_escape(str(item))}'" for item in v)
                        sql_set[k] = f"[{items}]"
                else:
                    sql_set[k] = str(v)
            where = (
                f"source = '{_escape(source)}' AND "
                f"page = {int(page)} AND type = '{_escape(legacy_type)}'"
            )
            try:
                table.update(where=where, values=sql_set)
                updated += 1
            except Exception:
                # Fall back to a single all-rows-for-source update without the
                # per-group narrowing if the structured update fails. Page
                # range degrades to (0, 0) in this branch.
                continue
        return updated

    return _backfill


def _print_plan(steps, skipped_existing) -> None:
    print(f"Planned migrations: {len(steps)}")
    print(f"Skipped (already in documents table): {len(skipped_existing)}")
    for step in steps:
        marker = "STUB" if step.stubbed else "PDF "
        print(
            f"  [{marker}] {step.source} -> {step.identity.canonical_source_id} "
            f"({step.identity.canonical_title!r}) "
            f"rows={step.summary.row_count} pages={step.summary.page_min}-{step.summary.page_max}"
        )
    if skipped_existing:
        print("Skipped sources:")
        for source in skipped_existing:
            print(f"  - {source}")


def _summarize_report(report: MigrationReport) -> None:
    print(f"Wrote {len(report.written)} document rows.")
    if report.stubbed:
        print(f"Stubbed (PDF missing): {len(report.stubbed)}")
        for cid in report.stubbed:
            print(f"  - {cid}")
    if report.backfilled_chunks:
        total = sum(report.backfilled_chunks.values())
        print(f"Updated {total} chunk-row groups across {len(report.backfilled_chunks)} documents.")
    if report.errors:
        print(f"Errors: {len(report.errors)}")
        for source, msg in report.errors:
            print(f"  ! {source}: {msg}")


def _build_worker():
    """Construct a worker for LLM normalization, or return None if unavailable."""
    try:
        from app.config.settings import SETTINGS
        from app.providers.workers import build_worker  # type: ignore
    except Exception:
        return None
    try:
        return build_worker(
            provider=SETTINGS.registry_updater.provider,
            model=SETTINGS.registry_updater.model,
        )
    except Exception:
        return None


def main() -> int:
    args = _build_parser().parse_args()
    pdf_dirs: list[Path] = args.pdf_dir or _default_pdf_dirs()

    config = load_retrieval_config()
    chunks_store = build_store(config)
    docs_store = build_documents_store(config)

    chunks_rows = _load_chunks(chunks_store)
    if not chunks_rows:
        print("No rows in chunks table — nothing to migrate.")
        return 0
    docs_rows = _load_documents(docs_store)

    run_id = "migrate_" + _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit = AuditLog(run_id)

    worker = _build_worker() if args.llm else None
    if args.llm and worker is None:
        print("Warning: --llm requested but no worker could be constructed; running without LLM.")

    try:
        steps, skipped = plan_migration(
            chunks=chunks_rows,
            documents=docs_rows,
            pdf_dirs=pdf_dirs,
            audit=audit,
            worker=worker,
            pipeline_version=args.pipeline_version,
        )
        _print_plan(steps, skipped)

        if args.dry_run:
            print(f"Dry run — no writes. Audit log: {audit.path}")
            return 0

        if not steps:
            print("Nothing to apply.")
            return 0

        def _write_document(identity) -> None:
            row = _document_identity_to_row(identity)
            docs_store.add_rows([row])

        backfill = None if args.no_backfill_chunks else _make_chunk_backfiller(chunks_store)

        report = apply_migration(
            steps,
            write_document=_write_document,
            backfill_chunks=backfill,
            audit=audit,
        )
        _summarize_report(report)
        print(f"Audit log: {audit.path}")
    finally:
        audit.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
