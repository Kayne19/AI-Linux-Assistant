import math
import re
from collections import Counter, defaultdict

from orchestration.routing_registry import get_aliases_for_label
from retrieval.formatter import format_context_blocks, merge_context_chunks
from utils.debug_utils import debug_print


class RetrievalSearchPipeline:
    def __init__(
        self,
        store,
        metadata_store,
        embedding_provider,
        reranker_provider,
        event_listener=None,
        initial_fetch=40,
        final_top_k=20,
        neighbor_pages=2,
        max_expanded=40,
        source_profile_sample=5000,
    ):
        self.store = store
        self.metadata_store = metadata_store
        self.embedding_provider = embedding_provider
        self.reranker_provider = reranker_provider
        self.event_listener = event_listener
        self.initial_fetch = initial_fetch
        self.final_top_k = final_top_k
        self.neighbor_pages = neighbor_pages
        self.max_expanded = max_expanded
        self.source_profile_sample = source_profile_sample
        self._source_profiles_ready = False
        self._source_idf = {}
        self._source_top_tokens = {}

    def _emit_event(self, event_type, payload):
        if self.event_listener is not None:
            self.event_listener(event_type, payload)

    def _tokenize(self, text):
        words = re.split(r"[^a-zA-Z0-9]+", text.lower())
        return [word for word in words if len(word) >= 3]

    def _matches_allowed_titles(self, source, allowed_titles):
        if not allowed_titles:
            return True
        source = (source or "").lower()
        for label in allowed_titles:
            for alias in get_aliases_for_label(label):
                alias = (alias or "").strip().lower()
                if alias and alias in source:
                    return True
        return False

    def _build_source_profiles(self):
        rows = self.store.sample_rows(self.source_profile_sample)
        by_source = defaultdict(list)
        for _, row in rows.iterrows():
            src = row.get("source") or "Unknown"
            text = row.get("search_text") or ""
            if text:
                by_source[src].append(text)

        source_tokens = {}
        doc_freq = Counter()
        for src, texts in by_source.items():
            counts = Counter()
            unique_tokens = set()
            for text in texts:
                tokens = self._tokenize(text)
                counts.update(tokens)
                unique_tokens.update(tokens)
            source_tokens[src] = counts
            for token in unique_tokens:
                doc_freq[token] += 1

        total_sources = max(1, len(source_tokens))
        self._source_idf = {
            token: math.log((total_sources + 1) / (df + 1)) + 1.0
            for token, df in doc_freq.items()
        }

        self._source_top_tokens = {}
        for src, counts in source_tokens.items():
            scored = []
            for token, tf in counts.items():
                idf = self._source_idf.get(token, 0.0)
                scored.append((tf * idf, token))
            scored.sort(reverse=True)
            self._source_top_tokens[src] = {token for _, token in scored[:100]}

        self._source_profiles_ready = True

    def _source_boost(self, query):
        if not self._source_profiles_ready:
            return {}
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return {}

        boosts = {}
        for src, top_tokens in self._source_top_tokens.items():
            score = 0.0
            for token in query_tokens:
                if token in top_tokens:
                    score += self._source_idf.get(token, 0.0)
            boosts[src] = score / (len(query_tokens) + 1.0)
        return boosts

    def retrieve_context(self, query, sources):
        try:
            self.store.open_table()
        except Exception:
            return "Error: Database not initialized."

        debug_print(f"\n🔍 Searching manual for: '{query}'...")
        self._emit_event(
            "retrieval_search_started",
            {"query": query, "sources": list(sources or [])},
        )
        self.metadata_store.ensure_embedding_compatibility(self.embedding_provider, require_metadata=False)
        query_vec = self.embedding_provider.embed_query(query)
        candidates = self.store.search_hybrid(query_vec, query, self.initial_fetch)
        self._emit_event(
            "retrieval_candidates_found",
            {"count": len(candidates), "initial_fetch": self.initial_fetch},
        )

        if sources:
            candidates = [
                doc for doc in candidates
                if self._matches_allowed_titles(doc.get("source", ""), sources)
            ]
            self._emit_event(
                "retrieval_sources_filtered",
                {"count": len(candidates), "sources": list(sources or [])},
            )

        if not candidates:
            self._emit_event("retrieval_no_results", {"query": query})
            return ""

        debug_print(f"   - Reranking {len(candidates)} chunks...")
        self._emit_event("retrieval_reranking", {"count": len(candidates)})
        rerank_documents = [doc["search_text"] for doc in candidates]
        scores = self.reranker_provider.rerank(query, rerank_documents)
        for index, doc in enumerate(candidates):
            doc["rerank_score"] = scores[index]
        ranked_results = sorted(candidates, key=lambda item: item["rerank_score"], reverse=True)

        if not self._source_profiles_ready:
            self._build_source_profiles()
        boosts = self._source_boost(query)
        self._emit_event("retrieval_source_boosting", {"sources": len(boosts)})
        for doc in ranked_results:
            doc["rerank_score"] += 0.2 * boosts.get(doc.get("source", "Unknown"), 0.0)
        ranked_results = sorted(ranked_results, key=lambda item: item["rerank_score"], reverse=True)

        final_results = ranked_results[: self.final_top_k]

        if self.neighbor_pages > 0:
            self._emit_event(
                "retrieval_expanding",
                {"neighbor_pages": self.neighbor_pages, "max_expanded": self.max_expanded},
            )
            by_source_page = {}
            for doc in candidates:
                src = doc.get("source", "Unknown")
                try:
                    page = int(doc.get("page"))
                except Exception:
                    continue
                by_source_page.setdefault(src, {}).setdefault(page, []).append(doc)

            expanded = []
            seen = set()

            def _doc_key(doc):
                return doc.get("id") or (doc.get("source"), doc.get("page"), doc.get("text", "")[:64])

            for doc in final_results:
                key = _doc_key(doc)
                if key not in seen:
                    expanded.append(doc)
                    seen.add(key)

                try:
                    page = int(doc.get("page"))
                except Exception:
                    page = None
                if page is None:
                    continue

                src = doc.get("source", "Unknown")
                for candidate_page in range(page - self.neighbor_pages, page + self.neighbor_pages + 1):
                    for neighbor_doc in by_source_page.get(src, {}).get(candidate_page, []):
                        neighbor_key = _doc_key(neighbor_doc)
                        if neighbor_key in seen:
                            continue
                        expanded.append(neighbor_doc)
                        seen.add(neighbor_key)
                        if len(expanded) >= self.max_expanded:
                            break
                    if len(expanded) >= self.max_expanded:
                        break
                if len(expanded) >= self.max_expanded:
                    break

            if expanded:
                expanded_documents = [doc.get("search_text", "") for doc in expanded]
                expanded_scores = self.reranker_provider.rerank(query, expanded_documents)
                for index, doc in enumerate(expanded):
                    doc["rerank_score"] = expanded_scores[index]
                expanded = sorted(expanded, key=lambda item: item["rerank_score"], reverse=True)
                expanded = expanded[: self.max_expanded]
            final_results = expanded

        merged_results = merge_context_chunks(final_results)
        context_text, selected_sources = format_context_blocks(merged_results)
        self._emit_event(
            "retrieval_complete",
            {"merged_blocks": len(merged_results), "selected_sources": selected_sources},
        )
        debug_print(
            f"   (Selected top {len(merged_results)} merged blocks from: {', '.join(selected_sources)}...)"
        )
        return context_text
