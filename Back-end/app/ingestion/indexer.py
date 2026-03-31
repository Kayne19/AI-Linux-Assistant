import json
import os

from retrieval.factory import build_embedding_provider, build_index_metadata_store, build_store


class IngestionIndexer:
    def __init__(self, store, metadata_store, embedding_provider):
        self.store = store
        self.metadata_store = metadata_store
        self.embedding_provider = embedding_provider

    def ingest_json(self, json_path: str):
        if not os.path.exists(json_path):
            raise FileNotFoundError(json_path)

        with open(json_path, "r", encoding="utf-8") as handle:
            raw_data = json.load(handle)

        data_to_insert = []
        texts_to_embed = []

        for idx, element in enumerate(raw_data):
            search_text = element.get("metadata", {}).get("embedding_text", element.get("text", ""))
            display_text = (element.get("text", "") or "").strip()
            metadata = element.get("metadata", {}) or {}

            texts_to_embed.append(search_text)
            data_to_insert.append(
                {
                    "id": f"vec_{idx}",
                    "text": display_text,
                    "search_text": search_text,
                    "page": metadata.get("page_number", 0),
                    "source": metadata.get("filename", "Unknown"),
                    "type": element.get("type", "Text"),
                }
            )

        if self.store.table_exists():
            self.metadata_store.ensure_embedding_compatibility(
                self.embedding_provider,
                require_metadata=False,
            )

        vectors = self.embedding_provider.embed_documents(texts_to_embed, show_progress_bar=True)
        for index, row in enumerate(data_to_insert):
            row["vector"] = vectors[index]

        created_table = self.store.add_rows(data_to_insert)
        self.store.rebuild_fts_index("search_text")
        self.metadata_store.write(self.embedding_provider)
        return {
            "rows": len(data_to_insert),
            "created_table": created_table,
            "table_name": self.store.table_name,
        }


def build_ingestion_indexer(retrieval_config):
    return IngestionIndexer(
        store=build_store(retrieval_config),
        metadata_store=build_index_metadata_store(retrieval_config),
        embedding_provider=build_embedding_provider(retrieval_config),
    )
