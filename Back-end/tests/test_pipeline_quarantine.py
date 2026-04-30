"""Tests for per-document quarantine logic in pipeline.py.

All tests stub out process_pdf_parallel and sanitize_pdf — no real PDFs are
run through partition_pdf.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingestion.pipeline import (
    IngestState,
    IngestPipelineConfig,
    IngestPipelineRunner,
    IngestRunContext,
    LowPageCoverageError,
    _filter_llm_identity_fields,
)
from ingestion.stages.pdf_intake import IntakeResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_config(tmp_path: Path, **overrides) -> IngestPipelineConfig:
    """Build a minimal IngestPipelineConfig pointing at tmp_path."""
    defaults = dict(
        raw_output=tmp_path / "raw.json",
        clean_output=tmp_path / "clean.json",
        context_output=tmp_path / "context.txt",
        final_output=tmp_path / "final.json",
        batch_size=10,
        max_workers=1,
        hi_res_model_name="yolox",
        min_text_chars=50,
        ocr_dpi=300,
        enrichment_provider="local",
        enrichment_model="test-model",
        enrichment_reasoning_effort=None,
        registry_provider="local",
        registry_model="test-model",
        identity_provider="local",
        identity_model="test-model",
        identity_reasoning_effort=None,
        trace_output_dir=tmp_path / "traces",
        mass_mode=False,
        sanitize=False,
        min_page_coverage=0.9,
        identity_llm_infill=False,
    )
    defaults.update(overrides)
    return IngestPipelineConfig(**defaults)


def _make_pdf(tmp_path: Path, stem: str = "test") -> Path:
    """Write a minimal valid PDF so the path exists."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    pdf = tmp_path / f"{stem}.pdf"
    with pdf.open("wb") as fh:
        writer.write(fh)
    return pdf


