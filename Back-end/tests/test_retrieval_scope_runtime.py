from types import SimpleNamespace

import pandas as pd

from retrieval.search_pipeline import RetrievalSearchPipeline


DOCUMENTS = [
    {
        "canonical_source_id": "proxmox-ve-8-admin-guide",
        "canonical_title": "Proxmox VE Admin Guide",
        "os_family": "linux",
        "source_family": "proxmox",
        "major_subsystems": ["filesystems", "virtualization"],
        "trust_tier": "canonical",
        "freshness_status": "current",
    },
    {
        "canonical_source_id": "debian-admin-handbook",
        "canonical_title": "Debian Administrator's Handbook",
        "os_family": "linux",
        "source_family": "debian",
        "package_managers": ["apt"],
        "init_systems": ["systemd"],
        "trust_tier": "official",
        "freshness_status": "current",
    },
    {
        "canonical_source_id": "arch-wiki",
        "canonical_title": "Arch Wiki",
        "os_family": "linux",
        "source_family": "arch",
        "package_managers": ["pacman"],
        "init_systems": ["systemd"],
        "trust_tier": "community",
        "freshness_status": "current",
    },
]


CHUNKS = [
    {
        "id": "pve-47",
        "canonical_source_id": "proxmox-ve-8-admin-guide",
        "canonical_title": "Proxmox VE Admin Guide",
        "source": "Proxmox.pdf",
        "page": 47,
        "text": "Create a ZFS pool in Proxmox.",
        "search_text": "Create a ZFS pool in Proxmox.",
    },
    {
        "id": "deb-10",
        "canonical_source_id": "debian-admin-handbook",
        "canonical_title": "Debian Administrator's Handbook",
        "source": "Debian.pdf",
        "page": 10,
        "text": "Install packages with apt.",
        "search_text": "Install packages with apt.",
    },
    {
        "id": "arch-5",
        "canonical_source_id": "arch-wiki",
        "canonical_title": "Arch Wiki",
        "source": "Arch.pdf",
        "page": 5,
        "text": "Install packages with pacman.",
        "search_text": "Install packages with pacman.",
    },
]


class FakeStore:
    def __init__(self):
        self.scoped_calls = []

    def open_table(self):
        return object()

    def search_hybrid_scoped(self, query_vector, query_text, limit, canonical_source_ids):
        del query_vector, query_text, limit
        self.scoped_calls.append(tuple(canonical_source_ids or ()))
        allowed = set(canonical_source_ids or ())
        return [dict(row) for row in CHUNKS if not allowed or row["canonical_source_id"] in allowed]

    def fetch_source_page_window(self, source, page_start, page_end, limit=None):
        del page_start, page_end, limit
        return [dict(row) for row in CHUNKS if row["source"] == source]

    def fetch_canonical_page_window(self, canonical_source_id, page_start, page_end, limit=None):
        del page_start, page_end, limit
        return [dict(row) for row in CHUNKS if row["canonical_source_id"] == canonical_source_id]

    def sample_rows(self, limit):
        del limit
        return pd.DataFrame(CHUNKS)


class FakeDocumentsStore:
    def __init__(self, documents):
        self.documents = list(documents)
        self.load_count = 0

    def load_documents(self):
        self.load_count += 1
        return [dict(row) for row in self.documents]


class FakeMetadataStore:
    def ensure_embedding_compatibility(self, embedding_provider, require_metadata=False):
        del embedding_provider, require_metadata


class FakeEmbeddingProvider:
    def embed_query(self, query):
        del query
        return [0.0]


class FakeRerankerProvider:
    def rerank(self, query, documents):
        del query
        return [float(len(documents) - index) for index, _ in enumerate(documents)]


def build_pipeline(monkeypatch, *, min_hit_count=1, min_top_score=2.0, max_widenings=2):
    events = []
    store = FakeStore()
    documents_store = FakeDocumentsStore(DOCUMENTS)
    monkeypatch.setattr(
        "retrieval.search_pipeline.load_retrieval_config",
        lambda: SimpleNamespace(
            scope_min_hit_count=min_hit_count,
            scope_min_top_score=min_top_score,
            scope_max_widenings=max_widenings,
        ),
    )
    pipeline = RetrievalSearchPipeline(
        store=store,
        documents_store=documents_store,
        metadata_store=FakeMetadataStore(),
        embedding_provider=FakeEmbeddingProvider(),
        reranker_provider=FakeRerankerProvider(),
        event_listener=lambda event_type, payload: events.append((event_type, payload)),
        initial_fetch=10,
        final_top_k=1,
        neighbor_pages=0,
        max_expanded=10,
        source_profile_sample=20,
    )
    return pipeline, store, events, documents_store


def scope_event(events):
    matches = [payload for event_type, payload in events if event_type == "retrieval_scope_selected"]
    assert len(matches) == 1
    return matches[0]


def test_query_scope_selects_proxmox_doc_without_widening(monkeypatch):
    pipeline, store, events, _ = build_pipeline(monkeypatch)

    result = pipeline.retrieve_context_result("how do I create a ZFS pool on proxmox", [])

    event = scope_event(events)
    assert event["candidate_doc_ids"][0] == "proxmox-ve-8-admin-guide"
    assert event["widenings_taken"] == 0
    assert store.scoped_calls == [("proxmox-ve-8-admin-guide",)]
    assert result["retrieval_metadata"]["delivered_bundle_count"] == 1


def test_router_hint_linux_scope_can_widen_once_and_keep_linux_docs(monkeypatch):
    pipeline, store, events, _ = build_pipeline(monkeypatch, min_hit_count=4, max_widenings=1)

    pipeline.retrieve_context_result("general maintenance", [], router_hint={"os_family": "linux"})

    event = scope_event(events)
    assert event["widenings_taken"] == 1
    assert set(event["candidate_doc_ids"]) == {
        "proxmox-ve-8-admin-guide",
        "debian-admin-handbook",
        "arch-wiki",
    }
    assert set(store.scoped_calls[0]) == set(event["candidate_doc_ids"])


def test_query_with_no_scope_signals_fully_widens_to_configured_limit(monkeypatch):
    pipeline, _, events, _ = build_pipeline(monkeypatch, min_hit_count=10, max_widenings=2)

    pipeline.retrieve_context_result("general advice on doing backups", [])

    event = scope_event(events)
    assert event["widenings_taken"] == 2
    assert event["candidate_doc_ids"] == [
        "proxmox-ve-8-admin-guide",
        "debian-admin-handbook",
        "arch-wiki",
    ]


def test_explicit_doc_ids_bypass_scope_selection(monkeypatch):
    pipeline, store, events, _ = build_pipeline(monkeypatch)

    pipeline.retrieve_context_result(
        "package install",
        [],
        explicit_doc_ids=("proxmox-ve-8-admin-guide",),
    )

    event = scope_event(events)
    assert event["candidate_doc_ids"] == ["proxmox-ve-8-admin-guide"]
    assert event["winning_filter"]["explicit_doc_ids"] == ["proxmox-ve-8-admin-guide"]
    assert store.scoped_calls == [("proxmox-ve-8-admin-guide",)]
