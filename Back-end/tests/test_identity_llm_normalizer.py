"""Tests for ingestion/identity/llm_normalizer.py"""

import json

import pytest

from ingestion.identity.llm_normalizer import (
    LLM_NORMALIZER_OUTPUT_SCHEMA,
    build_llm_context,
    normalize_with_llm,
)
from ingestion.identity.vocabularies import ALL_ENUMS


# ---------------------------------------------------------------------------
# Stub workers
# ---------------------------------------------------------------------------

class _GoodWorker:
    """Returns valid JSON matching the schema."""

    def __init__(self, payload: dict):
        self._payload = payload

    def generate_text(self, **kwargs):
        return json.dumps(self._payload)


class _GarbageWorker:
    """Returns non-JSON garbage."""

    def generate_text(self, **kwargs):
        return "not-json }{{"


class _EmptyWorker:
    """Returns empty string."""

    def generate_text(self, **kwargs):
        return ""


class _RaisingWorker:
    """Raises on generate_text."""

    def generate_text(self, **kwargs):
        raise RuntimeError("network error")


class _NoCacheConfigWorker:
    """Does not accept cache_config kwarg."""

    def __init__(self, payload: dict):
        self._payload = payload

    def generate_text(self, system_prompt, user_message, history, temperature,
                      structured_output, output_schema):
        return json.dumps(self._payload)


# ---------------------------------------------------------------------------
# Sample inputs
# ---------------------------------------------------------------------------

_FILENAME = "proxmox-ve-admin-guide.pdf"

_PDF_INFO = {
    "title": "Proxmox VE Administration Guide",
    "subject": "",
    "author": "Proxmox Server Solutions",
    "producer": "LaTeX",
    "creator": "",
    "keywords": "",
}

_HEURISTICS = {
    "filename": _FILENAME,
    "stem": "proxmox-ve-admin-guide",
    "metadata": {"title": "Proxmox VE Administration Guide", "subject": "", "author": "", "producer": ""},
    "front_matter_samples": ["Page one content " * 40],  # will be trimmed to 600 chars
    "heading_candidates": [f"Heading {i}" for i in range(20)],  # will be capped at 15
    "version_detected": "8.1",
    "vendors_detected": ["Proxmox"],
    "outline_summary": {"top_title": "Introduction", "depth": 3, "entry_count": 42},
}

_SIDECAR = {"doc_kind": "admin_guide", "trust_tier": "official"}

_MINIMAL_PAYLOAD = {
    "canonical_title": "Proxmox VE Administration Guide",
    "title_aliases": ["proxmox-ve-admin-guide"],
    "source_family": "proxmox",
    "product": "Proxmox VE",
    "vendor_or_project": "proxmox_server_solutions",
    "version": "8.1",
    "release_date": "2024-01-01",
    "doc_kind": "admin_guide",
    "trust_tier": "official",
    "freshness_status": "current",
    "os_family": "linux",
    "init_systems": ["systemd"],
    "package_managers": ["apt"],
    "major_subsystems": ["virtualization"],
    "applies_to": ["Proxmox VE 8.1"],
    "source_url": "",
    "rationale": "Matches Proxmox branding and admin guide structure.",
}


# ---------------------------------------------------------------------------
# build_llm_context tests
# ---------------------------------------------------------------------------

class TestBuildLlmContext:
    def test_contains_expected_tags(self):
        ctx = build_llm_context(
            filename=_FILENAME,
            pdf_info=_PDF_INFO,
            heuristic_signals=_HEURISTICS,
            sidecar=_SIDECAR,
        )
        for tag in ("<filename>", "<pdf_info>", "<heuristic_signals>", "<operator_sidecar>", "<instructions>"):
            assert tag in ctx, f"Missing tag {tag}"

    def test_trims_front_matter_samples_to_600(self):
        long_sample = "x" * 1200
        signals = dict(_HEURISTICS, front_matter_samples=[long_sample])
        ctx = build_llm_context(
            filename=_FILENAME,
            pdf_info=_PDF_INFO,
            heuristic_signals=signals,
            sidecar=None,
        )
        parsed_signals = json.loads(ctx.split("<heuristic_signals>\n")[1].split("\n</heuristic_signals>")[0])
        assert all(len(s) <= 600 for s in parsed_signals["front_matter_samples"])

    def test_caps_heading_candidates_at_15(self):
        signals = dict(_HEURISTICS, heading_candidates=[f"H{i}" for i in range(25)])
        ctx = build_llm_context(
            filename=_FILENAME,
            pdf_info=_PDF_INFO,
            heuristic_signals=signals,
            sidecar=None,
        )
        parsed_signals = json.loads(ctx.split("<heuristic_signals>\n")[1].split("\n</heuristic_signals>")[0])
        assert len(parsed_signals["heading_candidates"]) <= 15

    def test_sidecar_none_renders_as_none_text(self):
        ctx = build_llm_context(
            filename=_FILENAME,
            pdf_info=_PDF_INFO,
            heuristic_signals=_HEURISTICS,
            sidecar=None,
        )
        assert "<operator_sidecar>\nnone\n</operator_sidecar>" in ctx

    def test_sidecar_dict_renders_as_json(self):
        ctx = build_llm_context(
            filename=_FILENAME,
            pdf_info=_PDF_INFO,
            heuristic_signals=_HEURISTICS,
            sidecar=_SIDECAR,
        )
        # The sidecar content should be JSON-parseable between the tags
        raw = ctx.split("<operator_sidecar>\n")[1].split("\n</operator_sidecar>")[0]
        parsed = json.loads(raw)
        assert parsed == _SIDECAR


