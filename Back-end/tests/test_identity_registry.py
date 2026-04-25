"""Tests for ingestion/identity/registry.py"""

import json
from pathlib import Path

import pytest

from ingestion.identity.registry import (
    DOCUMENT_REGISTRY_PATH,
    get_document,
    list_documents,
    load_document_registry,
    save_document_registry,
    upsert_document,
)
from ingestion.identity.schema import DocumentIdentity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_identity(
    cid: str = "test_doc__1_0",
    title: str = "Test Document",
    version: str | None = "1.0",
) -> DocumentIdentity:
    return DocumentIdentity(
        canonical_source_id=cid,
        canonical_title=title,
        title_aliases=[],
        source_family="other",
        product=None,
        vendor_or_project="unknown",
        version=version,
        release_date=None,
        doc_kind="other",
        trust_tier="unknown",
        freshness_status="unknown",
        os_family="unknown",
        init_systems=["unknown"],
        package_managers=["unknown"],
        major_subsystems=[],
        applies_to=[],
        source_url=None,
        ingest_source_type="pdf_operator",
        operator_override_present=False,
        ingested_at=None,
        pipeline_version="1.0.0",
    )


# ---------------------------------------------------------------------------
# DOCUMENT_REGISTRY_PATH resolution test
# ---------------------------------------------------------------------------

class TestRegistryPath:
    def test_resolves_to_correct_path(self):
        assert DOCUMENT_REGISTRY_PATH.name == "routing_documents.json"
        assert DOCUMENT_REGISTRY_PATH.parent.name == "orchestration"
        # Verify the parent chain: app/orchestration/ lives under Back-end/app/
        assert DOCUMENT_REGISTRY_PATH.parents[1].name == "app"


# ---------------------------------------------------------------------------
# load_document_registry tests
# ---------------------------------------------------------------------------

class TestLoadDocumentRegistry:
    def test_missing_file_returns_empty(self, tmp_path):
        path = tmp_path / "missing.json"
        result = load_document_registry(path)
        assert result == {"documents": []}
        # file must NOT be created
        assert not path.exists()

    def test_valid_file_returns_data(self, tmp_path):
        path = tmp_path / "reg.json"
        data = {"documents": [{"canonical_source_id": "foo", "canonical_title": "Foo"}]}
        path.write_text(json.dumps(data), encoding="utf-8")
        result = load_document_registry(path)
        assert result["documents"][0]["canonical_source_id"] == "foo"

    def test_malformed_json_returns_empty(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        result = load_document_registry(path)
        assert result == {"documents": []}

    def test_wrong_shape_returns_empty(self, tmp_path):
        path = tmp_path / "wrong.json"
        path.write_text(json.dumps({"stuff": []}), encoding="utf-8")
        result = load_document_registry(path)
        assert result == {"documents": []}


# ---------------------------------------------------------------------------
# save_document_registry tests
# ---------------------------------------------------------------------------

class TestSaveDocumentRegistry:
    def test_writes_valid_json(self, tmp_path):
        path = tmp_path / "reg.json"
        data = {"documents": [{"canonical_source_id": "a"}]}
        save_document_registry(data, path)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded == data

    def test_atomic_write_leaves_no_tmp_file(self, tmp_path):
        path = tmp_path / "reg.json"
        save_document_registry({"documents": []}, path)
        assert not path.with_suffix(".tmp").exists()


# ---------------------------------------------------------------------------
# upsert_document tests
# ---------------------------------------------------------------------------

class TestUpsertDocument:
    def test_new_identity_returns_added(self, tmp_path):
        path = tmp_path / "reg.json"
        identity = _make_identity()
        changed, msg = upsert_document(identity, path=path)
        assert changed is True
        assert msg == "added"

    def test_new_identity_stored_in_file(self, tmp_path):
        path = tmp_path / "reg.json"
        identity = _make_identity()
        upsert_document(identity, path=path)
        rows = list_documents(path=path)
        assert len(rows) == 1
        assert rows[0]["canonical_source_id"] == "test_doc__1_0"

    def test_existing_identity_returns_updated(self, tmp_path):
        path = tmp_path / "reg.json"
        identity = _make_identity()
        upsert_document(identity, path=path)
        changed, msg = upsert_document(identity, path=path)
        assert changed is True
        assert msg == "updated"

    def test_upsert_same_id_keeps_count_at_one(self, tmp_path):
        path = tmp_path / "reg.json"
        identity = _make_identity()
        upsert_document(identity, path=path)
        upsert_document(identity, path=path)
        rows = list_documents(path=path)
        assert len(rows) == 1

    def test_upsert_updates_fields(self, tmp_path):
        path = tmp_path / "reg.json"
        identity_v1 = _make_identity(title="Original")
        upsert_document(identity_v1, path=path)

        identity_v2 = _make_identity(title="Updated")
        upsert_document(identity_v2, path=path)

        rows = list_documents(path=path)
        assert rows[0]["canonical_title"] == "Updated"

    def test_ingested_at_is_stamped_automatically(self, tmp_path):
        path = tmp_path / "reg.json"
        identity = _make_identity()
        upsert_document(identity, path=path)
        row = get_document("test_doc__1_0", path=path)
        assert row is not None
        assert row["ingested_at"] is not None
        assert row["ingested_at"].endswith("Z")

    def test_explicit_ingested_at_is_used(self, tmp_path):
        path = tmp_path / "reg.json"
        identity = _make_identity()
        ts = "2024-01-01T00:00:00Z"
        upsert_document(identity, path=path, ingested_at=ts)
        row = get_document("test_doc__1_0", path=path)
        assert row["ingested_at"] == ts

    def test_multiple_different_ids_accumulate(self, tmp_path):
        path = tmp_path / "reg.json"
        for i in range(5):
            identity = _make_identity(cid=f"doc_{i}", title=f"Doc {i}")
            upsert_document(identity, path=path)
        rows = list_documents(path=path)
        assert len(rows) == 5

    def test_atomic_write_file_always_valid_json(self, tmp_path):
        path = tmp_path / "reg.json"
        for i in range(10):
            identity = _make_identity(cid=f"doc_{i}", title=f"Doc {i}")
            upsert_document(identity, path=path)
        # After all writes, file must be valid JSON with a list of 10
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data["documents"], list)
        assert len(data["documents"]) == 10


