from dataclasses import replace

from retrieval.config import LEGACY_EMBED_MODEL, LEGACY_EMBED_PROVIDER, load_retrieval_config
from retrieval.factory import build_runtime_components
from retrieval.search_pipeline import RetrievalSearchPipeline
from utils.debug_utils import debug_print


class VectorDB:
    LEGACY_EMBED_PROVIDER = LEGACY_EMBED_PROVIDER
    LEGACY_EMBED_MODEL = LEGACY_EMBED_MODEL

    def __init__(
        self,
        embedding_provider=None,
        reranker_provider=None,
        runtime_components=None,
        config=None,
        db_path=None,
        table_name=None,
        index_metadata_suffix=None,
    ):
        debug_print("🤖 Loading Models...")
        self._event_listener = None
        self._runtime_config_mutable = runtime_components is None
        resolved_config = config or load_retrieval_config()
        if db_path is not None:
            resolved_config = replace(resolved_config, db_path=db_path)
        if table_name is not None:
            resolved_config = replace(resolved_config, table_name=table_name)
        if index_metadata_suffix is not None:
            resolved_config = replace(resolved_config, index_metadata_suffix=index_metadata_suffix)
        if runtime_components is None:
            components = build_runtime_components(
                config=resolved_config,
                embedding_provider=embedding_provider,
                reranker_provider=reranker_provider,
                event_listener=self._event_listener,
            )
        else:
            components = runtime_components
        self._apply_runtime_components(components)

    def set_event_listener(self, listener):
        self._event_listener = listener
        self._search_pipeline.event_listener = listener

    @property
    def DB_PATH(self):
        return self.config.db_path

    @DB_PATH.setter
    def DB_PATH(self, value):
        self._reconfigure_runtime(db_path=value)

    @property
    def TABLE_NAME(self):
        return self.config.table_name

    @TABLE_NAME.setter
    def TABLE_NAME(self, value):
        self._reconfigure_runtime(table_name=value)

    @property
    def INDEX_METADATA_SUFFIX(self):
        return self.config.index_metadata_suffix

    @INDEX_METADATA_SUFFIX.setter
    def INDEX_METADATA_SUFFIX(self, value):
        self._reconfigure_runtime(index_metadata_suffix=value)

    def _apply_runtime_components(self, components):
        self.config = components["config"]
        self.embedding_provider = components["embedding_provider"]
        self.reranker_provider = components["reranker_provider"]
        self._store = components["store"]
        self._metadata_store = components["metadata_store"]
        self._search_pipeline = RetrievalSearchPipeline(
            store=self._store,
            metadata_store=self._metadata_store,
            embedding_provider=self.embedding_provider,
            reranker_provider=self.reranker_provider,
            event_listener=self._event_listener,
            initial_fetch=self.config.initial_fetch,
            final_top_k=self.config.final_top_k,
            neighbor_pages=self.config.neighbor_pages,
            max_expanded=self.config.max_expanded,
            source_profile_sample=self.config.source_profile_sample,
        )

    def _reconfigure_runtime(self, *, db_path=None, table_name=None, index_metadata_suffix=None):
        if not self._runtime_config_mutable:
            raise AttributeError("This VectorDB instance uses shared runtime components and cannot be reconfigured.")
        next_config = self.config
        if db_path is not None:
            next_config = replace(next_config, db_path=db_path)
        if table_name is not None:
            next_config = replace(next_config, table_name=table_name)
        if index_metadata_suffix is not None:
            next_config = replace(next_config, index_metadata_suffix=index_metadata_suffix)
        self._apply_runtime_components(
            build_runtime_components(
                config=next_config,
                embedding_provider=self.embedding_provider,
                reranker_provider=self.reranker_provider,
                event_listener=self._event_listener,
            )
        )

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
