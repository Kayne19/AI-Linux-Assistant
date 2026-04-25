"""Tests for ingestion/identity/resolver.py"""

import json
from pathlib import Path

import pytest

from ingestion.audit import AuditLog
from ingestion.identity.resolver import (
    LayerContribution,
    build_canonical_source_id,
    resolve_identity,
)
from ingestion.identity.schema import DocumentIdentity
from ingestion.identity.vocabularies import IngestSourceType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PIPELINE_VERSION = "1.0.0"

_EMPTY_PDF_INFO = {
    "title": "",
    "subject": "",
    "author": "",
    "producer": "",
    "creator": "",
    "keywords": "",
}

_EMPTY_HEURISTICS = {
    "filename": "test.pdf",
    "stem": "test",
    "metadata": {"title": "", "subject": "", "author": "", "producer": ""},
    "front_matter_samples": [],
    "heading_candidates": [],
    "version_detected": None,
    "vendors_detected": [],
    "outline_summary": {"top_title": None, "depth": 0, "entry_count": 0},
}


def _make_pdf_path(tmp_path: Path, name: str = "test.pdf") -> Path:
    p = tmp_path / name
    p.touch()
    return p


def _call_resolve(
    pdf_path: Path,
    *,
    sidecar=None,
    pdf_info=None,
    heuristic_signals=None,
    llm_fields=None,
    audit=None,
) -> tuple[DocumentIdentity, list[LayerContribution]]:
    return resolve_identity(
        pdf_path=pdf_path,
        sidecar=sidecar,
        pdf_info=pdf_info or _EMPTY_PDF_INFO,
        heuristic_signals=heuristic_signals or _EMPTY_HEURISTICS,
        llm_fields=llm_fields or {},
        pipeline_version=_PIPELINE_VERSION,
        audit=audit,
    )


# ---------------------------------------------------------------------------
# Precedence tests
# ---------------------------------------------------------------------------

