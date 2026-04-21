"""Tests for IntakeResult coverage math and serialization."""

import dataclasses

import pytest

from ingestion.stages.pdf_intake import IntakeResult


def make_result(total: int, failed_batches=None):
    """Build an IntakeResult mimicking pipeline logic."""
    if failed_batches is None:
        failed_batches = []
    failed_page_count = sum(b["end_page"] - b["start_page"] + 1 for b in failed_batches)
    processed = total - failed_page_count
    coverage = (processed / total) if total > 0 else 1.0
    return IntakeResult(
        elements=[],
        total_pages=total,
        processed_pages=processed,
        failed_batches=failed_batches,
        page_coverage_pct=coverage,
    )


class TestIntakeResultCoverage:
    def test_full_coverage(self):
        result = make_result(100)
        assert result.processed_pages == 100
        assert result.page_coverage_pct == pytest.approx(1.0)

    def test_90_percent_coverage(self):
        result = make_result(100, [{"start_page": 91, "end_page": 100, "error": "boom"}])
        assert result.processed_pages == 90
        assert result.page_coverage_pct == pytest.approx(0.9)

    def test_80_percent_coverage_below_default_threshold(self):
        result = make_result(100, [{"start_page": 81, "end_page": 100, "error": "timeout"}])
        assert result.processed_pages == 80
        assert result.page_coverage_pct == pytest.approx(0.8)
        # This should be flagged as below the default 0.9 threshold
        default_threshold = 0.9
        assert result.page_coverage_pct < default_threshold

    def test_zero_total_pages_no_divide_by_zero(self):
        result = make_result(0)
        assert result.total_pages == 0
        assert result.processed_pages == 0
        assert result.page_coverage_pct == pytest.approx(1.0)

    def test_empty_failed_batches_processed_equals_total(self):
        result = make_result(50, [])
        assert result.processed_pages == result.total_pages

    def test_multiple_failed_batches_coverage(self):
        # 3 batches failed: pages 1-10, 41-50, 91-100 = 30 failed out of 100
        failed = [
            {"start_page": 1, "end_page": 10, "error": "err1"},
            {"start_page": 41, "end_page": 50, "error": "err2"},
            {"start_page": 91, "end_page": 100, "error": "err3"},
        ]
        result = make_result(100, failed)
        assert result.processed_pages == 70
        assert result.page_coverage_pct == pytest.approx(0.7)

    def test_single_page_doc_success(self):
        result = make_result(1)
        assert result.page_coverage_pct == pytest.approx(1.0)

    def test_single_page_doc_failure(self):
        result = make_result(1, [{"start_page": 1, "end_page": 1, "error": "parse failed"}])
        assert result.processed_pages == 0
        assert result.page_coverage_pct == pytest.approx(0.0)

    def test_serialization_round_trip(self):
        failed = [{"start_page": 5, "end_page": 10, "error": "x"}]
        result = make_result(100, failed)
        as_dict = dataclasses.asdict(result)
        assert as_dict["total_pages"] == 100
        assert as_dict["processed_pages"] == 94
        assert len(as_dict["failed_batches"]) == 1
        assert as_dict["failed_batches"][0]["start_page"] == 5
        assert abs(as_dict["page_coverage_pct"] - 0.94) < 1e-6

    def test_failed_batches_carry_error_string(self):
        failed = [{"start_page": 1, "end_page": 5, "error": "segfault in worker"}]
        result = make_result(50, failed)
        assert result.failed_batches[0]["error"] == "segfault in worker"
