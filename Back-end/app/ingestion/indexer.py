"""Vector-DB indexer.

The indexer writes two tables in the shared LanceDB directory:

- **chunks** (existing table) — one row per searchable chunk. The T11 schema
  adds ``canonical_source_id``, ``section_path``, ``section_title``,
  ``page_start``/``page_end``, ``chunk_type``, ``local_subsystems``,
  ``entities`` (JSON-encoded), and ``applies_to_override``. ``source`` is now
  the document's ``canonical_title`` instead of its filename so citations
  read naturally. Legacy behavior (source=filename) is preserved when no
  :class:`DocumentIdentity` is supplied.

- **documents** (new table) — one row per ingested document. Populated only
  when an identity is supplied. Enables scoped retrieval in T12.
"""

from __future__ import annotations

import json
import os
import hashlib

from ingestion.identity.schema import DocumentIdentity
from retrieval.factory import (
    build_documents_store,
    build_embedding_provider,
    build_index_metadata_store,
    build_store,
)


_CHUNK_DEFAULTS = {
    "canonical_source_id": "",
    "section_path": [],
    "section_title": "",
    "page_start": 0,
    "page_end": 0,
    "chunk_type": "uncategorized",
    "local_subsystems": [],
    "entities": "{}",
    "applies_to_override": [],
}


def _chunk_metadata_value(metadata: dict, element: dict, key: str, default):
    """Pull *key* from the chunk's per-element metadata, falling back to *default*."""
    if key in metadata:
        return metadata[key]
    if key in element:
        return element[key]
    return default


def _coerce_page_range(metadata: dict, element: dict):
    page = metadata.get("page_number", 0) or 0
    page_start = _chunk_metadata_value(metadata, element, "page_start", page)
    page_end = _chunk_metadata_value(metadata, element, "page_end", page_start)
    return int(page_start or 0), int(page_end or page_start or 0)


def _entities_as_json(raw) -> str:
    if raw is None:
        return "{}"
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw, sort_keys=True)
    except (TypeError, ValueError):
        return "{}"


def _source_key_for(
    json_path: str, raw_data: list[dict], document_identity: DocumentIdentity | None
) -> str:
    if document_identity is not None and document_identity.canonical_source_id:
        return document_identity.canonical_source_id
    source_hint = ""
    for element in raw_data:
        metadata = element.get("metadata", {}) or {}
        source_hint = metadata.get("filename") or element.get("source") or ""
        if source_hint:
            break
    if not source_hint:
        source_hint = os.path.basename(json_path)
    return hashlib.sha256(source_hint.encode("utf-8")).hexdigest()[:16]


