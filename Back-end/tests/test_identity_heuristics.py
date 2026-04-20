from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from ingestion.identity.heuristics import (
    _looks_like_heading,
    collect_front_matter,
    detect_from_outline,
    detect_vendor_strings,
    detect_version,
    extract_heuristic_signals,
)


# ---------------------------------------------------------------------------
# Helpers to build in-memory PDFs
# ---------------------------------------------------------------------------

def _add_text_page(writer: PdfWriter, text: str) -> None:
    page = writer.add_blank_page(width=612, height=792)
    lines = text.split("\n")
    ops = b"\n".join(
        f"BT /F1 12 Tf 72 {700 - i * 18} Td ({line.replace('(', '').replace(')', '')}) Tj ET".encode()
        for i, line in enumerate(lines)
    )
    cs = DecodedStreamObject()
    cs.set_data(ops)
    page[NameObject("/Contents")] = writer._add_object(cs)
    font_dict = DictionaryObject()
    font_dict[NameObject("/Type")] = NameObject("/Font")
    font_dict[NameObject("/Subtype")] = NameObject("/Type1")
    font_dict[NameObject("/BaseFont")] = NameObject("/Helvetica")
    font_ref = writer._add_object(font_dict)
    resources = DictionaryObject()
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = font_ref
    resources[NameObject("/Font")] = fonts
    page[NameObject("/Resources")] = resources


def _write_pdf(writer: PdfWriter, tmp_path: Path) -> Path:
    path = tmp_path / "test.pdf"
    with path.open("wb") as f:
        writer.write(f)
    return path


# ---------------------------------------------------------------------------
# _looks_like_heading
# ---------------------------------------------------------------------------

def test_heading_accepts_title_case_line():
    # "Introduction", "Networking", "Guide" are all title-case (3/3 >= 0.7)
    assert _looks_like_heading("Introduction Networking Guide") is True


def test_heading_accepts_all_caps_short():
    assert _looks_like_heading("OVERVIEW") is True


def test_heading_rejects_sentence_ending_with_period():
    assert _looks_like_heading("This is a regular sentence.") is False


def test_heading_rejects_comma_terminated():
    assert _looks_like_heading("First item, second item,") is False


def test_heading_rejects_colon_terminated():
    assert _looks_like_heading("See the following:") is False


def test_heading_rejects_over_long_line():
    long_line = "Word " * 16
    assert _looks_like_heading(long_line.strip()) is False


def test_heading_rejects_empty_string():
    assert _looks_like_heading("") is False


def test_heading_rejects_too_short():
    assert _looks_like_heading("Hi") is False


def test_heading_rejects_mostly_lowercase_words():
    assert _looks_like_heading("this is all lowercase text here and more") is False


def test_heading_accepts_mixed_with_most_title_case():
    # 3 of 4 alpha words are title-case (>= 0.7)
    assert _looks_like_heading("Network Configuration Guide v2") is True


# ---------------------------------------------------------------------------
# detect_version
# ---------------------------------------------------------------------------

def test_detect_version_finds_version_prefix():
    assert detect_version("Version 7.4 of the software") == "7.4"


def test_detect_version_finds_v_prefix():
    assert detect_version("v8.0.1 release notes") == "8.0.1"


def test_detect_version_finds_release_prefix():
    result = detect_version("Release 12.0 of the package")
    assert result == "12.0"


def test_detect_version_finds_vendor_anchored():
    result = detect_version("Proxmox VE 8.1 administration guide")
    assert result == "8.1"


def test_detect_version_finds_iso_date_fallback():
    result = detect_version("Updated on 2024-03-15 for production")
    assert result == "2024-03-15"


def test_detect_version_returns_none_on_unrelated_text():
    assert detect_version("No version information here at all") is None


def test_detect_version_returns_none_on_empty():
    assert detect_version("") is None


def test_detect_version_vendor_ubuntu():
    result = detect_version("Ubuntu 22.04 LTS server guide")
    assert result == "22.04"


# ---------------------------------------------------------------------------
# detect_vendor_strings
# ---------------------------------------------------------------------------

def test_detect_vendor_strings_finds_single_vendor():
    result = detect_vendor_strings("This guide covers Proxmox VE deployment")
    assert "Proxmox" in result


def test_detect_vendor_strings_finds_multiple_vendors():
    result = detect_vendor_strings("Debian and Ubuntu are both supported")
    assert len(result) >= 2
    lower_results = [v.lower() for v in result]
    assert any("debian" in v for v in lower_results)
    assert any("ubuntu" in v for v in lower_results)


def test_detect_vendor_strings_deduplicates():
    result = detect_vendor_strings("Docker Docker Docker is mentioned three times")
    docker_count = sum(1 for v in result if v.lower() == "docker")
    assert docker_count == 1


def test_detect_vendor_strings_caps_at_ten():
    many = " ".join([
        "Proxmox", "Debian", "Ubuntu", "Red Hat", "RHEL", "CentOS",
        "Fedora", "SUSE", "Docker", "Kubernetes", "Linux Foundation",
        "Canonical", "systemd",
    ])
    result = detect_vendor_strings(many)
    assert len(result) <= 10


def test_detect_vendor_strings_returns_empty_for_no_match():
    result = detect_vendor_strings("A document about nothing special")
    assert result == []


def test_detect_vendor_strings_case_insensitive_detection():
    result = detect_vendor_strings("proxmox is great")
    assert len(result) == 1


