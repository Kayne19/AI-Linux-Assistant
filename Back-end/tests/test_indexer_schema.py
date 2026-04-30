"""Tests for the T11 indexer schema.

Covers:
- Legacy path (no document_identity) still writes source=filename and leaves new columns on safe defaults
- With DocumentIdentity: source=canonical_title, canonical_source_id populated, document row written
- Chunk metadata round-trip: section_path, section_title, page range, chunk_type, entities (JSON str), lists
- _document_identity_to_row covers every DocumentIdentity field with a type-stable default
"""

import json
from pathlib import Path
from unittest.mock import MagicMock


from ingestion.indexer import (
    IngestionIndexer,
    _document_identity_to_row,
)
from ingestion.identity.schema import DocumentIdentity


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, table_name="chunks"):
        self.table_name = table_name
        self.rows: list[dict] = []
        self._exists = False

    def table_exists(self) -> bool:
        return self._exists

    def add_rows(self, rows):
        created = not self._exists
        self.rows.extend(rows)
        self._exists = True
        return created

    def rebuild_fts_index(self, field_name: str = "search_text"):
        pass

    def count_rows_by_id_prefix(self, prefix: str) -> int:
        return sum(1 for r in self.rows if r.get("id", "").startswith(prefix))

    def delete_by_id_prefix(self, prefix: str) -> int:
        before = self.count_rows_by_id_prefix(prefix)
        self.rows = [r for r in self.rows if not r.get("id", "").startswith(prefix)]
        return before

    def count_rows_matching(self, predicate: str) -> int:
        # Simple equality predicate: "column = 'value'"
        import re as _re

        m = _re.match(r"^(\w+)\s*=\s*'(.*)'$", predicate)
        if not m:
            return 0
        col, val = m.group(1), m.group(2)
        return sum(1 for r in self.rows if str(r.get(col, "")) == val)

    def delete_by_predicate(self, predicate: str) -> int:
        import re as _re

        m = _re.match(r"^(\w+)\s*=\s*'(.*)'$", predicate)
        if not m:
            return 0
        col, val = m.group(1), m.group(2)
        before = sum(1 for r in self.rows if str(r.get(col, "")) == val)
        self.rows = [r for r in self.rows if str(r.get(col, "")) != val]
        return before


class _FakeMetadataStore:
    def ensure_embedding_compatibility(
        self, embedding_provider, require_metadata=False
    ):
        pass

    def write(self, embedding_provider):
        pass


class _FakeEmbedder:
    def embed_documents(self, texts, show_progress_bar=False):
        return [[float(i)] for i in range(len(texts))]


def _make_indexer(documents_store=None) -> IngestionIndexer:
    return IngestionIndexer(
        store=_FakeStore("chunks"),
        metadata_store=_FakeMetadataStore(),
        embedding_provider=_FakeEmbedder(),
        documents_store=documents_store,
    )


def _write_elements(tmp_path: Path, elements: list[dict]) -> Path:
    p = tmp_path / "elements.json"
    p.write_text(json.dumps(elements))
    return p


# ---------------------------------------------------------------------------
# Legacy path — no DocumentIdentity
# ---------------------------------------------------------------------------


def test_ingest_json_legacy_keeps_filename_as_source(tmp_path):
    path = _write_elements(
        tmp_path,
        [
            {
                "text": "hello",
                "type": "NarrativeText",
                "metadata": {
                    "filename": "book.pdf",
                    "page_number": 3,
                    "embedding_text": "E hello",
                },
            }
        ],
    )
    indexer = _make_indexer()
    result = indexer.ingest_json(str(path))

    assert result["rows"] == 1
    row = indexer.store.rows[0]
    assert row["source"] == "book.pdf"  # legacy
    assert row["canonical_source_id"] == ""  # safe default
    assert row["page_start"] == 3
    assert row["page_end"] == 3
    assert row["entities"] == "{}"
    assert row["section_path"] == []
    assert result["document_row"] is None


