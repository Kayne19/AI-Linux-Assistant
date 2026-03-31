from retrieval.config import RetrievalConfig, load_retrieval_config
from retrieval.index_metadata import IndexMetadataStore
from retrieval.retrieval_providers import create_embedding_provider, create_reranker_provider
from retrieval.search_pipeline import RetrievalSearchPipeline
from retrieval.store import LanceDBStore


def build_embedding_provider(config: RetrievalConfig, override=None):
    if override is not None:
        return override
    return create_embedding_provider(
        config.embed_provider_name,
        config.embed_model_name,
        device=config.embed_device,
        output_dimension=config.voyage_output_dimension,
    )


def build_reranker_provider(config: RetrievalConfig, override=None):
    if override is not None:
        return override
    return create_reranker_provider(
        config.rerank_provider_name,
        config.rerank_model_name,
        device=config.rerank_device,
    )


def build_store(config: RetrievalConfig):
    return LanceDBStore(config.db_path, config.table_name)


def build_index_metadata_store(config: RetrievalConfig):
    return IndexMetadataStore(config.db_path, config.table_name, config.index_metadata_suffix)


def build_search_pipeline(config: RetrievalConfig, embedding_provider=None, reranker_provider=None, event_listener=None):
    return RetrievalSearchPipeline(
        store=build_store(config),
        metadata_store=build_index_metadata_store(config),
        embedding_provider=build_embedding_provider(config, override=embedding_provider),
        reranker_provider=build_reranker_provider(config, override=reranker_provider),
        event_listener=event_listener,
        initial_fetch=config.initial_fetch,
        final_top_k=config.final_top_k,
        neighbor_pages=config.neighbor_pages,
        max_expanded=config.max_expanded,
        source_profile_sample=config.source_profile_sample,
    )


def build_runtime_components(
    config: RetrievalConfig | None = None,
    embedding_provider=None,
    reranker_provider=None,
    event_listener=None,
):
    config = config or load_retrieval_config()
    resolved_embedding_provider = build_embedding_provider(config, override=embedding_provider)
    resolved_reranker_provider = build_reranker_provider(config, override=reranker_provider)
    return {
        "config": config,
        "store": build_store(config),
        "metadata_store": build_index_metadata_store(config),
        "embedding_provider": resolved_embedding_provider,
        "reranker_provider": resolved_reranker_provider,
        "search_pipeline": build_search_pipeline(
            config,
            embedding_provider=resolved_embedding_provider,
            reranker_provider=resolved_reranker_provider,
            event_listener=event_listener,
        ),
    }
