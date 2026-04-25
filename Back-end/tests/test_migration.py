"""Tests for the T13 identity backfill migration.

Covers:
- collect_sources groups chunk rows by filename and tracks page range / types
- existing_canonical_ids picks up canonical_source_id from documents rows
- find_pdf finds files by exact filename and by stem fallback
- stub_identity_for_filename builds a usable identity from filename only
- reconstruct_chunk_metadata maps legacy ``type`` to ChunkType + populates pages
- plan_migration skips sources already in documents (idempotency)
- plan_migration delegates stubbing when PDF is absent
- apply_migration writes documents and backfills chunks via injected callables
"""

from pathlib import Path
from unittest.mock import MagicMock

from ingestion.migration import (
    MigrationReport,
    apply_migration,
    collect_sources,
    existing_canonical_ids,
    find_pdf,
    plan_migration,
    reconstruct_chunk_metadata,
    stub_identity_for_filename,
)


# ---------------------------------------------------------------------------
# collect_sources
# ---------------------------------------------------------------------------


def test_collect_sources_groups_rows_and_tracks_page_range():
    chunks = [
        {"source": "a.pdf", "page": 3, "type": "NarrativeText"},
        {"source": "a.pdf", "page": 5, "type": "ListItem"},
        {"source": "a.pdf", "page": 1, "type": "NarrativeText"},
        {"source": "b.pdf", "page": 7, "type": "Title"},
    ]
    summaries = collect_sources(chunks)
    assert [s.source for s in summaries] == ["a.pdf", "b.pdf"]
    a = next(s for s in summaries if s.source == "a.pdf")
    assert a.row_count == 3
    assert a.page_min == 1
    assert a.page_max == 5
    assert "NarrativeText" in a.sample_types
    assert "ListItem" in a.sample_types


def test_collect_sources_skips_rows_already_carrying_canonical_id():
    chunks = [
        {"source": "a.pdf", "page": 1, "type": "NarrativeText"},
        {"source": "b.pdf", "page": 1, "type": "NarrativeText", "canonical_source_id": "b_id"},
    ]
    summaries = collect_sources(chunks)
    assert [s.source for s in summaries] == ["a.pdf"]


def test_collect_sources_skips_blank_source():
    chunks = [
        {"source": "", "page": 1, "type": "NarrativeText"},
        {"source": None, "page": 1, "type": "NarrativeText"},
        {"source": "good.pdf", "page": 2, "type": "NarrativeText"},
    ]
    summaries = collect_sources(chunks)
    assert [s.source for s in summaries] == ["good.pdf"]


# ---------------------------------------------------------------------------
# existing_canonical_ids
# ---------------------------------------------------------------------------


def test_existing_canonical_ids_collects_non_empty_strings():
    docs = [
        {"canonical_source_id": "a"},
        {"canonical_source_id": ""},
        {"canonical_source_id": "b"},
        {"other": "ignored"},
    ]
    assert existing_canonical_ids(docs) == {"a", "b"}


# ---------------------------------------------------------------------------
# find_pdf
# ---------------------------------------------------------------------------


def test_find_pdf_exact_filename(tmp_path):
    (tmp_path / "DebianGuide.pdf").write_bytes(b"%PDF")
    found = find_pdf("DebianGuide.pdf", [tmp_path])
    assert found is not None
    assert found.name == "DebianGuide.pdf"


def test_find_pdf_stem_match_when_extension_differs(tmp_path):
    (tmp_path / "ProxmoxAdmin.pdf").write_bytes(b"%PDF")
    found = find_pdf("ProxmoxAdmin", [tmp_path])
    assert found is not None
    assert found.stem == "ProxmoxAdmin"


def test_find_pdf_returns_none_when_missing(tmp_path):
    assert find_pdf("missing.pdf", [tmp_path]) is None


def test_find_pdf_handles_missing_dir(tmp_path):
    assert find_pdf("anything.pdf", [tmp_path / "does_not_exist"]) is None


# ---------------------------------------------------------------------------
# stub_identity_for_filename
# ---------------------------------------------------------------------------


def test_stub_identity_makes_canonical_title_from_stem():
    identity = stub_identity_for_filename("Some_Doc-Name.pdf")
    assert identity.canonical_title == "Some Doc Name"
    assert identity.canonical_source_id  # nonempty
    assert identity.operator_override_present is False


def test_stub_identity_preserves_filename_in_aliases_when_distinct():
    identity = stub_identity_for_filename("Some_Doc-Name.pdf")
    assert "Some_Doc-Name" in identity.title_aliases


def test_stub_identity_audits_when_audit_provided():
    audit = MagicMock()
    identity = stub_identity_for_filename("a.pdf", audit=audit)
    audit.record.assert_called_once()
    kwargs = audit.record.call_args.kwargs
    assert kwargs["doc"] == "a.pdf"
    assert kwargs["phase"] == "migration"
    assert kwargs["action"] == "stub_from_filename"
    assert kwargs["chosen"]["canonical_source_id"] == identity.canonical_source_id


# ---------------------------------------------------------------------------
# reconstruct_chunk_metadata
# ---------------------------------------------------------------------------


def test_reconstruct_chunk_metadata_maps_type_to_chunk_type():
    legacy = {"page": 4, "type": "ListItem"}
    out = reconstruct_chunk_metadata(legacy, canonical_source_id="cid", canonical_title="Title")
    assert out["chunk_type"] == "list_item"
    assert out["page_start"] == 4
    assert out["page_end"] == 4
    assert out["canonical_source_id"] == "cid"
    assert out["source"] == "Title"
    assert out["section_path"] == []
    assert out["entities"] == "{}"