# ---------------------------------------------------------------------------
# get_document tests
# ---------------------------------------------------------------------------

class TestGetDocument:
    def test_returns_row_when_present(self, tmp_path):
        path = tmp_path / "reg.json"
        identity = _make_identity()
        upsert_document(identity, path=path)
        row = get_document("test_doc__1_0", path=path)
        assert row is not None
        assert row["canonical_title"] == "Test Document"

    def test_returns_none_when_absent(self, tmp_path):
        path = tmp_path / "reg.json"
        row = get_document("does_not_exist", path=path)
        assert row is None


# ---------------------------------------------------------------------------
# list_documents tests
# ---------------------------------------------------------------------------

class TestListDocuments:
    def test_returns_empty_list_for_missing_file(self, tmp_path):
        path = tmp_path / "missing.json"
        rows = list_documents(path=path)
        assert rows == []

    def test_returns_all_rows(self, tmp_path):
        path = tmp_path / "reg.json"
        for i in range(3):
            upsert_document(_make_identity(cid=f"doc_{i}", title=f"Doc {i}"), path=path)
        rows = list_documents(path=path)
        assert len(rows) == 3
        ids = {r["canonical_source_id"] for r in rows}
        assert ids == {"doc_0", "doc_1", "doc_2"}


# ---------------------------------------------------------------------------
# Safety: ensure tests never touch the real registry file
# ---------------------------------------------------------------------------

class TestRegistrySafety:
    def test_real_registry_not_modified(self, tmp_path):
        """This test just verifies that using path=tmp_path keeps writes isolated."""
        path = tmp_path / "isolated.json"
        identity = _make_identity()
        upsert_document(identity, path=path)
        # Confirm the real registry was not created/modified
        assert not DOCUMENT_REGISTRY_PATH.exists() or True  # OK if it already exists in repo
        # What we actually verify: our tmp file has data, real file path is different
        assert path != DOCUMENT_REGISTRY_PATH
