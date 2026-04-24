"""Tests for stages/sanitizer.py."""

from pathlib import Path

import pytest

from ingestion.stages.sanitizer import sanitize_pdf


def _make_minimal_pdf(tmp_path: Path, num_pages: int = 2, stem: str = "test") -> Path:
    """Create a minimal valid PDF with num_pages blank pages using pypdf."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=612, height=792)

    out = tmp_path / f"{stem}.pdf"
    with out.open("wb") as fh:
        writer.write(fh)
    return out


class TestSanitizerHappyPath:
    def test_valid_pdf_returns_true(self, tmp_path):
        src = _make_minimal_pdf(tmp_path, num_pages=2, stem="src")
        dst = tmp_path / "dst.pdf"
        result = sanitize_pdf(src, dst)
        assert result is True

    def test_output_file_exists(self, tmp_path):
        src = _make_minimal_pdf(tmp_path, num_pages=2)
        dst = tmp_path / "out.pdf"
        sanitize_pdf(src, dst)
        assert dst.exists()

    def test_output_has_correct_page_count(self, tmp_path):
        from pypdf import PdfReader

        src = _make_minimal_pdf(tmp_path, num_pages=3)
        dst = tmp_path / "out.pdf"
        sanitize_pdf(src, dst)
        reader = PdfReader(str(dst))
        assert len(reader.pages) == 3

    def test_two_page_round_trip(self, tmp_path):
        from pypdf import PdfReader

        src = _make_minimal_pdf(tmp_path, num_pages=2)
        dst = tmp_path / "sanitized.pdf"
        ok = sanitize_pdf(src, dst)
        assert ok is True
        reader = PdfReader(str(dst))
        assert len(reader.pages) == 2


class TestSanitizerFailurePath:
    def test_non_pdf_returns_false(self, tmp_path):
        bad = tmp_path / "not_a_pdf.pdf"
        bad.write_bytes(b"not a pdf at all, just garbage bytes")
        dst = tmp_path / "out.pdf"
        result = sanitize_pdf(bad, dst)
        assert result is False

    def test_non_pdf_does_not_raise(self, tmp_path):
        """sanitize_pdf must never propagate exceptions to the caller."""
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"\x00\x01\x02")
        dst = tmp_path / "out.pdf"
        # Should not raise
        try:
            sanitize_pdf(bad, dst)
        except Exception as exc:
            pytest.fail(f"sanitize_pdf raised unexpectedly: {exc}")

    def test_missing_file_returns_false(self, tmp_path):
        src = tmp_path / "nonexistent.pdf"
        dst = tmp_path / "out.pdf"
        result = sanitize_pdf(src, dst)
        assert result is False

    def test_dst_not_created_on_failure(self, tmp_path):
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"not a pdf")
        dst = tmp_path / "out.pdf"
        sanitize_pdf(bad, dst)
        assert not dst.exists()


class TestSanitizerAnnotationStripping:
    def test_annots_removed_from_pages(self, tmp_path):
        """After sanitization, /Annots should not appear on any page."""
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import ArrayObject, DictionaryObject, NameObject

        # Build a PDF with a fake /Annots entry on the first page
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_blank_page(width=612, height=792)

        # Inject a dummy /Annots list on page 0
        page0 = writer.pages[0]
        page0[NameObject("/Annots")] = ArrayObject()

        src = tmp_path / "with_annots.pdf"
        with src.open("wb") as fh:
            writer.write(fh)

        # Verify the annotation is present in the source
        reader = PdfReader(str(src))
        assert "/Annots" in reader.pages[0], "Test setup: /Annots should be present before sanitization"

        dst = tmp_path / "sanitized.pdf"
        ok = sanitize_pdf(src, dst)
        assert ok is True

        # After sanitization, /Annots should be gone
        reader2 = PdfReader(str(dst))
        for page in reader2.pages:
            assert "/Annots" not in page, "/Annots should be stripped by sanitizer"
