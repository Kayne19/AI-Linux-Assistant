from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


def sidecar_path(pdf_path: Path) -> Path | None:
    for suffix in (".meta.yaml", ".meta.yml"):
        candidate = pdf_path.with_suffix(suffix)
        if candidate.exists():
            return candidate
    return None


def load_sidecar(pdf_path: Path) -> dict | None:
    path = sidecar_path(pdf_path)
    if path is None:
        return None
    if yaml is None:
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data