# ---------------------------------------------------------------------------
# LLM_NORMALIZER_OUTPUT_SCHEMA tests
# ---------------------------------------------------------------------------

class TestOutputSchema:
    def test_schema_includes_all_enum_fields(self):
        # The enum fields present in the schema (excludes chunk_type and ingest_source_type
        # which are not LLM output fields)
        schema_props = LLM_NORMALIZER_OUTPUT_SCHEMA["properties"]
        # scalar enum fields the LLM should fill
        expected_scalar = {"source_family", "vendor_or_project", "doc_kind", "trust_tier",
                           "freshness_status", "os_family"}
        # list enum fields the LLM should fill
        expected_list = {"init_systems", "package_managers", "major_subsystems"}

        for field in expected_scalar:
            assert field in schema_props, f"Missing scalar enum field: {field}"
            enum_cls = ALL_ENUMS[field]
            schema_enum = schema_props[field].get("enum", [])
            vocab_values = [m.value for m in enum_cls]
            assert schema_enum == vocab_values, (
                f"Enum values mismatch for {field}: schema={schema_enum}, vocab={vocab_values}"
            )

        for field in expected_list:
            assert field in schema_props, f"Missing list enum field: {field}"
            enum_cls = ALL_ENUMS[field]
            item_enum = schema_props[field]["items"].get("enum", [])
            vocab_values = [m.value for m in enum_cls]
            assert item_enum == vocab_values, (
                f"Item enum values mismatch for {field}: schema={item_enum}, vocab={vocab_values}"
            )

    def test_schema_additional_properties_false(self):
        assert LLM_NORMALIZER_OUTPUT_SCHEMA.get("additionalProperties") is False

    def test_schema_required_lists_all_fields(self):
        required = set(LLM_NORMALIZER_OUTPUT_SCHEMA["required"])
        props = set(LLM_NORMALIZER_OUTPUT_SCHEMA["properties"].keys())
        assert required == props


# ---------------------------------------------------------------------------
# normalize_with_llm tests
# ---------------------------------------------------------------------------

class TestNormalizeWithLlm:
    def test_valid_json_returned_as_dict(self):
        worker = _GoodWorker(_MINIMAL_PAYLOAD)
        result = normalize_with_llm(
            worker=worker,
            filename=_FILENAME,
            pdf_info=_PDF_INFO,
            heuristic_signals=_HEURISTICS,
            sidecar=_SIDECAR,
        )
        assert isinstance(result, dict)
        assert result["canonical_title"] == "Proxmox VE Administration Guide"
        assert result["doc_kind"] == "admin_guide"

    def test_garbage_response_returns_empty_dict(self):
        worker = _GarbageWorker()
        result = normalize_with_llm(
            worker=worker,
            filename=_FILENAME,
            pdf_info=_PDF_INFO,
            heuristic_signals=_HEURISTICS,
            sidecar=None,
        )
        assert result == {}

    def test_empty_response_returns_empty_dict(self):
        worker = _EmptyWorker()
        result = normalize_with_llm(
            worker=worker,
            filename=_FILENAME,
            pdf_info=_PDF_INFO,
            heuristic_signals=_HEURISTICS,
            sidecar=None,
        )
        assert result == {}

    def test_raising_worker_returns_empty_dict(self):
        worker = _RaisingWorker()
        result = normalize_with_llm(
            worker=worker,
            filename=_FILENAME,
            pdf_info=_PDF_INFO,
            heuristic_signals=_HEURISTICS,
            sidecar=None,
        )
        assert result == {}

    def test_cache_suffix_accepted_without_blowing_up_no_cache_config_worker(self):
        """Worker without cache_config kwarg should not cause normalize_with_llm to fail."""
        worker = _NoCacheConfigWorker(_MINIMAL_PAYLOAD)
        result = normalize_with_llm(
            worker=worker,
            filename=_FILENAME,
            pdf_info=_PDF_INFO,
            heuristic_signals=_HEURISTICS,
            sidecar=None,
            cache_suffix="test-suffix",
        )
        assert isinstance(result, dict)
        assert result.get("canonical_title") == "Proxmox VE Administration Guide"

    def test_cache_suffix_none_does_not_crash(self):
        worker = _GoodWorker(_MINIMAL_PAYLOAD)
        result = normalize_with_llm(
            worker=worker,
            filename=_FILENAME,
            pdf_info=_PDF_INFO,
            heuristic_signals=_HEURISTICS,
            sidecar=None,
            cache_suffix=None,
        )
        assert isinstance(result, dict)
