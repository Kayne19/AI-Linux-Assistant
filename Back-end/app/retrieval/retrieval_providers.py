import os


class RetrievalProviderError(RuntimeError):
    pass


class BaseEmbeddingProvider:
    provider_name = "base"

    def __init__(self, model_name):
        self.model_name = model_name

    def embed_documents(self, texts, show_progress_bar=False):
        raise NotImplementedError

    def embed_query(self, text):
        raise NotImplementedError

    def get_index_metadata(self):
        return {
            "provider": self.provider_name,
            "model": self.model_name,
            "compatibility_key": f"{self.provider_name}:{self.model_name}",
        }

    def is_compatible_with_index(self, index_metadata):
        if not index_metadata:
            return False
        return (
            index_metadata.get("provider") == self.provider_name
            and index_metadata.get("model") == self.model_name
        )


class BaseRerankerProvider:
    provider_name = "base"

    def __init__(self, model_name):
        self.model_name = model_name

    def rerank(self, query, documents):
        raise NotImplementedError


class LocalSentenceTransformerEmbeddingProvider(BaseEmbeddingProvider):
    provider_name = "local"

    def __init__(self, model_name, device=None):
        super().__init__(model_name)
        from sentence_transformers import SentenceTransformer

        kwargs = {}
        if device is not None:
            kwargs["device"] = device
        self._model = SentenceTransformer(model_name, **kwargs)

    def embed_documents(self, texts, show_progress_bar=False):
        return self._model.encode(texts, show_progress_bar=show_progress_bar)

    def embed_query(self, text):
        return self._model.encode(text)


class LocalCrossEncoderRerankerProvider(BaseRerankerProvider):
    provider_name = "local"

    def __init__(self, model_name, device="cuda"):
        super().__init__(model_name)
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name, device=device)

    def rerank(self, query, documents):
        if not documents:
            return []
        pairs = [[query, document] for document in documents]
        return self._model.predict(pairs)


class VoyageEmbeddingProvider(BaseEmbeddingProvider):
    provider_name = "voyage"
    _MAX_BATCH_SIZE = 1000
    _SERIES_4_MODELS = {
        "voyage-4",
        "voyage-4-lite",
        "voyage-4-large",
        "voyage-4-nano",
    }

    def __init__(self, model_name, api_key=None, output_dimension=None):
        super().__init__(model_name)
        try:
            import voyageai
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RetrievalProviderError(
                "voyageai is not installed. Install it before using Voyage retrieval providers."
            ) from exc

        resolved_api_key = (api_key or os.getenv("VOYAGE_API_KEY", "")).strip()
        if not resolved_api_key:
            raise RetrievalProviderError("VOYAGE_API_KEY is required for Voyage embedding provider.")

        self.output_dimension = output_dimension
        self._client = voyageai.Client(api_key=resolved_api_key)

    def embed_documents(self, texts, show_progress_bar=False):
        if not texts:
            return []

        kwargs = {}
        if self.output_dimension:
            kwargs["output_dimension"] = self.output_dimension

        embeddings = []
        total = len(texts)
        for start in range(0, total, self._MAX_BATCH_SIZE):
            end = min(start + self._MAX_BATCH_SIZE, total)
            if show_progress_bar:
                print(
                    f"Voyage embedding batch {start + 1}-{end} of {total}",
                    flush=True,
                )
            response = self._client.embed(
                texts[start:end],
                model=self.model_name,
                input_type="document",
                **kwargs,
            )
            embeddings.extend(response.embeddings)

        return embeddings

    def embed_query(self, text):
        kwargs = {}
        if self.output_dimension:
            kwargs["output_dimension"] = self.output_dimension
        response = self._client.embed(
            [text],
            model=self.model_name,
            input_type="query",
            **kwargs,
        )
        return response.embeddings[0]

    def get_index_metadata(self):
        metadata = super().get_index_metadata()
        metadata["output_dimension"] = self.output_dimension
        if self.model_name in self._SERIES_4_MODELS:
            metadata["compatibility_key"] = "voyage:series-4"
        return metadata

    def is_compatible_with_index(self, index_metadata):
        if not index_metadata:
            return False

        current_metadata = self.get_index_metadata()
        if index_metadata.get("compatibility_key") == current_metadata.get("compatibility_key"):
            return index_metadata.get("output_dimension") == current_metadata.get("output_dimension")
        return super().is_compatible_with_index(index_metadata)


class VoyageRerankerProvider(BaseRerankerProvider):
    provider_name = "voyage"

    def __init__(self, model_name, api_key=None):
        super().__init__(model_name)
        try:
            import voyageai
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RetrievalProviderError(
                "voyageai is not installed. Install it before using Voyage retrieval providers."
            ) from exc

        resolved_api_key = (api_key or os.getenv("VOYAGE_API_KEY", "")).strip()
        if not resolved_api_key:
            raise RetrievalProviderError("VOYAGE_API_KEY is required for Voyage reranker provider.")

        self._client = voyageai.Client(api_key=resolved_api_key)

    def rerank(self, query, documents):
        if not documents:
            return []

        response = self._client.rerank(
            query=query,
            documents=documents,
            model=self.model_name,
            top_k=len(documents),
        )

        scores = [0.0] * len(documents)
        for item in getattr(response, "results", []):
            scores[item.index] = item.relevance_score
        return scores


def create_embedding_provider(provider_name, model_name, **kwargs):
    normalized = (provider_name or "local").strip().lower()
    if normalized == "local":
        return LocalSentenceTransformerEmbeddingProvider(model_name, device=kwargs.get("device"))
    if normalized == "voyage":
        return VoyageEmbeddingProvider(
            model_name,
            api_key=kwargs.get("api_key"),
            output_dimension=kwargs.get("output_dimension"),
        )
    raise RetrievalProviderError(f"Unsupported embedding provider: {provider_name}")


def create_reranker_provider(provider_name, model_name, **kwargs):
    normalized = (provider_name or "local").strip().lower()
    if normalized == "local":
        return LocalCrossEncoderRerankerProvider(model_name, device=kwargs.get("device", "cuda"))
    if normalized == "voyage":
        return VoyageRerankerProvider(model_name, api_key=kwargs.get("api_key"))
    raise RetrievalProviderError(f"Unsupported reranker provider: {provider_name}")
