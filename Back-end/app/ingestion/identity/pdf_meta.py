from pathlib import Path

from pypdf import PdfReader


def _clean(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def read_pdf_info(pdf_path: Path) -> dict:
    try:
        reader = PdfReader(str(pdf_path))
        meta = reader.metadata or {}
        return {
            "title": _clean(getattr(meta, "title", None) or meta.get("/Title")),
            "subject": _clean(getattr(meta, "subject", None) or meta.get("/Subject")),
            "author": _clean(getattr(meta, "author", None) or meta.get("/Author")),
            "producer": _clean(getattr(meta, "producer", None) or meta.get("/Producer")),
            "creator": _clean(getattr(meta, "creator", None) or meta.get("/Creator")),
            "keywords": _clean(getattr(meta, "keywords", None) or meta.get("/Keywords")),
        }
    except Exception:
        return {"title": "", "subject": "", "author": "", "producer": "", "creator": "", "keywords": ""}


def _collect_outline_entries(items, level: int, results: list) -> None:
    for item in items:
        if isinstance(item, list):
            _collect_outline_entries(item, level + 1, results)
        else:
            try:
                title = str(item.title) if hasattr(item, "title") else ""
                try:
                    page = item.page.idnum if hasattr(item, "page") and item.page is not None else None
                except Exception:
                    page = None
                results.append({"title": title, "level": level, "page": page})
            except Exception:
                pass


def read_outline(pdf_path: Path) -> list[dict]:
    try:
        reader = PdfReader(str(pdf_path))
        items = reader.outline
        if not items:
            return []
        results: list[dict] = []
        _collect_outline_entries(items, 1, results)
        return results
    except Exception:
        return []
