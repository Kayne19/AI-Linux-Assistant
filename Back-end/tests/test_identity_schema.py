import pytest
from ingestion.identity.vocabularies import (
    ALL_ENUMS,
    coerce_enum,
    coerce_enum_list,
    SourceFamily,
    DocKind,
    ChunkType,
)
from ingestion.identity.schema import DocumentIdentity, ChunkMetadata


# ---------------------------------------------------------------------------
# coerce_enum
# ---------------------------------------------------------------------------

def test_coerce_enum_valid_lowercase():
    assert coerce_enum("source_family", "debian") == "debian"


def test_coerce_enum_valid_uppercase():
    assert coerce_enum("source_family", "DEBIAN") == "debian"


def test_coerce_enum_valid_mixed_case():
    assert coerce_enum("doc_kind", "Admin_Guide") == "admin_guide"


def test_coerce_enum_hyphen_to_underscore():
    assert coerce_enum("source_family", "network-manager") == "network_manager"


def test_coerce_enum_space_to_underscore():
    assert coerce_enum("source_family", "linux generic") == "linux_generic"


def test_coerce_enum_invalid_returns_unknown():
    assert coerce_enum("source_family", "not_a_real_family") == "unknown"


def test_coerce_enum_none_returns_unknown():
    assert coerce_enum("source_family", None) == "unknown"


def test_coerce_enum_empty_string_returns_unknown():
    assert coerce_enum("source_family", "") == "unknown"


def test_coerce_enum_unknown_field_raises():
    with pytest.raises(KeyError):
        coerce_enum("not_a_field", "debian")


def test_coerce_enum_chunk_type_valid():
    assert coerce_enum("chunk_type", "CODE") == "code"


def test_coerce_enum_chunk_type_invalid():
    assert coerce_enum("chunk_type", "garbage") == "unknown"


# ---------------------------------------------------------------------------
# coerce_enum_list
# ---------------------------------------------------------------------------

def test_coerce_enum_list_valid_values():
    result = coerce_enum_list("init_systems", ["systemd", "openrc"])
    assert result == ["openrc", "systemd"]  # sorted


def test_coerce_enum_list_drops_unknowns_when_valid_present():
    result = coerce_enum_list("init_systems", ["systemd", "bad_value", None])
    assert "unknown" not in result
    assert "systemd" in result


def test_coerce_enum_list_all_unknown_returns_unknown():
    result = coerce_enum_list("init_systems", ["bad1", "bad2"])
    assert result == ["unknown"]


def test_coerce_enum_list_none_input_returns_unknown():
    assert coerce_enum_list("init_systems", None) == ["unknown"]


def test_coerce_enum_list_empty_list_returns_unknown():
    assert coerce_enum_list("package_managers", []) == ["unknown"]


def test_coerce_enum_list_deduplicates():
    result = coerce_enum_list("init_systems", ["systemd", "SYSTEMD", "systemd"])
    assert result == ["systemd"]


def test_coerce_enum_list_sorted():
    result = coerce_enum_list("package_managers", ["yum", "apt", "dnf"])
    assert result == ["apt", "dnf", "yum"]


# ---------------------------------------------------------------------------
# ALL_ENUMS covers every enum-typed field on DocumentIdentity
# ---------------------------------------------------------------------------

def test_all_enums_covers_scalar_fields():
    for f in ("source_family", "vendor_or_project", "doc_kind", "trust_tier",
              "freshness_status", "os_family", "ingest_source_type"):
        assert f in ALL_ENUMS, f"{f} missing from ALL_ENUMS"


def test_all_enums_covers_list_fields():
    for f in ("init_systems", "package_managers", "major_subsystems"):
        assert f in ALL_ENUMS, f"{f} missing from ALL_ENUMS"


def test_all_enums_covers_chunk_type():
    assert "chunk_type" in ALL_ENUMS


# ---------------------------------------------------------------------------
# DocumentIdentity round-trip
# ---------------------------------------------------------------------------

def _minimal_doc_dict():
    return {
        "canonical_source_id": "doc-001",
        "canonical_title": "Proxmox Admin Guide",
    }


def test_document_identity_from_dict_to_dict_round_trip():
    d = {
        "canonical_source_id": "doc-001",
        "canonical_title": "Proxmox Admin Guide",
        "source_family": "proxmox",
        "doc_kind": "admin_guide",
        "trust_tier": "official",
        "os_family": "linux",
        "init_systems": ["systemd"],
        "package_managers": ["apt"],
        "major_subsystems": ["virtualization", "storage"],
        "title_aliases": [],
        "product": None,
        "vendor_or_project": "proxmox_server_solutions",
        "version": "8.1",
        "release_date": "2024-01-15",
        "freshness_status": "current",
        "applies_to": ["proxmox"],
        "source_url": None,
        "ingest_source_type": "pdf_operator",
        "operator_override_present": False,
        "ingested_at": None,
        "pipeline_version": "1.0.0",
    }
    identity = DocumentIdentity.from_dict(d)
    result = identity.to_dict()
    assert result["canonical_source_id"] == "doc-001"
    assert result["source_family"] == "proxmox"
    assert result["doc_kind"] == "admin_guide"
    assert result["init_systems"] == ["systemd"]
    assert result["major_subsystems"] == ["storage", "virtualization"]  # sorted


