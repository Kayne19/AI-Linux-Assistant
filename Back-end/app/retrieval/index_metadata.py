import json
import os

from retrieval.retrieval_providers import RetrievalProviderError


class IndexMetadataStore:
    LEGACY_EMBED_PROVIDER = "local"
    LEGACY_EMBED_MODEL = "all-MiniLM-L6-v2"

    def __init__(self, db_path: str, table_name: str, metadata_suffix: str = ".index_meta.json"):
        self.db_path = db_path
        self.table_name = table_name
        self.metadata_suffix = metadata_suffix

    def metadata_path(self):
        return os.path.join(self.db_path, f"{self.table_name}{self.metadata_suffix}")

    def load(self):
        path = self.metadata_path()
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def write(self, embedding_provider):
        os.makedirs(self.db_path, exist_ok=True)
        payload = {
            "embedding": embedding_provider.get_index_metadata(),
        }
        with open(self.metadata_path(), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)

    def _matches_legacy_local_index(self, embedding_provider):
        return (
            embedding_provider.provider_name == self.LEGACY_EMBED_PROVIDER
            and embedding_provider.model_name == self.LEGACY_EMBED_MODEL
        )

    def ensure_embedding_compatibility(self, embedding_provider, require_metadata: bool = False):
        metadata = self.load()
        if metadata is None:
            if require_metadata:
                raise RetrievalProviderError(
                    "This index has no embedding metadata. Re-ingest with the current provider configuration "
                    "before appending documents."
                )
            if self._matches_legacy_local_index(embedding_provider):
                return
            raise RetrievalProviderError(
                "The existing LanceDB index was created before embedding metadata was tracked. "
                "Keep the legacy local embedding configuration or re-ingest before switching embedding providers."
            )

        embedding_metadata = metadata.get("embedding") or {}
        if embedding_provider.is_compatible_with_index(embedding_metadata):
            return

        raise RetrievalProviderError(
            "Configured embedding provider/model is incompatible with the current LanceDB index. "
            f"Index uses {embedding_metadata.get('provider')} / {embedding_metadata.get('model')}; "
            f"current config is {embedding_provider.provider_name} / {embedding_provider.model_name}. "
            "Re-ingest the corpus before switching embedding models."
        )