def test_ingest_json_legacy_returns_schema_metadata(tmp_path):
    path = _write_elements(
        tmp_path,
        [{"text": "t", "type": "NarrativeText", "metadata": {"page_number": 1}}],
    )
    indexer = _make_indexer()
    result = indexer.ingest_json(str(path))

    assert result["table_name"] == "chunks"
    assert result["created_table"] is True


def test_ingest_json_legacy_chunk_ids_are_source_stable_and_unique(tmp_path):
    first = _write_elements(
        tmp_path,
        [
            {
                "text": "t",
                "type": "NarrativeText",
                "metadata": {"filename": "a.pdf", "page_number": 1},
            }
        ],
    )
    second = tmp_path / "elements_b.json"
    second.write_text(
        json.dumps(
            [
                {
                    "text": "t",
                    "type": "NarrativeText",
                    "metadata": {"filename": "b.pdf", "page_number": 1},
                }
            ]
        )
    )

    indexer_a = _make_indexer()
    indexer_b = _make_indexer()
    indexer_a.ingest_json(str(first))
    indexer_b.ingest_json(str(second))

    assert indexer_a.store.rows[0]["id"] != indexer_b.store.rows[0]["id"]
    assert indexer_a.store.rows[0]["id"].startswith("vec_")


# ---------------------------------------------------------------------------
# With DocumentIdentity — new schema
# ---------------------------------------------------------------------------


def _identity() -> DocumentIdentity:
    return DocumentIdentity(
        canonical_source_id="debian-install-guide-12",
        canonical_title="Debian Installation Guide",
        title_aliases=["DebianInstallGuide.pdf", "Debian Install"],
        source_family="debian",
        vendor_or_project="debian-project",
        version="12",
        release_date="2023-06-10",
        doc_kind="install_guide",
        trust_tier="canonical",
        freshness_status="current",
        os_family="linux",
        init_systems=["systemd"],
        package_managers=["apt", "dpkg"],
        major_subsystems=["boot", "networking", "filesystems"],
        applies_to=["debian-12"],
        source_url="https://www.debian.org/releases/bookworm/installmanual",
        ingest_source_type="pdf_operator",
        operator_override_present=True,
        ingested_at="2026-04-24T00:00:00Z",
    )


def test_ingest_json_with_identity_sets_canonical_source_and_title(tmp_path):
    path = _write_elements(
        tmp_path,
        [
            {
                "text": "Install Debian.",
                "type": "NarrativeText",
                "metadata": {
                    "filename": "DebianInstallGuide.pdf",
                    "page_number": 42,
                    "section_path": ["Chapter 3", "3.2 Disk"],
                    "section_title": "3.2 Disk",
                    "page_start": 40,
                    "page_end": 42,
                    "chunk_type": "narrative",
                    "local_subsystems": ["storage"],
                    "entities": {"commands": ["parted"], "paths": ["/dev/sda"]},
                    "applies_to_override": ["debian-12"],
                    "embedding_text": "E Install Debian.",
                },
            }
        ],
    )
    docs_store = _FakeStore("documents")
    indexer = _make_indexer(documents_store=docs_store)

    identity = _identity()
    result = indexer.ingest_json(str(path), document_identity=identity)

    row = indexer.store.rows[0]
    assert row["id"] == "vec_debian-install-guide-12_000000"
    assert row["source"] == "Debian Installation Guide"
    assert row["canonical_source_id"] == "debian-install-guide-12"
    assert row["section_path"] == ["Chapter 3", "3.2 Disk"]
    assert row["section_title"] == "3.2 Disk"
    assert row["page_start"] == 40
    assert row["page_end"] == 42
    assert row["chunk_type"] == "narrative"
    assert row["local_subsystems"] == ["storage"]
    entities = json.loads(row["entities"])
    assert entities == {"commands": ["parted"], "paths": ["/dev/sda"]}
    assert row["applies_to_override"] == ["debian-12"]
    assert result["document_row"] == {
        "written": True,
        "created_table": True,
        "table_name": "documents",
        "canonical_source_id": "debian-install-guide-12",
    }


