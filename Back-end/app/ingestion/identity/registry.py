"""JSON document registry — persists DocumentIdentity rows to routing_documents.json.

T11 will additionally write to LanceDB. This module is JSON-only.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ingestion.identity.schema import DocumentIdentity


DOCUMENT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2] / "orchestration" / "routing_documents.json"
)


def _stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def load_document_registry(path: Path | None = None) -> dict:
    """Load the {documents: [...]} registry. Returns {"documents": []} if file missing."""
    target = path or DOCUMENT_REGISTRY_PATH
    if not target.exists():
        return {"documents": []}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("documents"), list):
            return data
        return {"documents": []}
    except Exception:
        return {"documents": []}


def save_document_registry(registry: dict, path: Path | None = None) -> None:
    """Write pretty JSON atomically (write tmp, rename)."""
    target = path or DOCUMENT_REGISTRY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)


def upsert_document(
    identity: DocumentIdentity,
    *,
    path: Path | None = None,
    ingested_at: str | None = None,
) -> tuple[bool, str]:
    """Insert or update by canonical_source_id. Stamps ingested_at. Returns (changed, message)."""
    registry = load_document_registry(path)
    row = identity.to_dict()
    row["ingested_at"] = ingested_at or _stamp_now()

    cid = row["canonical_source_id"]
    documents: list[dict] = registry["documents"]

    for i, existing in enumerate(documents):
        if existing.get("canonical_source_id") == cid:
            documents[i] = row
            registry["documents"] = documents
            save_document_registry(registry, path)
            return True, "updated"

    documents.append(row)
    registry["documents"] = documents
    save_document_registry(registry, path)
    return True, "added"


def get_document(canonical_source_id: str, *, path: Path | None = None) -> dict | None:
    """Fetch one document row by id; returns None if absent."""
    registry = load_document_registry(path)
    for row in registry["documents"]:
        if row.get("canonical_source_id") == canonical_source_id:
            return row
    return None


def list_documents(*, path: Path | None = None) -> list[dict]:
    """Return all document rows."""
    registry = load_document_registry(path)
    return registry["documents"]
