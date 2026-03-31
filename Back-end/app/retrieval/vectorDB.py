from retrieval.config import LEGACY_EMBED_MODEL, LEGACY_EMBED_PROVIDER, load_retrieval_config
from retrieval.factory import build_runtime_components
from utils.debug_utils import debug_print


class VectorDB:
    LEGACY_EMBED_PROVIDER = LEGACY_EMBED_PROVIDER
    LEGACY_EMBED_MODEL = LEGACY_EMBED_MODEL

    def __init__(self, embedding_provider=None, reranker_provider=None):
        debug_print("🤖 Loading Models...")
        self._event_listener = None
        components = build_runtime_components(
            config=load_retrieval_config(),
            embedding_provider=embedding_provider,
            reranker_provider=reranker_provider,
            event_listener=self._event_listener,
        )
        self.config = components["config"]
        self.embedding_provider = components["embedding_provider"]
        self.reranker_provider = components["reranker_provider"]
        self._store = components["store"]
        self._metadata_store = components["metadata_store"]
        self._search_pipeline = components["search_pipeline"]

        self.DB_PATH = self.config.db_path
        self.TABLE_NAME = self.config.table_name
        self.INDEX_METADATA_SUFFIX = self.config.index_metadata_suffix

    def set_event_listener(self, listener):
        self._event_listener = listener
        self._search_pipeline.event_listener = listener

    def _index_metadata_path(self):
        return self._metadata_store.metadata_path()

    def _load_index_metadata(self):
        return self._metadata_store.load()

    def _write_index_metadata(self):
        self._metadata_store.write(self.embedding_provider)

    def _embedding_metadata_matches_legacy_local_index(self):
        return (
            self.embedding_provider.provider_name == self.LEGACY_EMBED_PROVIDER
            and self.embedding_provider.model_name == self.LEGACY_EMBED_MODEL
        )

    def _ensure_embedding_compatibility(self, require_metadata=False):
        self._metadata_store.ensure_embedding_compatibility(
            self.embedding_provider,
            require_metadata=require_metadata,
        )

    def retrieve_context(self, query, sources):
        return self._search_pipeline.retrieve_context(query, sources)