class TestLayerPrecedence:
    def test_sidecar_overrides_all(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        sidecar = {"canonical_title": "Sidecar Title", "doc_kind": "admin_guide"}
        heuristics = dict(_EMPTY_HEURISTICS, metadata={"title": "Heuristic Title", "subject": "", "author": "", "producer": ""})
        pdf_info = dict(_EMPTY_PDF_INFO, title="PDF Meta Title")
        llm_fields = {"canonical_title": "LLM Title"}

        identity, _ = resolve_identity(
            pdf_path=pdf,
            sidecar=sidecar,
            pdf_info=pdf_info,
            heuristic_signals=heuristics,
            llm_fields=llm_fields,
            pipeline_version=_PIPELINE_VERSION,
        )
        assert identity.canonical_title == "Sidecar Title"

    def test_heuristics_overrides_pdf_meta_and_llm(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        heuristics = dict(_EMPTY_HEURISTICS, metadata={"title": "Heuristic Title", "subject": "", "author": "", "producer": ""})
        pdf_info = dict(_EMPTY_PDF_INFO, title="PDF Meta Title")
        llm_fields = {"canonical_title": "LLM Title"}

        identity, _ = resolve_identity(
            pdf_path=pdf,
            sidecar=None,
            pdf_info=pdf_info,
            heuristic_signals=heuristics,
            llm_fields=llm_fields,
            pipeline_version=_PIPELINE_VERSION,
        )
        assert identity.canonical_title == "Heuristic Title"

    def test_llm_fills_what_higher_layers_left_empty(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        llm_fields = {
            "canonical_title": "LLM Title",
            "doc_kind": "tutorial",
            "trust_tier": "community",
        }

        identity, _ = resolve_identity(
            pdf_path=pdf,
            sidecar=None,
            pdf_info=_EMPTY_PDF_INFO,
            heuristic_signals=_EMPTY_HEURISTICS,
            llm_fields=llm_fields,
            pipeline_version=_PIPELINE_VERSION,
        )
        assert identity.canonical_title == "LLM Title"
        assert identity.doc_kind == "tutorial"
        assert identity.trust_tier == "community"

    def test_llm_cannot_override_sidecar(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        sidecar = {"doc_kind": "admin_guide"}
        llm_fields = {"doc_kind": "tutorial"}

        identity, _ = resolve_identity(
            pdf_path=pdf,
            sidecar=sidecar,
            pdf_info=_EMPTY_PDF_INFO,
            heuristic_signals=_EMPTY_HEURISTICS,
            llm_fields=llm_fields,
            pipeline_version=_PIPELINE_VERSION,
        )
        assert identity.doc_kind == "admin_guide"

    def test_pdf_meta_overrides_llm(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        pdf_info = dict(_EMPTY_PDF_INFO, title="PDF Meta Title")
        llm_fields = {"canonical_title": "LLM Title"}

        identity, _ = resolve_identity(
            pdf_path=pdf,
            sidecar=None,
            pdf_info=pdf_info,
            heuristic_signals=_EMPTY_HEURISTICS,
            llm_fields=llm_fields,
            pipeline_version=_PIPELINE_VERSION,
        )
        assert identity.canonical_title == "PDF Meta Title"


# ---------------------------------------------------------------------------
# Enum coercion tests
# ---------------------------------------------------------------------------

class TestEnumCoercion:
    def test_invalid_enum_value_becomes_unknown(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        sidecar = {"source_family": "totally_invalid_family"}
        identity, _ = _call_resolve(pdf, sidecar=sidecar)
        assert identity.source_family == "unknown"

    def test_invalid_enum_logged_in_audit(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        sidecar = {"source_family": "totally_invalid_family"}
        audit = AuditLog("test-coerce", traces_dir=tmp_path / "traces")
        _call_resolve(pdf, sidecar=sidecar, audit=audit)
        audit.close()

        lines = [json.loads(l) for l in audit.path.read_text().splitlines() if l.strip()]
        fallback_actions = [l for l in lines if l["action"] == "enum_coercion_fallback"]
        assert any(l["chosen"]["field"] == "source_family" for l in fallback_actions)

    def test_valid_enum_not_flagged(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        sidecar = {"source_family": "proxmox"}
        audit = AuditLog("test-valid-enum", traces_dir=tmp_path / "traces")
        identity, _ = _call_resolve(pdf, sidecar=sidecar, audit=audit)
        audit.close()
        assert identity.source_family == "proxmox"

    def test_heuristic_vendor_red_hat_resolves_to_rhel_source_family(self, tmp_path):
        # Regression: "red hat" doesn't directly match the SourceFamily enum
        # ("rhel"). Resolver must consult the vendor->family map.
        pdf = _make_pdf_path(tmp_path)
        heuristics = dict(_EMPTY_HEURISTICS, vendors_detected=["Red Hat"])
        identity, _ = _call_resolve(pdf, heuristic_signals=heuristics)
        assert identity.source_family == "rhel"

    def test_heuristic_vendor_canonical_resolves_to_ubuntu_source_family(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        heuristics = dict(_EMPTY_HEURISTICS, vendors_detected=["Canonical"])
        identity, _ = _call_resolve(pdf, heuristic_signals=heuristics)
        assert identity.source_family == "ubuntu"

    def test_heuristic_vendor_arch_linux_resolves_to_arch_source_family(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        heuristics = dict(_EMPTY_HEURISTICS, vendors_detected=["Arch Linux"])
        identity, _ = _call_resolve(pdf, heuristic_signals=heuristics)
        assert identity.source_family == "arch"


# ---------------------------------------------------------------------------
# build_canonical_source_id tests
# ---------------------------------------------------------------------------

class TestBuildCanonicalSourceId:
    def test_basic_slug(self):
        assert build_canonical_source_id("Proxmox VE Admin Guide") == "proxmox_ve_admin_guide"

    def test_empty_title_returns_unnamed(self):
        assert build_canonical_source_id("") == "unnamed"
        assert build_canonical_source_id(None) == "unnamed"  # type: ignore[arg-type]

    def test_version_appended_with_double_underscore(self):
        assert build_canonical_source_id("Proxmox VE", "8.1") == "proxmox_ve__8_1"

    def test_version_none_not_appended(self):
        assert build_canonical_source_id("Proxmox VE", None) == "proxmox_ve"

    def test_special_chars_normalized(self):
        slug = build_canonical_source_id("Hello World! (2024)")
        assert slug == "hello_world_2024"

    def test_leading_trailing_underscores_stripped(self):
        slug = build_canonical_source_id("  !!  hello  !!  ")
        assert not slug.startswith("_")
        assert not slug.endswith("_")


# ---------------------------------------------------------------------------
# operator_override_present tests
# ---------------------------------------------------------------------------

class TestOperatorOverridePresent:
    def test_true_when_sidecar_contributed(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        sidecar = {"canonical_title": "Sidecar Title"}
        identity, _ = _call_resolve(pdf, sidecar=sidecar)
        assert identity.operator_override_present is True

    def test_false_when_no_sidecar(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        identity, _ = _call_resolve(pdf, sidecar=None)
        assert identity.operator_override_present is False

    def test_false_when_sidecar_has_only_filtered_keys(self, tmp_path):
        """Sidecar present but its only key is excluded from the merge field set."""
        pdf = _make_pdf_path(tmp_path)
        # pipeline_version is stripped from all_fields in _merge_layers, so no
        # contribution from sidecar ever reaches contributions with accepted=True.
        sidecar = {"pipeline_version": "something"}
        identity, contributions = _call_resolve(pdf, sidecar=sidecar)
        assert identity.operator_override_present is False
        # Also confirm that no sidecar contribution was accepted
        accepted_sidecar = [c for c in contributions if c.layer == "sidecar" and c.accepted]
        assert accepted_sidecar == []


# ---------------------------------------------------------------------------
# ingest_source_type test
# ---------------------------------------------------------------------------

class TestIngestSourceType:
    def test_always_pdf_operator(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        identity, _ = _call_resolve(pdf, sidecar=None)
        assert identity.ingest_source_type == IngestSourceType.pdf_operator.value


# ---------------------------------------------------------------------------
# Audit record tests
# ---------------------------------------------------------------------------

class TestAuditRecords:
    def test_audit_records_accept_and_resolve_complete(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        audit = AuditLog("test-audit", traces_dir=tmp_path / "traces")
        sidecar = {"doc_kind": "admin_guide", "trust_tier": "official"}
        _call_resolve(pdf, sidecar=sidecar, audit=audit)
        audit.close()

        lines = [json.loads(l) for l in audit.path.read_text().splitlines() if l.strip()]
        actions = [l["action"] for l in lines]
        assert "accept" in actions
        assert "resolve_complete" in actions

    def test_running_without_audit_does_not_raise(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        identity, _ = _call_resolve(pdf, sidecar=None, audit=None)
        assert isinstance(identity, DocumentIdentity)

    def test_resolve_complete_has_canonical_source_id(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        audit = AuditLog("test-complete", traces_dir=tmp_path / "traces")
        _call_resolve(pdf, sidecar={"canonical_title": "Test Doc"}, audit=audit)
        audit.close()

        lines = [json.loads(l) for l in audit.path.read_text().splitlines() if l.strip()]
        complete = [l for l in lines if l["action"] == "resolve_complete"]
        assert len(complete) == 1
        assert "canonical_source_id" in complete[0]["chosen"]
        assert "canonical_title" in complete[0]["chosen"]


# ---------------------------------------------------------------------------
# LayerContribution tests
# ---------------------------------------------------------------------------

class TestLayerContributions:
    def test_non_winning_layers_have_accepted_false(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        sidecar = {"canonical_title": "Sidecar Title"}
        heuristics = dict(_EMPTY_HEURISTICS, metadata={"title": "Heuristic Title", "subject": "", "author": "", "producer": ""})
        pdf_info = dict(_EMPTY_PDF_INFO, title="PDF Meta Title")
        llm_fields = {"canonical_title": "LLM Title"}

        _, contributions = resolve_identity(
            pdf_path=pdf,
            sidecar=sidecar,
            pdf_info=pdf_info,
            heuristic_signals=heuristics,
            llm_fields=llm_fields,
            pipeline_version=_PIPELINE_VERSION,
        )

        ct_contribs = [c for c in contributions if c.field == "canonical_title"]
        winning = [c for c in ct_contribs if c.accepted]
        losing = [c for c in ct_contribs if not c.accepted]
        assert len(winning) == 1
        assert winning[0].layer == "sidecar"
        assert len(losing) > 0

    def test_contributions_are_layer_contribution_instances(self, tmp_path):
        pdf = _make_pdf_path(tmp_path)
        _, contributions = _call_resolve(pdf, sidecar={"doc_kind": "admin_guide"})
        assert all(isinstance(c, LayerContribution) for c in contributions)
