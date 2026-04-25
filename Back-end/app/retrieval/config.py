import os
from dataclasses import dataclass

from config.settings import load_effective_settings


@dataclass(frozen=True)
class RetrievalConfig:
    db_path: str
    table_name: str
    index_metadata_suffix: str
    embed_provider_name: str
    embed_model_name: str
    rerank_provider_name: str
    rerank_model_name: str
    initial_fetch: int
    final_top_k: int
    neighbor_pages: int
    max_expanded: int
    source_profile_sample: int
    embed_device: str | None
    rerank_device: str | None
    voyage_output_dimension: int | None
    documents_table_name: str = "documents"
    # Scope pre-narrowing (T12)
    scope_min_hit_count: int = 3
    scope_min_top_score: float = 2.0
    scope_max_widenings: int = 2


LEGACY_EMBED_PROVIDER = "local"
LEGACY_EMBED_MODEL = "all-MiniLM-L6-v2"


def _parse_optional_int(raw_value):
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except ValueError:
        return None


def load_retrieval_config() -> RetrievalConfig:
    settings = load_effective_settings()
    return RetrievalConfig(
        db_path="lancedb_data",
        table_name="debian_manual",
        index_metadata_suffix=".index_meta.json",
        embed_provider_name=os.getenv("VECTORDB_EMBED_PROVIDER", "voyage").strip() or "voyage",
        embed_model_name=os.getenv("VECTORDB_EMBED_MODEL", "voyage-4").strip() or "voyage-4",
        rerank_provider_name=os.getenv("VECTORDB_RERANK_PROVIDER", "voyage").strip() or "voyage",
        rerank_model_name=os.getenv("VECTORDB_RERANK_MODEL", "rerank-2.5-lite").strip()
        or "rerank-2.5-lite",
        initial_fetch=settings.retrieval_initial_fetch,
        final_top_k=settings.retrieval_final_top_k,
        neighbor_pages=settings.retrieval_neighbor_pages,
        max_expanded=settings.retrieval_max_expanded,
        source_profile_sample=settings.retrieval_source_profile_sample,
        embed_device=os.getenv("VECTORDB_EMBED_DEVICE", "").strip() or None,
        rerank_device=os.getenv("VECTORDB_RERANK_DEVICE", "cuda").strip() or "cuda",
        voyage_output_dimension=_parse_optional_int(os.getenv("VECTORDB_VOYAGE_OUTPUT_DIMENSION", "")),
    )