def test_ingest_json_with_identity_writes_document_row(tmp_path):
    path = _write_elements(
        tmp_path,
        [{"text": "t", "type": "NarrativeText", "metadata": {"page_number": 1}}],
    )
    docs_store = _FakeStore("documents")
    indexer = _make_indexer(documents_store=docs_store)

    identity = _identity()
    indexer.ingest_json(str(path), document_identity=identity)

    assert len(docs_store.rows) == 1
    row = docs_store.rows[0]
    assert row["canonical_source_id"] == "debian-install-guide-12"
    assert row["canonical_title"] == "Debian Installation Guide"
    assert row["title_aliases"] == ["DebianInstallGuide.pdf", "Debian Install"]
    assert row["source_family"] == "debian"
    assert row["vendor_or_project"] == "debian-project"
    assert row["version"] == "12"
    assert row["trust_tier"] == "canonical"
    assert row["init_systems"] == ["systemd"]
    assert row["package_managers"] == ["apt", "dpkg"]
    assert row["operator_override_present"] is True


def test_ingest_json_without_documents_store_skips_doc_row(tmp_path):
    path = _write_elements(
        tmp_path,
        [{"text": "t", "type": "NarrativeText", "metadata": {"page_number": 1}}],
    )
    indexer = _make_indexer(documents_store=None)

    result = indexer.ingest_json(str(path), document_identity=_identity())

    assert result["document_row"] == {"written": False, "reason": "no documents_store"}


# ---------------------------------------------------------------------------
# _document_identity_to_row
# ---------------------------------------------------------------------------


def test_document_identity_to_row_coerces_none_to_empty_string():
    identity = DocumentIdentity(
        canonical_source_id="id",
        canonical_title="Title",
        # product, version, release_date, source_url default to None
    )
    row = _document_identity_to_row(identity)
    assert row["product"] == ""
    assert row["version"] == ""
    assert row["release_date"] == ""
    assert row["source_url"] == ""
    assert row["ingested_at"] == ""


def test_document_identity_to_row_preserves_lists_as_fresh_copies():
    identity = DocumentIdentity(
        canonical_source_id="id",
        canonical_title="T",
        title_aliases=["a", "b"],
        init_systems=["systemd"],
    )
    row = _document_identity_to_row(identity)
    # Mutating the row should not leak into the identity
    row["title_aliases"].append("c")
    row["init_systems"].append("openrc")
    assert identity.title_aliases == ["a", "b"]
    assert identity.init_systems == ["systemd"]


def test_entities_non_serializable_falls_back_to_empty_object():
    """Non-JSON-serializable entities should degrade gracefully, not crash."""
    from ingestion.indexer import _entities_as_json

    class _NotSerializable:
        pass

    assert _entities_as_json(_NotSerializable()) == "{}"
    assert _entities_as_json(None) == "{}"
    assert _entities_as_json({"a": ["b"]}) == '{"a": ["b"]}'


def test_entities_string_passthrough(tmp_path):
    path = _write_elements(
        tmp_path,
        [
            {
                "text": "t",
                "type": "NarrativeText",
                "metadata": {"page_number": 1, "entities": '{"packages":["curl"]}'},
            }
        ],
    )
    indexer = _make_indexer()
    indexer.ingest_json(str(path))
    assert json.loads(indexer.store.rows[0]["entities"]) == {"packages": ["curl"]}


