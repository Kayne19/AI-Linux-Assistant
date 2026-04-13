import pandas as pd

from retrieval.search_pipeline import RetrievalSearchPipeline


class FakeStore:
    def __init__(self, *, candidates=None, window_rows=None, sample_rows=None):
        self.candidates = list(candidates or [])
        self.window_rows = {
            (source, int(page_start), int(page_end)): list(rows)
            for (source, page_start, page_end), rows in (window_rows or {}).items()
        }
        self.sample_frame = pd.DataFrame(sample_rows or [])
        self.window_calls = []

    def open_table(self):
        return object()

    def search_hybrid(self, query_vector, query_text, limit):
        del query_vector, query_text, limit
        return [dict(row) for row in self.candidates]

    def fetch_source_page_window(self, source, page_start, page_end, limit=None):
        del limit
        self.window_calls.append((source, page_start, page_end))
        return [
            dict(row)
            for row in self.window_rows.get((source, int(page_start), int(page_end)), [])
        ]

    def sample_rows(self, limit):
        del limit
        return self.sample_frame


class FakeMetadataStore:
    def ensure_embedding_compatibility(self, embedding_provider, require_metadata=False):
        del embedding_provider, require_metadata


class FakeEmbeddingProvider:
    def embed_query(self, query):
        del query
        return [0.0]


class FakeRerankerProvider:
    def __init__(self, score_by_text):
        self.score_by_text = dict(score_by_text)

    def rerank(self, query, documents):
        del query
        return [float(self.score_by_text.get(document, 0.0)) for document in documents]


def build_pipeline(*, candidates, window_rows, score_by_text, initial_fetch=10, final_top_k=2, neighbor_pages=2, max_expanded=20):
    store = FakeStore(
        candidates=candidates,
        window_rows=window_rows,
        sample_rows=[
            {"source": row.get("source"), "search_text": row.get("search_text", "")}
            for row in candidates
        ],
    )
    return RetrievalSearchPipeline(
        store=store,
        metadata_store=FakeMetadataStore(),
        embedding_provider=FakeEmbeddingProvider(),
        reranker_provider=FakeRerankerProvider(score_by_text),
        initial_fetch=initial_fetch,
        final_top_k=final_top_k,
        neighbor_pages=neighbor_pages,
        max_expanded=max_expanded,
        source_profile_sample=50,
    ), store


def test_retrieval_fetches_true_neighbors_and_preserves_them_as_final_block():
    candidates = [
        {
            "id": "vec_10",
            "source": "Debian.pdf",
            "page": 10,
            "text": "Install anchor",
            "search_text": "Install anchor",
        },
        {
            "id": "vec_30",
            "source": "Debian.pdf",
            "page": 30,
            "text": "Other topic",
            "search_text": "Other topic",
        },
    ]
    window_rows = {
        ("Debian.pdf", 8, 12): [
            {"id": "vec_8", "source": "Debian.pdf", "page": 8, "text": "Prep", "search_text": "Prep"},
            {"id": "vec_9", "source": "Debian.pdf", "page": 9, "text": "Deps", "search_text": "Deps"},
            {"id": "vec_10", "source": "Debian.pdf", "page": 10, "text": "Install anchor", "search_text": "Install anchor"},
            {"id": "vec_11", "source": "Debian.pdf", "page": 11, "text": "Flags", "search_text": "Flags"},
            {"id": "vec_12", "source": "Debian.pdf", "page": 12, "text": "Cleanup", "search_text": "Cleanup"},
        ],
    }
    pipeline, store = build_pipeline(
        candidates=candidates,
        window_rows=window_rows,
        score_by_text={
            "Install anchor": 10.0,
            "Other topic": 1.0,
        },
        final_top_k=1,
        neighbor_pages=2,
        max_expanded=10,
    )

    result = pipeline.retrieve_context_result("install package", ["debian"])

    assert store.window_calls == [("Debian.pdf", 8, 12)]
    assert result["merged_blocks"] == [
        {
            "source": "Debian.pdf",
            "pages": [8, 9, 10, 11, 12],
            "page_label": "Pages 8-12",
            "text": "Prep\n\nDeps\n\nInstall anchor\n\nFlags\n\nCleanup",
            "bundle_key": "bundle:Debian.pdf:8-12:anchor:vec_10",
            "block_key": "block:Debian.pdf:8-12",
            "page_window_key": "window:Debian.pdf:8-12",
        }
    ]
    metadata = result["retrieval_metadata"]
    assert metadata["anchor_count"] == 1
    assert metadata["anchor_pages"] == [10]
    assert metadata["fetched_neighbor_pages"] == [{"source": "Debian.pdf", "pages": [8, 9, 11, 12]}]
    assert metadata["delivered_bundle_count"] == 1
    assert metadata["delivered_page_window_keys"] == ["window:Debian.pdf:8-12"]


def test_retrieval_applies_max_expanded_at_bundle_boundaries():
    candidates = [
        {"id": "vec_10", "source": "Debian.pdf", "page": 10, "text": "Anchor A", "search_text": "Anchor A"},
        {"id": "vec_30", "source": "Debian.pdf", "page": 30, "text": "Anchor B", "search_text": "Anchor B"},
    ]
    window_rows = {
        ("Debian.pdf", 8, 12): [
            {"id": f"vec_{page}", "source": "Debian.pdf", "page": page, "text": f"A {page}", "search_text": f"A {page}"}
            for page in range(8, 13)
        ],
        ("Debian.pdf", 28, 32): [
            {"id": f"vec_{page}", "source": "Debian.pdf", "page": page, "text": f"B {page}", "search_text": f"B {page}"}
            for page in range(28, 33)
        ],
    }
    pipeline, _ = build_pipeline(
        candidates=candidates,
        window_rows=window_rows,
        score_by_text={"Anchor A": 9.0, "Anchor B": 8.0},
        final_top_k=2,
        neighbor_pages=2,
        max_expanded=6,
    )

    result = pipeline.retrieve_context_result("install package", ["debian"])

    assert [block["pages"] for block in result["merged_blocks"]] == [[8, 9, 10, 11, 12]]
    metadata = result["retrieval_metadata"]
    assert metadata["delivered_bundle_count"] == 1
    assert metadata["skipped_bundle_count"] == 1


def test_page_less_rows_stay_singleton_non_expandable_bundles():
    candidates = [
        {
            "id": "vec_unpaged",
            "source": "Notes.md",
            "page": 0,
            "text": "Unpaged note",
            "search_text": "Unpaged note",
        }
    ]
    pipeline, store = build_pipeline(
        candidates=candidates,
        window_rows={},
        score_by_text={"Unpaged note": 7.0},
        final_top_k=1,
        neighbor_pages=3,
        max_expanded=5,
    )

    result = pipeline.retrieve_context_result("note", [])

    assert store.window_calls == []
    assert result["merged_blocks"] == [
        {
            "source": "Notes.md",
            "pages": [],
            "page_label": "Page ?",
            "text": "Unpaged note",
            "bundle_key": "bundle:Notes.md:singleton:vec_unpaged",
            "block_key": "block:Notes.md:singleton:vec_unpaged",
            "page_window_key": None,
        }
    ]
    assert result["bundle_summaries"][0]["page_less"] is True
    assert result["bundle_summaries"][0]["requested_page_window_key"] is None
