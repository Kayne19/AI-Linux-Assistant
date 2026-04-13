from types import SimpleNamespace

from retrieval.config import load_retrieval_config


def test_load_retrieval_config_uses_effective_settings(monkeypatch):
    monkeypatch.setattr(
        "retrieval.config.load_effective_settings",
        lambda: SimpleNamespace(
            retrieval_initial_fetch=52,
            retrieval_final_top_k=14,
            retrieval_neighbor_pages=4,
            retrieval_max_expanded=70,
            retrieval_source_profile_sample=8000,
        ),
    )
    monkeypatch.setenv("VECTORDB_EMBED_PROVIDER", "voyage")
    monkeypatch.setenv("VECTORDB_EMBED_MODEL", "voyage-4")
    monkeypatch.setenv("VECTORDB_RERANK_PROVIDER", "voyage")
    monkeypatch.setenv("VECTORDB_RERANK_MODEL", "rerank-2.5-lite")

    config = load_retrieval_config()

    assert config.initial_fetch == 52
    assert config.final_top_k == 14
    assert config.neighbor_pages == 4
    assert config.max_expanded == 70
    assert config.source_profile_sample == 8000
