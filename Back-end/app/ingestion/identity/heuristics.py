import re
from pathlib import Path

from pypdf import PdfReader

from ingestion.identity import pdf_meta as _pdf_meta


_VENDOR_TOKENS = [
    "Proxmox", "Debian", "Ubuntu", "Red Hat", "RHEL", "CentOS", "Fedora",
    "SUSE", "openSUSE", "Arch Linux", "Docker", "Kubernetes", "Linux Foundation",
    "Canonical", "systemd", "BSD", "FreeBSD", "OpenBSD",
]

_VERSION_PATTERNS = [
    # vendor-anchored versions — captured group is the version number
    (re.compile(
        r"(?:Proxmox VE|Debian|Ubuntu|RHEL|CentOS)\s+([\d.]+)",
        re.IGNORECASE,
    ), True),
    # "Version X.Y", "Release X.Y", "vX.Y.Z" at line/title context
    (re.compile(
        r"(?:version|release|v)\s*(v?\d+\.\d+(?:\.\d+)?)",
        re.IGNORECASE,
    ), False),
    # bare "vX.Y.Z"
    (re.compile(r"\bv(\d+\.\d+(?:\.\d+)?)\b"), False),
    # bare version at line start (e.g. "9.0 release notes")
    (re.compile(r"^\s*(\d+\.\d+(?:\.\d+)?)\b", re.MULTILINE), False),
    # ISO date as weak fallback
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"), False),
]


def _normalize_whitespace(text: str) -> str:
    return " ".join((text or "").split())


def _clean_pdf_meta_value(value) -> str:
    if value is None:
        return ""
    return _normalize_whitespace(str(value))


def _looks_like_heading(line: str) -> bool:
    line = _normalize_whitespace(line)
    if not line:
        return False
    if len(line) < 5 or len(line) > 120:
        return False
    if line.endswith((".", ",", ";", ":")):
        return False

    words = line.split()
    if len(words) > 14:
        return False

    alpha_words = [word for word in words if any(ch.isalpha() for ch in word)]
    if not alpha_words:
        return False

    titleish_words = 0
    for word in alpha_words:
        stripped = word.strip("()[]{}<>-_/\\")
        if not stripped:
            continue
        if stripped.isupper() or stripped[:1].isupper():
            titleish_words += 1

    return (titleish_words / len(alpha_words)) >= 0.7


def collect_front_matter(pdf_path: Path) -> dict:
    reader = PdfReader(str(pdf_path))
    metadata = reader.metadata or {}

    first_page_samples = []
    heading_candidates = []
    seen_headings: set[str] = set()

    for page in reader.pages[:5]:
        page_text = page.extract_text() or ""
        normalized_page = _normalize_whitespace(page_text)
        if normalized_page:
            first_page_samples.append(normalized_page[:1200])

        for raw_line in page_text.splitlines():
            line = _normalize_whitespace(raw_line)
            if not _looks_like_heading(line):
                continue
            dedupe_key = line.lower()
            if dedupe_key in seen_headings:
                continue
            seen_headings.add(dedupe_key)
            heading_candidates.append(line)
            if len(heading_candidates) >= 20:
                break
        if len(heading_candidates) >= 20:
            break

    return {
        "filename": pdf_path.name,
        "stem": pdf_path.stem,
        "metadata": {
            "title": _clean_pdf_meta_value(getattr(metadata, "title", None) or metadata.get("/Title")),
            "subject": _clean_pdf_meta_value(getattr(metadata, "subject", None) or metadata.get("/Subject")),
            "author": _clean_pdf_meta_value(getattr(metadata, "author", None) or metadata.get("/Author")),
            "producer": _clean_pdf_meta_value(getattr(metadata, "producer", None) or metadata.get("/Producer")),
        },
        "front_matter_samples": first_page_samples[:3],
        "heading_candidates": heading_candidates[:15],
    }


def detect_version(text: str) -> str | None:
    for pattern, use_group_1 in _VERSION_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1) if use_group_1 else m.group(1)
    return None


def detect_vendor_strings(text: str) -> list[str]:
    seen_lower: set[str] = set()
    results: list[str] = []
    for token in _VENDOR_TOKENS:
        if len(results) >= 10:
            break
        if re.search(re.escape(token), text, re.IGNORECASE):
            lower = token.lower()
            if lower not in seen_lower:
                seen_lower.add(lower)
                # preserve casing as it appears in text
                m = re.search(re.escape(token), text, re.IGNORECASE)
                results.append(m.group(0) if m else token)
    return results


def detect_from_outline(outline: list[dict]) -> dict:
    if not outline:
        return {"top_title": None, "depth": 0, "entry_count": 0}
    top_title = next(
        (entry["title"] for entry in outline if entry.get("level") == 1),
        None,
    )
    depth = max((entry.get("level") or 0) for entry in outline)
    return {"top_title": top_title, "depth": depth, "entry_count": len(outline)}


def extract_heuristic_signals(pdf_path: Path) -> dict:
    try:
        base = collect_front_matter(pdf_path)
    except Exception:
        base = {
            "filename": pdf_path.name,
            "stem": pdf_path.stem,
            "metadata": {"title": "", "subject": "", "author": "", "producer": ""},
            "front_matter_samples": [],
            "heading_candidates": [],
        }

    version_detected: str | None = None
    vendors_detected: list[str] = []
    outline_summary: dict = {"top_title": None, "depth": 0, "entry_count": 0}

    try:
        search_text = " ".join(filter(None, [
            base["metadata"].get("title"),
            base["metadata"].get("subject"),
            *base.get("front_matter_samples", []),
            *base.get("heading_candidates", []),
        ]))
        version_detected = detect_version(search_text)
        vendors_detected = detect_vendor_strings(search_text)
        outline = _pdf_meta.read_outline(pdf_path)
        outline_summary = detect_from_outline(outline)
    except Exception:
        pass

    return {
        **base,
        "version_detected": version_detected,
        "vendors_detected": vendors_detected,
        "outline_summary": outline_summary,
    }