class _IdentityWorker:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def generate_text(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(self.payload)


def _low_coverage_result() -> IntakeResult:
    return IntakeResult(
        elements=[],
        total_pages=100,
        processed_pages=50,
        failed_batches=[{"start_page": 51, "end_page": 100, "error": "parse error"}],
        page_coverage_pct=0.5,
    )


def _full_coverage_result() -> IntakeResult:
    return IntakeResult(
        elements=[
            {"type": "NarrativeText", "text": "hello", "metadata": {"page_number": 1}}
        ],
        total_pages=1,
        processed_pages=1,
        failed_batches=[],
        page_coverage_pct=1.0,
    )


def _long_coverage_result() -> IntakeResult:
    return IntakeResult(
        elements=[
            {
                "type": "Title",
                "text": "Chapter 1 Setup",
                "metadata": {"page_number": 1},
            },
            {
                "type": "NarrativeText",
                "text": "Install packages and configure the service. " * 4,
                "metadata": {"page_number": 1, "filename": "manual.pdf"},
            },
        ],
        total_pages=1,
        processed_pages=1,
        failed_batches=[],
        page_coverage_pct=1.0,
    )


class _FakeIndexer:
    def __init__(self):
        self.calls = []

    def ingest_json(self, path, *, document_identity=None, force_reingest=False):
        self.calls.append((path, document_identity, force_reingest))
        return {"rows": 2, "table_name": "chunks", "created_table": False}


# ---------------------------------------------------------------------------
# Tests: low coverage raises LowPageCoverageError
# ---------------------------------------------------------------------------


class TestLowCoverageQuarantine:
    def test_low_coverage_raises_error(self, tmp_path):
        """process_pdf_parallel returning low coverage → LowPageCoverageError."""
        pdf = _make_pdf(tmp_path)
        config = _minimal_config(tmp_path, min_page_coverage=0.9)

        with patch(
            "ingestion.pipeline.process_pdf_parallel",
            return_value=_low_coverage_result(),
        ):
            runner = IngestPipelineRunner.__new__(IngestPipelineRunner)
            runner.config = config
            context = IngestRunContext(pdf_path=pdf, config=config, audit=MagicMock())
            with pytest.raises(LowPageCoverageError):
                runner._intake_raw(context)

    def test_low_coverage_audit_log_records_intake_quarantine(self, tmp_path):
        """AuditLog.record should be called with phase='intake_quarantine', action='low_coverage'."""
        pdf = _make_pdf(tmp_path)
        config = _minimal_config(tmp_path, min_page_coverage=0.9)
        mock_audit = MagicMock()

        with patch(
            "ingestion.pipeline.process_pdf_parallel",
            return_value=_low_coverage_result(),
        ):
            runner = IngestPipelineRunner.__new__(IngestPipelineRunner)
            runner.config = config
            context = IngestRunContext(pdf_path=pdf, config=config, audit=mock_audit)
            try:
                runner._intake_raw(context)
            except LowPageCoverageError:
                pass

        calls = mock_audit.record.call_args_list
        assert any(
            call.kwargs.get("phase") == "intake_quarantine"
            and call.kwargs.get("action") == "low_coverage"
            for call in calls
        ), f"Expected intake_quarantine/low_coverage audit entry; got: {calls}"

    def test_low_coverage_creates_quarantine_dir(self, tmp_path):
        """A quarantine directory should be created under data/failed/<stem>/.

        _quarantine_doc uses pdf_path.parent.parent.parent as the repo root.
        Production layout: Back-end/data/to_ingest/<pdf> → root = Back-end.
        We mirror that: tmp_path / data / to_ingest / test.pdf → root = tmp_path.
        """
        to_ingest = tmp_path / "data" / "to_ingest"
        to_ingest.mkdir(parents=True)
        pdf = _make_pdf(to_ingest)
        # 3 levels up from pdf: to_ingest → data → tmp_path
        expected_failed_dir = tmp_path / "data" / "failed" / pdf.stem

        config = _minimal_config(tmp_path, min_page_coverage=0.9)

        with patch(
            "ingestion.pipeline.process_pdf_parallel",
            return_value=_low_coverage_result(),
        ):
            runner = IngestPipelineRunner.__new__(IngestPipelineRunner)
            runner.config = config
            context = IngestRunContext(pdf_path=pdf, config=config, audit=MagicMock())
            try:
                runner._intake_raw(context)
            except LowPageCoverageError:
                pass

        assert expected_failed_dir.exists(), (
            f"Expected quarantine dir: {expected_failed_dir}"
        )

    def test_low_coverage_error_json_has_expected_keys(self, tmp_path):
        """error.json must have: reason, page_coverage_pct, processed_pages, total_pages, failed_batches, ts."""
        to_ingest = tmp_path / "data" / "to_ingest"
        to_ingest.mkdir(parents=True)
        pdf = _make_pdf(to_ingest)
        expected_failed_dir = tmp_path / "data" / "failed" / pdf.stem

        config = _minimal_config(tmp_path, min_page_coverage=0.9)

        with patch(
            "ingestion.pipeline.process_pdf_parallel",
            return_value=_low_coverage_result(),
        ):
            runner = IngestPipelineRunner.__new__(IngestPipelineRunner)
            runner.config = config
            context = IngestRunContext(pdf_path=pdf, config=config, audit=MagicMock())
            try:
                runner._intake_raw(context)
            except LowPageCoverageError:
                pass

        error_json = expected_failed_dir / "error.json"
        assert error_json.exists()
        data = json.loads(error_json.read_text())
        for key in (
            "reason",
            "page_coverage_pct",
            "processed_pages",
            "total_pages",
            "failed_batches",
            "ts",
        ):
            assert key in data, f"error.json missing key: {key}"


# ---------------------------------------------------------------------------
# Tests: sanitizer failure quarantine
# ---------------------------------------------------------------------------


class TestSanitizerFailedQuarantine:
    def test_sanitizer_failure_raises_low_coverage_error(self, tmp_path):
        """When sanitizer returns False in mass_mode, LowPageCoverageError is raised."""
        pdf = _make_pdf(tmp_path)
        config = _minimal_config(tmp_path, mass_mode=True, sanitize=True)

        with patch("ingestion.pipeline.sanitize_pdf", return_value=False):
            runner = IngestPipelineRunner.__new__(IngestPipelineRunner)
            runner.config = config
            context = IngestRunContext(pdf_path=pdf, config=config, audit=MagicMock())
            with pytest.raises(LowPageCoverageError):
                runner._intake_raw(context)

    def test_sanitizer_failure_audit_action(self, tmp_path):
        """Audit log should record action='sanitizer_failed'."""
        pdf = _make_pdf(tmp_path)
        config = _minimal_config(tmp_path, mass_mode=True, sanitize=True)
        mock_audit = MagicMock()

        with patch("ingestion.pipeline.sanitize_pdf", return_value=False):
            runner = IngestPipelineRunner.__new__(IngestPipelineRunner)
            runner.config = config
            context = IngestRunContext(pdf_path=pdf, config=config, audit=mock_audit)
            try:
                runner._intake_raw(context)
            except LowPageCoverageError:
                pass

        calls = mock_audit.record.call_args_list
        assert any(
            call.kwargs.get("phase") == "intake_quarantine"
            and call.kwargs.get("action") == "sanitizer_failed"
            for call in calls
        ), f"Expected sanitizer_failed audit entry; got: {calls}"

    def test_sanitizer_failure_creates_error_json(self, tmp_path):
        """error.json should be written even when failure is from sanitizer."""
        to_ingest = tmp_path / "data" / "to_ingest"
        to_ingest.mkdir(parents=True)
        pdf = _make_pdf(to_ingest)
        expected_failed_dir = tmp_path / "data" / "failed" / pdf.stem

        config = _minimal_config(tmp_path, mass_mode=True, sanitize=True)

        with patch("ingestion.pipeline.sanitize_pdf", return_value=False):
            runner = IngestPipelineRunner.__new__(IngestPipelineRunner)
            runner.config = config
            context = IngestRunContext(pdf_path=pdf, config=config, audit=MagicMock())
            try:
                runner._intake_raw(context)
            except LowPageCoverageError:
                pass

        assert (expected_failed_dir / "error.json").exists()


# ---------------------------------------------------------------------------
# Tests: sufficient coverage does NOT quarantine
# ---------------------------------------------------------------------------


class TestSufficientCoverageNoQuarantine:
    def test_full_coverage_does_not_raise(self, tmp_path):
        """Full coverage should not trigger LowPageCoverageError."""
        pdf = _make_pdf(tmp_path)
        config = _minimal_config(tmp_path, min_page_coverage=0.9)

        with (
            patch(
                "ingestion.pipeline.process_pdf_parallel",
                return_value=_full_coverage_result(),
            ),
            patch("ingestion.pipeline.write_json"),
        ):
            runner = IngestPipelineRunner.__new__(IngestPipelineRunner)
            runner.config = config
            context = IngestRunContext(pdf_path=pdf, config=config, audit=MagicMock())
            # Should not raise
            runner._intake_raw(context)

        assert context.raw_elements == _full_coverage_result().elements


class TestPipelineIdentitySections:
    def test_sync_run_resolves_identity_sections_and_indexes_with_identity(
        self, tmp_path
    ):
        pdf = _make_pdf(tmp_path, stem="manual")
        config = _minimal_config(tmp_path, min_page_coverage=0.9)
        fake_indexer = _FakeIndexer()

        def fake_enrich_elements(
            json_path, context_text_path, worker, model, cache_config
        ):
            final = Path(json_path).with_name(f"{Path(json_path).stem}_final.json")
            final.write_text(Path(json_path).read_text())

        runner = IngestPipelineRunner.__new__(IngestPipelineRunner)
        runner.config = config
        runner.enrichment_worker = MagicMock()

        with (
            patch(
                "ingestion.pipeline.load_sidecar",
                return_value={
                    "canonical_title": "Operator Manual",
                    "source_family": "debian",
                },
            ),
            patch(
                "ingestion.pipeline.process_pdf_parallel",
                return_value=_long_coverage_result(),
            ),
            patch(
                "ingestion.pipeline.export_full_text",
                side_effect=lambda _pdf, out: out.write_text("Operator Manual context"),
            ),
            patch("ingestion.pipeline.update_routing_registry"),
            patch(
                "ingestion.pipeline.enrich_elements", side_effect=fake_enrich_elements
            ),
            patch("ingestion.pipeline.load_retrieval_config", return_value=MagicMock()),
            patch(
                "ingestion.pipeline.build_ingestion_indexer", return_value=fake_indexer
            ),
        ):
            context = runner.run(pdf)

        assert IngestState.LOAD_SIDECAR.value in context.state_trace
        assert IngestState.EXTRACT_IDENTITY.value in context.state_trace
        assert IngestState.RESOLVE_IDENTITY.value in context.state_trace
        assert IngestState.DETECT_SECTIONS.value in context.state_trace
        assert context.document_identity is not None
        assert context.document_identity.canonical_title == "Operator Manual"
        assert fake_indexer.calls[0][1] is context.document_identity

    def test_batch_park_persists_identity_and_canonical_doc_id(self, tmp_path):
        pdf = _make_pdf(tmp_path, stem="manual")
        config = _minimal_config(
            tmp_path,
            batch_mode=True,
            ingest_state_dir=tmp_path / "ingest_state",
            min_page_coverage=0.9,
        )

        runner = IngestPipelineRunner.__new__(IngestPipelineRunner)
        runner.config = config
        runner.enrichment_worker = MagicMock()

        with (
            patch(
                "ingestion.pipeline.load_sidecar",
                return_value={
                    "canonical_title": "Batch Manual",
                    "source_family": "debian",
                },
            ),
            patch(
                "ingestion.pipeline.process_pdf_parallel",
                return_value=_long_coverage_result(),
            ),
            patch(
                "ingestion.pipeline.export_full_text",
                side_effect=lambda _pdf, out: out.write_text(
                    "Batch Manual context " * 20
                ),
            ),
            patch("ingestion.pipeline.update_routing_registry"),
        ):
            context = runner.run(pdf)

        assert context.state_trace[-1] == IngestState.AWAITING_ENRICHMENT.value
        assert context.document_identity is not None
        assert context.doc_id == context.document_identity.canonical_source_id
        state_path = config.ingest_state_dir / context.doc_id / "state.json"
        data = json.loads(state_path.read_text())
        assert data["doc_id"] == context.document_identity.canonical_source_id
        assert data["document_identity"]["canonical_title"] == "Batch Manual"

    def test_resolve_identity_uses_llm_for_weak_fields(self, tmp_path):
        pdf = _make_pdf(tmp_path, stem="weak")
        config = _minimal_config(tmp_path, identity_llm_infill=True)
        runner = IngestPipelineRunner.__new__(IngestPipelineRunner)
        runner.config = config
        runner.identity_worker = _IdentityWorker(
            {
                "canonical_title": "Weak Manual",
                "title_aliases": ["weak"],
                "source_family": "debian",
                "product": "Debian",
                "vendor_or_project": "debian_project",
                "version": "12",
                "release_date": "",
                "doc_kind": "install_guide",
                "trust_tier": "official",
                "freshness_status": "current",
                "os_family": "linux",
                "init_systems": ["systemd"],
                "package_managers": ["apt", "dpkg"],
                "major_subsystems": ["package_management"],
                "applies_to": ["debian-12"],
                "source_url": "",
                "rationale": "Debian installer metadata.",
            }
        )
        context = IngestRunContext(
            pdf_path=pdf,
            config=config,
            pdf_info={"title": "", "subject": "", "author": "", "producer": ""},
            heuristic_signals={
                "filename": "weak.pdf",
                "stem": "weak",
                "metadata": {"title": "", "subject": "", "author": "", "producer": ""},
                "front_matter_samples": [],
                "heading_candidates": [],
                "version_detected": None,
                "vendors_detected": [],
                "outline_summary": {"top_title": None, "depth": 0, "entry_count": 0},
            },
            audit=None,
        )

        runner._resolve_identity(context)

        assert runner.identity_worker.calls
        assert context.document_identity.source_family == "debian"
        assert context.document_identity.product == "Debian"
        assert context.document_identity.trust_tier == "unknown"
        assert context.document_identity.freshness_status == "unknown"

    def test_conservative_llm_fields_are_dropped(self):
        filtered = _filter_llm_identity_fields(
            llm_fields={
                "trust_tier": "canonical",
                "freshness_status": "current",
                "doc_kind": "admin_guide",
            },
            requested_fields={"trust_tier", "freshness_status", "doc_kind"},
            audit=None,
            doc="x.pdf",
        )

        assert filtered == {"doc_kind": "admin_guide"}


# ---------------------------------------------------------------------------
# Tests: quarantine path resolves relative to queue root (not repo layout)
# ---------------------------------------------------------------------------


class TestQuarantinePathQueueRelative:
    def test_non_standard_queue_layout(self, tmp_path):
        """Quarantine must land at <queue_root>/failed/<stem>/ for any queue root.

        Layout: tmp_path/myqueue/to_ingest/sample.pdf
        Expected: tmp_path/myqueue/failed/sample/
        """
        to_ingest = tmp_path / "myqueue" / "to_ingest"
        to_ingest.mkdir(parents=True)
        pdf = _make_pdf(to_ingest, stem="sample")
        expected_failed_dir = tmp_path / "myqueue" / "failed" / "sample"

        config = _minimal_config(tmp_path, min_page_coverage=0.9)

        with patch(
            "ingestion.pipeline.process_pdf_parallel",
            return_value=_low_coverage_result(),
        ):
            runner = IngestPipelineRunner.__new__(IngestPipelineRunner)
            runner.config = config
            context = IngestRunContext(pdf_path=pdf, config=config, audit=MagicMock())
            try:
                runner._intake_raw(context)
            except LowPageCoverageError:
                pass

        assert expected_failed_dir.exists(), (
            f"Expected quarantine dir: {expected_failed_dir}"
        )
        assert (expected_failed_dir / "error.json").exists()
