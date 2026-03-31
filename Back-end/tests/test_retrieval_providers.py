import tempfile

from retrieval.retrieval_providers import RetrievalProviderError
from retrieval.vectorDB import VectorDB


class FakeEmbeddingProvider:
    def __init__(self, provider_name, model_name, compatibility_key=None):
        self.provider_name = provider_name
        self.model_name = model_name
        self.compatibility_key = compatibility_key or f"{provider_name}:{model_name}"

    def embed_documents(self, texts, show_progress_bar=False):
        del show_progress_bar
        return [[0.0] * 4 for _ in texts]

    def embed_query(self, text):
        del text
        return [0.0] * 4

    def get_index_metadata(self):
        return {
            "provider": self.provider_name,
            "model": self.model_name,
            "compatibility_key": self.compatibility_key,
        }

    def is_compatible_with_index(self, index_metadata):
        return index_metadata.get("compatibility_key") == self.compatibility_key


class FakeRerankerProvider:
    provider_name = "fake"
    model_name = "fake-reranker"

    def rerank(self, query, documents):
        del query
        return [0.0] * len(documents)


def _build_vectordb(tmpdir, embedding_provider):
    db = VectorDB(
        embedding_provider=embedding_provider,
        reranker_provider=FakeRerankerProvider(),
    )
    db.DB_PATH = tmpdir
    db.TABLE_NAME = "unit_test_table"
    return db


def test_vectordb_allows_legacy_local_index_without_metadata():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = _build_vectordb(
            tmpdir,
            FakeEmbeddingProvider("local", "all-MiniLM-L6-v2"),
        )
        db._ensure_embedding_compatibility(require_metadata=False)


def test_vectordb_rejects_nonlegacy_provider_without_metadata():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = _build_vectordb(
            tmpdir,
            FakeEmbeddingProvider("voyage", "voyage-4", compatibility_key="voyage:series-4"),
        )
        try:
            db._ensure_embedding_compatibility(require_metadata=False)
            assert False, "Expected RetrievalProviderError for provider/index mismatch"
        except RetrievalProviderError as exc:
            assert "re-ingest" in str(exc).lower()


def test_vectordb_accepts_matching_index_metadata():
    with tempfile.TemporaryDirectory() as tmpdir:
        provider = FakeEmbeddingProvider("voyage", "voyage-4", compatibility_key="voyage:series-4")
        db = _build_vectordb(tmpdir, provider)
        db._write_index_metadata()
        db._ensure_embedding_compatibility(require_metadata=False)


def test_vectordb_rejects_mismatched_index_metadata():
    with tempfile.TemporaryDirectory() as tmpdir:
        writer = _build_vectordb(
            tmpdir,
            FakeEmbeddingProvider("voyage", "voyage-4", compatibility_key="voyage:series-4"),
        )
        writer._write_index_metadata()

        reader = _build_vectordb(
            tmpdir,
            FakeEmbeddingProvider("voyage", "voyage-context-3", compatibility_key="voyage:context-3"),
        )
        try:
            reader._ensure_embedding_compatibility(require_metadata=False)
            assert False, "Expected RetrievalProviderError for incompatible embedding metadata"
        except RetrievalProviderError as exc:
            assert "incompatible" in str(exc).lower()
