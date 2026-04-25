from pathlib import Path

from pypdf import PdfWriter

from ingestion.identity.pdf_meta import _clean, read_pdf_info, read_outline

_EXPECTED_KEYS = {"title", "subject", "author", "producer", "creator", "keywords"}


def _make_pdf(tmp_path: Path, name: str = "doc.pdf", pages: int = 1, metadata: dict | None = None) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if metadata:
        writer.add_metadata(metadata)
    path = tmp_path / name
    with path.open("wb") as f:
        writer.write(f)
    return path


def _make_pdf_with_outline(tmp_path: Path) -> Path:
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=612, height=792)
    ch1 = writer.add_outline_item("Chapter 1", 0)
    writer.add_outline_item("Section 1.1", 1, parent=ch1)
    writer.add_outline_item("Chapter 2", 1)
    path = tmp_path / "outlined.pdf"
    with path.open("wb") as f:
        writer.write(f)
    return path


# ---------------------------------------------------------------------------
# _clean
# ---------------------------------------------------------------------------

def test_clean_strips_extra_whitespace():
    assert _clean("  hello   world  ") == "hello world"


def test_clean_returns_empty_on_none():
    assert _clean(None) == ""


def test_clean_coerces_non_string():
    assert _clean(42) == "42"


def test_clean_normalizes_internal_tabs():
    assert _clean("foo\t\tbar") == "foo bar"


# ---------------------------------------------------------------------------
# read_pdf_info
# ---------------------------------------------------------------------------

def test_read_pdf_info_returns_all_six_keys(tmp_path):
    pdf = _make_pdf(tmp_path)
    result = read_pdf_info(pdf)
    assert set(result.keys()) == _EXPECTED_KEYS


def test_read_pdf_info_populated_metadata(tmp_path):
    pdf = _make_pdf(tmp_path, metadata={
        "/Title": "Test Title",
        "/Author": "Test Author",
        "/Subject": "Test Subject",
        "/Producer": "Test Producer",
        "/Creator": "Test Creator",
        "/Keywords": "kw1 kw2",
    })
    result = read_pdf_info(pdf)
    assert result["title"] == "Test Title"
    assert result["author"] == "Test Author"
    assert result["subject"] == "Test Subject"
    assert result["producer"] == "Test Producer"
    assert result["creator"] == "Test Creator"
    assert result["keywords"] == "kw1 kw2"


def test_read_pdf_info_empty_strings_when_no_metadata(tmp_path):
    pdf = _make_pdf(tmp_path)
    result = read_pdf_info(pdf)
    # pypdf always injects /Producer; other fields should be empty
    assert result["title"] == ""
    assert result["author"] == ""
    assert result["subject"] == ""
    assert result["keywords"] == ""


def test_read_pdf_info_returns_empty_dict_on_corrupt_file(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    result = read_pdf_info(bad)
    assert set(result.keys()) == _EXPECTED_KEYS
    for v in result.values():
        assert v == ""


def test_read_pdf_info_does_not_raise_on_missing_file(tmp_path):
    missing = tmp_path / "missing.pdf"
    result = read_pdf_info(missing)
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# read_outline
# ---------------------------------------------------------------------------

def test_read_outline_returns_empty_list_for_pdf_without_outline(tmp_path):
    pdf = _make_pdf(tmp_path)
    result = read_outline(pdf)
    assert result == []


def test_read_outline_entries_have_expected_keys(tmp_path):
    pdf = _make_pdf_with_outline(tmp_path)
    result = read_outline(pdf)
    assert len(result) > 0
    for entry in result:
        assert "title" in entry
        assert "level" in entry
        assert "page" in entry


def test_read_outline_levels_start_at_one(tmp_path):
    pdf = _make_pdf_with_outline(tmp_path)
    result = read_outline(pdf)
    levels = [e["level"] for e in result]
    assert min(levels) == 1


def test_read_outline_detects_hierarchy(tmp_path):
    pdf = _make_pdf_with_outline(tmp_path)
    result = read_outline(pdf)
    levels = {e["level"] for e in result}
    assert 1 in levels
    assert 2 in levels


def test_read_outline_returns_empty_on_corrupt_file(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"garbage")
    result = read_outline(bad)
    assert result == []
