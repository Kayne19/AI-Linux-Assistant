"""Tests for per-document quarantine logic in pipeline.py.

All tests stub out process_pdf_parallel and sanitize_pdf — no real PDFs are
run through partition_pdf.
"""

import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ingestion.pipeline import (
    IngestPipelineConfig,
    IngestPipelineRunner,
    IngestRunContext,
    LowPageCoverageError,
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
        trace_output_dir=tmp_path / "traces",
        mass_mode=False,
        sanitize=False,
        min_page_coverage=0.9,
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
        elements=[{"type": "NarrativeText", "text": "hello", "metadata": {"page_number": 1}}],
        total_pages=1,
        processed_pages=1,
        failed_batches=[],
        page_coverage_pct=1.0,
    )


# ---------------------------------------------------------------------------
# Tests: low coverage raises LowPageCoverageError
# ---------------------------------------------------------------------------

class TestLowCoverageQuarantine:
    def test_low_coverage_raises_error(self, tmp_path):
        """process_pdf_parallel returning low coverage → LowPageCoverageError."""
        pdf = _make_pdf(tmp_path)
        config = _minimal_config(tmp_path, min_page_coverage=0.9)

        with patch("ingestion.pipeline.process_pdf_parallel", return_value=_low_coverage_result()):
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

        with patch("ingestion.pipeline.process_pdf_parallel", return_value=_low_coverage_result()):
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

        with patch("ingestion.pipeline.process_pdf_parallel", return_value=_low_coverage_result()):
            runner = IngestPipelineRunner.__new__(IngestPipelineRunner)
            runner.config = config
            context = IngestRunContext(pdf_path=pdf, config=config, audit=MagicMock())
            try:
                runner._intake_raw(context)
            except LowPageCoverageError:
                pass

        assert expected_failed_dir.exists(), f"Expected quarantine dir: {expected_failed_dir}"

    def test_low_coverage_error_json_has_expected_keys(self, tmp_path):
        """error.json must have: reason, page_coverage_pct, processed_pages, total_pages, failed_batches, ts."""
        to_ingest = tmp_path / "data" / "to_ingest"
        to_ingest.mkdir(parents=True)
        pdf = _make_pdf(to_ingest)
        expected_failed_dir = tmp_path / "data" / "failed" / pdf.stem

        config = _minimal_config(tmp_path, min_page_coverage=0.9)

        with patch("ingestion.pipeline.process_pdf_parallel", return_value=_low_coverage_result()):
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
        for key in ("reason", "page_coverage_pct", "processed_pages", "total_pages", "failed_batches", "ts"):
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
            patch("ingestion.pipeline.process_pdf_parallel", return_value=_full_coverage_result()),
            patch("ingestion.pipeline.write_json"),
        ):
            runner = IngestPipelineRunner.__new__(IngestPipelineRunner)
            runner.config = config
            context = IngestRunContext(pdf_path=pdf, config=config, audit=MagicMock())
            # Should not raise
            runner._intake_raw(context)

        assert context.raw_elements == _full_coverage_result().elements