class IngestionIndexer:
    def __init__(
        self, store, metadata_store, embedding_provider, *, documents_store=None
    ):
        self.store = store
        self.metadata_store = metadata_store
        self.embedding_provider = embedding_provider
        self.documents_store = documents_store

    def ingest_json(
        self,
        json_path: str,
        *,
        document_identity: DocumentIdentity | None = None,
        force_reingest: bool = False,
    ):
        if not os.path.exists(json_path):
            raise FileNotFoundError(json_path)

        with open(json_path, "r", encoding="utf-8") as handle:
            raw_data = json.load(handle)

        data_to_insert = []
        texts_to_embed = []

        source_display = (
            document_identity.canonical_title if document_identity else None
        )
        canonical_source_id = (
            document_identity.canonical_source_id if document_identity else ""
        )
        source_key = _source_key_for(json_path, raw_data, document_identity)
        id_prefix = f"vec_{source_key}_"

        # --- Idempotency guard ---
        existing_chunk_count = self.store.count_rows_by_id_prefix(id_prefix)
        if existing_chunk_count > 0:
            if not force_reingest:
                return {
                    "rows": 0,
                    "created_table": False,
                    "table_name": self.store.table_name,
                    "skipped": True,
                    "reason": f"{existing_chunk_count} chunk(s) already exist for source_key={source_key}",
                    "document_row": None,
                }
            # Replace: delete old chunks first
            self.store.delete_by_id_prefix(id_prefix)

        for idx, element in enumerate(raw_data):
            metadata = element.get("metadata", {}) or {}
            search_text = metadata.get("embedding_text", element.get("text", ""))
            display_text = (element.get("text", "") or "").strip()

            page_start, page_end = _coerce_page_range(metadata, element)
            chunk_source = (
                source_display
                if source_display
                else metadata.get("filename", "Unknown")
            )

            row = {
                "id": f"vec_{source_key}_{idx:06d}",
                "text": display_text,
                "search_text": search_text,
                "page": metadata.get("page_number", 0),
                "source": chunk_source,
                "type": element.get("type", "Text"),
                "canonical_source_id": canonical_source_id,
                "section_path": _chunk_metadata_value(
                    metadata,
                    element,
                    "section_path",
                    list(_CHUNK_DEFAULTS["section_path"]),
                ),
                "section_title": _chunk_metadata_value(
                    metadata, element, "section_title", _CHUNK_DEFAULTS["section_title"]
                )
                or "",
                "page_start": page_start,
                "page_end": page_end,
                "chunk_type": _chunk_metadata_value(
                    metadata, element, "chunk_type", _CHUNK_DEFAULTS["chunk_type"]
                ),
                "local_subsystems": _chunk_metadata_value(
                    metadata,
                    element,
                    "local_subsystems",
                    list(_CHUNK_DEFAULTS["local_subsystems"]),
                ),
                "entities": _entities_as_json(
                    _chunk_metadata_value(metadata, element, "entities", None)
                ),
                "applies_to_override": _chunk_metadata_value(
                    metadata,
                    element,
                    "applies_to_override",
                    list(_CHUNK_DEFAULTS["applies_to_override"]),
                ),
            }

            texts_to_embed.append(search_text)
            data_to_insert.append(row)

        if self.store.table_exists():
            self.metadata_store.ensure_embedding_compatibility(
                self.embedding_provider,
                require_metadata=False,
            )

        vectors = self.embedding_provider.embed_documents(
            texts_to_embed, show_progress_bar=True
        )
        for index, row in enumerate(data_to_insert):
            row["vector"] = vectors[index]

        created_table = self.store.add_rows(data_to_insert)
        self.store.rebuild_fts_index("search_text")
        self.metadata_store.write(self.embedding_provider)

        doc_result = None
        if document_identity is not None:
            doc_result = self._write_document_row(
                document_identity, force_reingest=force_reingest
            )

        return {
            "rows": len(data_to_insert),
            "created_table": created_table,
            "table_name": self.store.table_name,
            "document_row": doc_result,
        }

    def _write_document_row(
        self, identity: DocumentIdentity, *, force_reingest: bool = False
    ) -> dict:
        """Write the identity row into the ``documents`` table.

        Idempotent: if a row already exists for this canonical_source_id,
        return without writing unless *force_reingest* is True, in which case
        the old row is deleted first.

        Falls back silently when no documents store is configured so legacy
        callers keep working.
        """
        if self.documents_store is None:
            return {"written": False, "reason": "no documents_store"}

        escaped_id = identity.canonical_source_id.replace("'", "''")
        predicate = f"canonical_source_id = '{escaped_id}'"
        existing_count = self.documents_store.count_rows_matching(predicate)

        if existing_count > 0:
            if not force_reingest:
                return {
                    "written": False,
                    "created_table": False,
                    "table_name": self.documents_store.table_name,
                    "canonical_source_id": identity.canonical_source_id,
                    "skipped": True,
                    "reason": "document row already exists",
                }
            self.documents_store.delete_by_predicate(predicate)

        row = _document_identity_to_row(identity)
        created = self.documents_store.add_rows([row])
        return {
            "written": True,
            "created_table": created,
            "table_name": self.documents_store.table_name,
            "canonical_source_id": identity.canonical_source_id,
        }


def _document_identity_to_row(identity: DocumentIdentity) -> dict:
    """Convert a :class:`DocumentIdentity` to a LanceDB-friendly row dict.

    ``entities``-shaped nested objects don't live on documents, but
    ``title_aliases`` / enum lists are list[str] which LanceDB handles
    natively. Optional strings become ``""`` to keep the column type stable
    at the first row.
    """
    return {
        "canonical_source_id": identity.canonical_source_id,
        "canonical_title": identity.canonical_title,
        "title_aliases": list(identity.title_aliases),
        "source_family": identity.source_family,
        "product": identity.product or "",
        "vendor_or_project": identity.vendor_or_project,
        "version": identity.version or "",
        "release_date": identity.release_date or "",
        "doc_kind": identity.doc_kind,
        "trust_tier": identity.trust_tier,
        "freshness_status": identity.freshness_status,
        "os_family": identity.os_family,
        "init_systems": list(identity.init_systems),
        "package_managers": list(identity.package_managers),
        "major_subsystems": list(identity.major_subsystems),
        "applies_to": list(identity.applies_to),
        "source_url": identity.source_url or "",
        "ingest_source_type": identity.ingest_source_type,
        "operator_override_present": bool(identity.operator_override_present),
        "ingested_at": identity.ingested_at or "",
        "pipeline_version": identity.pipeline_version,
    }


def build_ingestion_indexer(retrieval_config):
    return IngestionIndexer(
        store=build_store(retrieval_config),
        metadata_store=build_index_metadata_store(retrieval_config),
        embedding_provider=build_embedding_provider(retrieval_config),
        documents_store=build_documents_store(retrieval_config),
    )