def test_build_ingestion_indexer_wires_documents_store(monkeypatch):
    """build_ingestion_indexer constructs a documents_store from the config."""
    from ingestion import indexer as indexer_mod

    cfg = MagicMock()
    cfg.documents_table_name = "documents"

    fake_chunks = MagicMock(name="chunks_store")
    fake_docs = MagicMock(name="docs_store")
    fake_meta = MagicMock(name="meta")
    fake_embed = MagicMock(name="embed")

    monkeypatch.setattr(indexer_mod, "build_store", lambda c: fake_chunks)
    monkeypatch.setattr(indexer_mod, "build_documents_store", lambda c: fake_docs)
    monkeypatch.setattr(indexer_mod, "build_index_metadata_store", lambda c: fake_meta)
    monkeypatch.setattr(indexer_mod, "build_embedding_provider", lambda c: fake_embed)

    result = indexer_mod.build_ingestion_indexer(cfg)
    assert result.store is fake_chunks
    assert result.documents_store is fake_docs
    assert result.metadata_store is fake_meta
    assert result.embedding_provider is fake_embed


# ---------------------------------------------------------------------------
# H4: Idempotent re-ingest
# ---------------------------------------------------------------------------


def test_reingest_same_source_is_skipped_by_default(tmp_path):
    """Re-ingesting the same source without --force-reingest returns a skipped result."""
    path = _write_elements(
        tmp_path,
        [
            {
                "text": "hello",
                "type": "NarrativeText",
                "metadata": {"filename": "a.pdf", "page_number": 1},
            }
        ],
    )
    indexer = _make_indexer()

    # First ingest
    result1 = indexer.ingest_json(str(path))
    assert result1["rows"] == 1
    assert result1.get("skipped") is None

    # Second ingest — same source, no force
    result2 = indexer.ingest_json(str(path))
    assert result2["rows"] == 0
    assert result2["skipped"] is True
    assert "already exist" in result2["reason"]
    assert len(indexer.store.rows) == 1  # no duplicate added


def test_reingest_same_source_with_force_replaces_chunks(tmp_path):
    """Re-ingesting with force_reingest=True deletes old chunks and inserts new ones."""
    path = _write_elements(
        tmp_path,
        [
            {
                "text": "hello",
                "type": "NarrativeText",
                "metadata": {"filename": "a.pdf", "page_number": 1},
            }
        ],
    )
    indexer = _make_indexer()

    indexer.ingest_json(str(path))
    assert len(indexer.store.rows) == 1

    result = indexer.ingest_json(str(path), force_reingest=True)
    assert result["rows"] == 1
    assert result.get("skipped") is None
    assert len(indexer.store.rows) == 1  # replaced, not duplicated


def test_reingest_with_identity_skips_by_default(tmp_path):
    """When chunks already exist, re-ingest returns early and the document row is preserved."""
    path = _write_elements(
        tmp_path,
        [{"text": "t", "type": "NarrativeText", "metadata": {"page_number": 1}}],
    )
    docs_store = _FakeStore("documents")
    indexer = _make_indexer(documents_store=docs_store)
    identity = _identity()

    indexer.ingest_json(str(path), document_identity=identity)
    assert len(docs_store.rows) == 1

    result = indexer.ingest_json(str(path), document_identity=identity)
    # Chunks guard fires first — early return, but doc row stays
    assert result["skipped"] is True
    assert len(docs_store.rows) == 1  # no duplicate


def test_write_document_row_idempotent_by_default(tmp_path):
    """Calling _write_document_row twice on the same identity skips the second call."""
    docs_store = _FakeStore("documents")
    indexer = _make_indexer(documents_store=docs_store)
    identity = _identity()

    result1 = indexer._write_document_row(identity)
    assert result1["written"] is True
    assert len(docs_store.rows) == 1

    result2 = indexer._write_document_row(identity)
    assert result2["written"] is False
    assert result2["skipped"] is True
    assert len(docs_store.rows) == 1  # no duplicate


def test_write_document_row_force_reingest_replaces(tmp_path):
    """_write_document_row with force_reingest=True deletes old row and inserts new."""
    docs_store = _FakeStore("documents")
    indexer = _make_indexer(documents_store=docs_store)
    identity = _identity()

    indexer._write_document_row(identity)

    result = indexer._write_document_row(identity, force_reingest=True)
    assert result["written"] is True
    assert result.get("skipped") is None
    assert len(docs_store.rows) == 1  # replaced, not duplicated