# ---------------------------------------------------------------------------
# detect_from_outline
# ---------------------------------------------------------------------------

def test_detect_from_outline_empty_input():
    result = detect_from_outline([])
    assert result == {"top_title": None, "depth": 0, "entry_count": 0}


def test_detect_from_outline_single_entry():
    outline = [{"title": "Introduction", "level": 1, "page": 1}]
    result = detect_from_outline(outline)
    assert result["top_title"] == "Introduction"
    assert result["depth"] == 1
    assert result["entry_count"] == 1


def test_detect_from_outline_reports_max_depth():
    outline = [
        {"title": "Chapter 1", "level": 1, "page": 1},
        {"title": "Section 1.1", "level": 2, "page": 2},
        {"title": "Subsection 1.1.1", "level": 3, "page": 3},
    ]
    result = detect_from_outline(outline)
    assert result["depth"] == 3
    assert result["entry_count"] == 3


def test_detect_from_outline_top_title_is_first_level_one():
    outline = [
        {"title": "Chapter 1", "level": 1, "page": 1},
        {"title": "Chapter 2", "level": 1, "page": 10},
    ]
    result = detect_from_outline(outline)
    assert result["top_title"] == "Chapter 1"


def test_detect_from_outline_no_level_one_entry():
    outline = [
        {"title": "Section A", "level": 2, "page": 1},
        {"title": "Section B", "level": 2, "page": 5},
    ]
    result = detect_from_outline(outline)
    assert result["top_title"] is None
    assert result["depth"] == 2


# ---------------------------------------------------------------------------
# collect_front_matter
# ---------------------------------------------------------------------------

def test_collect_front_matter_sample_limit(tmp_path):
    writer = PdfWriter()
    for i in range(6):
        _add_text_page(writer, f"Chapter {i + 1} Main Heading\nSome content on page {i + 1}")
    pdf = _write_pdf(writer, tmp_path)
    result = collect_front_matter(pdf)
    assert len(result["front_matter_samples"]) <= 3


def test_collect_front_matter_heading_limit(tmp_path):
    writer = PdfWriter()
    # 5 pages with enough distinct headings to exceed 15
    for i in range(5):
        lines = "\n".join(
            f"Heading Number {i * 6 + j + 1} Title" for j in range(6)
        )
        _add_text_page(writer, lines)
    pdf = _write_pdf(writer, tmp_path)
    result = collect_front_matter(pdf)
    assert len(result["heading_candidates"]) <= 15


def test_collect_front_matter_includes_filename_and_stem(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    path = tmp_path / "myguide.pdf"
    with path.open("wb") as f:
        writer.write(f)
    result = collect_front_matter(path)
    assert result["filename"] == "myguide.pdf"
    assert result["stem"] == "myguide"


def test_collect_front_matter_returns_metadata_dict(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/Title": "Test Doc"})
    path = tmp_path / "meta.pdf"
    with path.open("wb") as f:
        writer.write(f)
    result = collect_front_matter(path)
    assert "metadata" in result
    assert result["metadata"]["title"] == "Test Doc"


# ---------------------------------------------------------------------------
# extract_heuristic_signals
# ---------------------------------------------------------------------------

def test_extract_heuristic_signals_includes_all_fields(tmp_path):
    writer = PdfWriter()
    _add_text_page(writer, "Proxmox VE 8 Administration Guide\nVersion 8.0 of the manual")
    writer.add_metadata({"/Title": "Proxmox VE 8 Administration Guide"})
    path = tmp_path / "proxmox.pdf"
    with path.open("wb") as f:
        writer.write(f)
    result = extract_heuristic_signals(path)
    assert "version_detected" in result
    assert "vendors_detected" in result
    assert "outline_summary" in result
    assert "front_matter_samples" in result
    assert "heading_candidates" in result


def test_extract_heuristic_signals_detects_vendor_and_version(tmp_path):
    writer = PdfWriter()
    _add_text_page(writer, "Proxmox VE 8.2 Administrator Guide\nVersion 8.2 release")
    writer.add_metadata({"/Title": "Proxmox VE 8.2 Guide"})
    path = tmp_path / "proxmox8.pdf"
    with path.open("wb") as f:
        writer.write(f)
    result = extract_heuristic_signals(path)
    assert result["version_detected"] is not None
    assert any("Proxmox" in v for v in result["vendors_detected"])


# ---------------------------------------------------------------------------
# detect_version — line-start bare version
# ---------------------------------------------------------------------------

def test_detect_version_line_start_bare():
    assert detect_version("9.0 release notes") == "9.0"


def test_detect_version_no_bare_mid_line():
    # A version number in the middle of prose without keyword context should not match
    assert detect_version("the value 9.0 is stored") is None


# ---------------------------------------------------------------------------
# extract_heuristic_signals — graceful degradation
# ---------------------------------------------------------------------------

def test_extract_heuristic_signals_missing_pdf(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    result = extract_heuristic_signals(missing)
    assert result["filename"] == "does_not_exist.pdf"
    assert result["front_matter_samples"] == []
    assert result["heading_candidates"] == []
    assert result["version_detected"] is None
    assert result["vendors_detected"] == []


def test_extract_heuristic_signals_corrupt_pdf(tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"not a pdf")
    result = extract_heuristic_signals(corrupt)
    assert result["front_matter_samples"] == []
    assert result["version_detected"] is None