def test_document_identity_from_dict_coerces_enum_fields():
    d = _minimal_doc_dict()
    d["source_family"] = "PROXMOX"
    d["doc_kind"] = "Admin_Guide"
    d["trust_tier"] = "OFFICIAL"
    identity = DocumentIdentity.from_dict(d)
    assert identity.source_family == "proxmox"
    assert identity.doc_kind == "admin_guide"
    assert identity.trust_tier == "official"


def test_document_identity_from_dict_invalid_enum_becomes_unknown():
    d = _minimal_doc_dict()
    d["source_family"] = "not_real"
    identity = DocumentIdentity.from_dict(d)
    assert identity.source_family == "unknown"


def test_document_identity_defaults():
    identity = DocumentIdentity(
        canonical_source_id="x", canonical_title="y"
    )
    assert identity.source_family == "other"
    assert identity.doc_kind == "other"
    assert identity.trust_tier == "unknown"
    assert identity.init_systems == ["unknown"]
    assert identity.package_managers == ["unknown"]
    assert identity.major_subsystems == []
    assert identity.pipeline_version == "1.0.0"


# ---------------------------------------------------------------------------
# DocumentIdentity.validate
# ---------------------------------------------------------------------------

def test_validate_passes_for_valid_identity():
    identity = DocumentIdentity(
        canonical_source_id="doc-001",
        canonical_title="My Doc",
        source_family="debian",
        doc_kind="manpage",
    )
    assert identity.validate() == []


def test_validate_fails_missing_canonical_source_id():
    identity = DocumentIdentity(canonical_source_id="", canonical_title="Title")
    errors = identity.validate()
    assert any("canonical_source_id" in e for e in errors)


def test_validate_fails_missing_canonical_title():
    identity = DocumentIdentity(canonical_source_id="x", canonical_title="")
    errors = identity.validate()
    assert any("canonical_title" in e for e in errors)


def test_validate_fails_invalid_source_family():
    identity = DocumentIdentity(
        canonical_source_id="x",
        canonical_title="y",
        source_family="bogus_os",
    )
    errors = identity.validate()
    assert any("source_family" in e for e in errors)


def test_validate_passes_unknown_enum_value():
    # "unknown" is always accepted without error
    identity = DocumentIdentity(
        canonical_source_id="x",
        canonical_title="y",
        trust_tier="unknown",
    )
    assert identity.validate() == []


def test_validate_fails_invalid_doc_kind():
    identity = DocumentIdentity(
        canonical_source_id="x",
        canonical_title="y",
        doc_kind="not_a_kind",
    )
    errors = identity.validate()
    assert any("doc_kind" in e for e in errors)


# ---------------------------------------------------------------------------
# ChunkMetadata.validate
# ---------------------------------------------------------------------------

def _minimal_chunk():
    return ChunkMetadata(
        canonical_source_id="doc-001",
        page_start=1,
        page_end=3,
    )


def test_chunk_metadata_validate_passes():
    assert _minimal_chunk().validate() == []


def test_chunk_metadata_page_end_equals_page_start_passes():
    c = ChunkMetadata(canonical_source_id="x", page_start=5, page_end=5)
    assert c.validate() == []


def test_chunk_metadata_page_end_less_than_page_start_fails():
    c = ChunkMetadata(canonical_source_id="x", page_start=5, page_end=3)
    errors = c.validate()
    assert any("page_end" in e for e in errors)


def test_chunk_metadata_missing_source_id_fails():
    c = ChunkMetadata(canonical_source_id="", page_start=1, page_end=2)
    errors = c.validate()
    assert any("canonical_source_id" in e for e in errors)


def test_chunk_metadata_invalid_chunk_type_fails():
    c = ChunkMetadata(
        canonical_source_id="x",
        page_start=1,
        page_end=2,
        chunk_type="totally_wrong",
    )
    errors = c.validate()
    assert any("chunk_type" in e for e in errors)


def test_chunk_metadata_valid_chunk_type_passes():
    c = ChunkMetadata(
        canonical_source_id="x",
        page_start=1,
        page_end=2,
        chunk_type="code",
    )
    assert c.validate() == []


def test_chunk_metadata_from_dict_to_dict_round_trip():
    d = {
        "canonical_source_id": "doc-001",
        "page_start": 1,
        "page_end": 4,
        "section_path": ["Introduction", "Overview"],
        "section_title": "Overview",
        "chunk_type": "narrative",
        "local_subsystems": [],
        "entities": {"commands": ["systemctl"], "paths": ["/etc/systemd"]},
        "applies_to_override": [],
    }
    chunk = ChunkMetadata.from_dict(d)
    result = chunk.to_dict()
    assert result["chunk_type"] == "narrative"
    assert result["entities"]["commands"] == ["systemctl"]
    assert result["page_end"] == 4


def test_chunk_metadata_from_dict_coerces_chunk_type():
    d = {
        "canonical_source_id": "x",
        "page_start": 0,
        "page_end": 1,
        "chunk_type": "CODE",
    }
    chunk = ChunkMetadata.from_dict(d)
    assert chunk.chunk_type == "code"