def test_reconstruct_chunk_metadata_unknown_type_falls_back_to_uncategorized():
    out = reconstruct_chunk_metadata(
        {"page": 0, "type": "BlobThing"}, canonical_source_id="x", canonical_title="t"
    )
    assert out["chunk_type"] == "uncategorized"


# ---------------------------------------------------------------------------
# plan_migration
# ---------------------------------------------------------------------------


def test_plan_migration_skips_sources_already_in_documents(tmp_path):
    # Stub identity for "a.pdf" gives a deterministic canonical_source_id.
    stub_id = stub_identity_for_filename("a.pdf").canonical_source_id

    chunks = [{"source": "a.pdf", "page": 1, "type": "NarrativeText"}]
    docs = [{"canonical_source_id": stub_id}]

    steps, skipped = plan_migration(
        chunks=chunks,
        documents=docs,
        pdf_dirs=[tmp_path],
    )
    assert steps == []
    assert skipped == ["a.pdf"]


def test_plan_migration_creates_stub_when_pdf_missing(tmp_path):
    chunks = [
        {"source": "missing.pdf", "page": 1, "type": "NarrativeText"},
        {"source": "missing.pdf", "page": 9, "type": "ListItem"},
    ]
    steps, skipped = plan_migration(chunks=chunks, documents=[], pdf_dirs=[tmp_path])
    assert skipped == []
    assert len(steps) == 1
    step = steps[0]
    assert step.stubbed is True
    assert step.pdf_path is None
    assert step.summary.page_max == 9


def test_plan_migration_returns_steps_in_source_order(tmp_path):
    chunks = [
        {"source": "z.pdf", "page": 1, "type": "NarrativeText"},
        {"source": "a.pdf", "page": 1, "type": "NarrativeText"},
        {"source": "m.pdf", "page": 1, "type": "NarrativeText"},
    ]
    steps, _ = plan_migration(chunks=chunks, documents=[], pdf_dirs=[tmp_path])
    assert [s.source for s in steps] == ["a.pdf", "m.pdf", "z.pdf"]


# ---------------------------------------------------------------------------
# apply_migration
# ---------------------------------------------------------------------------


def test_apply_migration_writes_documents_and_backfills_chunks(tmp_path):
    chunks = [{"source": "doc1.pdf", "page": 2, "type": "NarrativeText"}]
    steps, _ = plan_migration(chunks=chunks, documents=[], pdf_dirs=[tmp_path])
    assert len(steps) == 1

    written: list = []
    backfill_calls: list = []

    def _write(identity):
        written.append(identity.canonical_source_id)

    def _backfill(source, cid, title):
        backfill_calls.append((source, cid, title))
        return 1

    report = apply_migration(steps, write_document=_write, backfill_chunks=_backfill)
    assert isinstance(report, MigrationReport)
    assert report.written == [steps[0].identity.canonical_source_id]
    assert report.stubbed == [steps[0].identity.canonical_source_id]
    assert backfill_calls == [(
        "doc1.pdf",
        steps[0].identity.canonical_source_id,
        steps[0].identity.canonical_title,
    )]
    assert report.backfilled_chunks[steps[0].identity.canonical_source_id] == 1


def test_apply_migration_skips_chunks_when_backfill_callback_is_none(tmp_path):
    chunks = [{"source": "doc1.pdf", "page": 2, "type": "NarrativeText"}]
    steps, _ = plan_migration(chunks=chunks, documents=[], pdf_dirs=[tmp_path])

    written: list = []

    def _write(identity):
        written.append(identity.canonical_source_id)

    report = apply_migration(steps, write_document=_write, backfill_chunks=None)
    assert report.backfilled_chunks == {}
    assert report.written == written


def test_make_chunk_backfiller_passes_typed_values_to_lancedb_update(tmp_path):
    """Regression: the backfiller must pass typed Python values (not SQL
    expression strings) to ``Table.update``. LanceDB's ``values=`` kwarg
    treats the dict as raw values; SQL expressions belong on ``values_sql=``.
    """
    from scripts.ingest.migrate_identity import _make_chunk_backfiller

    # Fake chain: chunks_store -> open_table() -> search().where().limit().to_list()
    # plus open_table().update(where=..., values=...).
    captured: dict = {}

    class _FakeSearch:
        def where(self, _predicate):
            return self
        def limit(self, _n):
            return self
        def to_list(self):
            return [{"page": 3, "type": "NarrativeText"}]

    class _FakeTable:
        def search(self):
            return _FakeSearch()
        def update(self, where, values):
            captured["where"] = where
            captured["values"] = values

    class _FakeStore:
        def table_exists(self):
            return True
        def open_table(self):
            return _FakeTable()

    backfill = _make_chunk_backfiller(_FakeStore())
    n = backfill("legacy.pdf", "cid", "Canonical Title")
    assert n == 1
    values = captured["values"]
    # Must be a dict of native Python types, not SQL strings:
    assert values["canonical_source_id"] == "cid"
    assert values["source"] == "Canonical Title"
    assert values["section_path"] == []
    assert values["page_start"] == 3
    assert values["page_end"] == 3
    assert values["chunk_type"] == "narrative"
    assert values["entities"] == "{}"
    # No raw SQL quoting must leak into the values:
    assert "'cid'" not in str(values["canonical_source_id"])


def test_apply_migration_records_write_errors(tmp_path):
    chunks = [{"source": "doc1.pdf", "page": 2, "type": "NarrativeText"}]
    steps, _ = plan_migration(chunks=chunks, documents=[], pdf_dirs=[tmp_path])

    def _write(identity):
        raise RuntimeError("disk full")

    report = apply_migration(steps, write_document=_write, backfill_chunks=None)
    assert report.written == []
    assert len(report.errors) == 1
    assert "disk full" in report.errors[0][1]
