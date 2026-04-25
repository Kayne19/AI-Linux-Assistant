import math
import re
from collections import Counter, defaultdict

from orchestration.routing_registry import get_aliases_for_label
from retrieval.config import load_retrieval_config
from retrieval.formatter import (
    build_block_key,
    build_bundle_key,
    build_page_window_key,
    build_row_key,
    coerce_page_number,
    format_context_blocks,
    merge_context_chunks,
    serialize_context_blocks,
)
from retrieval.scope import build_hint, select_candidate_docs, should_widen, widen_hint
from utils.debug_utils import debug_print


def _build_region_key(source, page_start=None, page_end=None, row_key=None):
    """Local helper — mirrors evidence_pool.build_region_key without the circular import."""
    if page_start is not None and page_end is not None:
        return f"region:{source}:{int(page_start)}-{int(page_end)}"
    if row_key is not None:
        return f"region:{source}:singleton:{row_key}"
    return f"region:{source}:singleton:unknown"


def _doc_source_key(doc):
    return doc.get("canonical_source_id") or doc.get("source") or "Unknown"


def _doc_display_source(doc):
    return doc.get("canonical_title") or doc.get("source") or _doc_source_key(doc)


def _doc_page_start(doc):
    return coerce_page_number(doc.get("page_start")) or coerce_page_number(doc.get("page"))


def _doc_page_end(doc):
    return coerce_page_number(doc.get("page_end")) or _doc_page_start(doc)


class RetrievalSearchPipeline:
    def __init__(
        self,
        store,
        documents_store,
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
        self.documents_store = documents_store
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
        self._documents_cache = None
        self._scope_config_cache = None

    def _emit_event(self, event_type, payload):
        if self.event_listener is not None:
            self.event_listener(event_type, payload)

    def _load_documents(self):
        if self._documents_cache is None:
            self._documents_cache = list(self.documents_store.load_documents())
        return self._documents_cache

    def _scope_config(self):
        if self._scope_config_cache is None:
            self._scope_config_cache = load_retrieval_config()
        return self._scope_config_cache

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

    def _goal_alignment_boost(self, requested_evidence_goal, doc):
        goal_tokens = set(self._tokenize(requested_evidence_goal or ""))
        if not goal_tokens:
            return 0.0
        doc_tokens = set(
            self._tokenize(
                f"{doc.get('search_text', '')} {doc.get('text', '')} {doc.get('source', '')}"
            )
        )
        overlap = len(goal_tokens & doc_tokens)
        if overlap <= 0:
            return 0.0
        return min(0.45, 0.15 * overlap)

    def _empty_result(self, context_text="", *, excluded_seen_count=0):
        return {
            "context_text": context_text,
            "selected_sources": [],
            "merged_blocks": [],
            "bundle_summaries": [],
            "retrieval_metadata": {
                "anchor_count": 0,
                "anchor_pages": [],
                "fetched_neighbor_pages": [],
                "delivered_bundle_count": 0,
                "delivered_bundle_keys": [],
                "delivered_block_keys": [],
                "delivered_page_window_keys": [],
                "delivered_page_windows": [],
                "excluded_seen_count": excluded_seen_count,
                "skipped_bundle_count": 0,
                "delivered_region_keys": [],
                "excluded_region_keys_seen": [],
                "net_new_region_count": 0,
            },
        }

    def _clone_doc(self, doc):
        clone = dict(doc or {})
        clone["row_key"] = build_row_key(clone)
        return clone

    def _rank_candidates(self, query, candidates, requested_evidence_goal=""):
        debug_print(f"   - Reranking {len(candidates)} chunks...")
        self._emit_event(
            "retrieval_reranking",
            {"count": len(candidates), "requested_evidence_goal": requested_evidence_goal or ""},
        )
        rerank_documents = [doc["search_text"] for doc in candidates]
        scores = self.reranker_provider.rerank(query, rerank_documents)
        ranked = []
        for index, doc in enumerate(candidates):
            ranked_doc = self._clone_doc(doc)
            ranked_doc["rerank_score"] = scores[index]
            ranked.append(ranked_doc)

        ranked_results = sorted(ranked, key=lambda item: item["rerank_score"], reverse=True)

        if not self._source_profiles_ready:
            self._build_source_profiles()
        boosts = self._source_boost(query)
        self._emit_event(
            "retrieval_source_boosting",
            {
                "sources": len(boosts),
                "requested_evidence_goal": requested_evidence_goal or "",
            },
        )
        for doc in ranked_results:
            doc["rerank_score"] += 0.2 * boosts.get(doc.get("source", "Unknown"), 0.0)
            doc["rerank_score"] += self._goal_alignment_boost(requested_evidence_goal, doc)
        return sorted(ranked_results, key=lambda item: item["rerank_score"], reverse=True)

    def _excluded_page_windows_by_source(self, excluded_page_windows):
        by_source = defaultdict(list)
        for window in excluded_page_windows or []:
            if not isinstance(window, dict):
                continue
            source = window.get("source")
            page_start = coerce_page_number(window.get("page_start"))
            page_end = coerce_page_number(window.get("page_end"))
            if not source or page_start is None or page_end is None:
                continue
            by_source[source].append((page_start, page_end))
        return by_source

    def _doc_is_excluded(self, doc, excluded_page_windows_by_source, excluded_block_keys):
        source_key = _doc_source_key(doc)
        display_source = _doc_display_source(doc)
        page_start = _doc_page_start(doc)
        page_end = _doc_page_end(doc)
        if page_start is not None and page_end is not None:
            candidate_windows = (
                excluded_page_windows_by_source.get(source_key, [])
                + excluded_page_windows_by_source.get(display_source, [])
            )
            for excluded_start, excluded_end in candidate_windows:
                if page_start <= excluded_end and page_end >= excluded_start:
                    return True
            return False
        singleton_block_key = build_block_key(source_key, row_keys=[build_row_key(doc)])
        return singleton_block_key in excluded_block_keys

    def _dedupe_docs(self, docs):
        deduped = []
        seen = set()
        for doc in docs:
            row_key = build_row_key(doc)
            if row_key in seen:
                continue
            doc["row_key"] = row_key
            deduped.append(doc)
            seen.add(row_key)
        return deduped

    def _build_anchor_bundle(self, anchor_doc, bundle_rank):
        source_key = _doc_source_key(anchor_doc)
        display_source = _doc_display_source(anchor_doc)
        anchor_row_key = build_row_key(anchor_doc)
        anchor_page_start = _doc_page_start(anchor_doc)
        anchor_page_end = _doc_page_end(anchor_doc)
        anchor_page = anchor_page_start
        anchor_score = float(anchor_doc.get("rerank_score", 0.0))

        if anchor_page_start is None or anchor_page_end is None:
            bundle_key = build_bundle_key(source_key, anchor_row_key)
            anchor_bundle_docs = [anchor_doc]
            return {
                "bundle_key": bundle_key,
                "bundle_rank": bundle_rank,
                "source_key": source_key,
                "display_source": display_source,
                "anchor_row_key": anchor_row_key,
                "anchor_page": None,
                "anchor_score": anchor_score,
                "requested_page_window_key": None,
                "requested_page_start": None,
                "requested_page_end": None,
                "fetched_neighbor_pages": [],
                "docs": anchor_bundle_docs,
                "is_page_less": True,
            }

        page_start = max(1, anchor_page - self.neighbor_pages)
        page_end = max(anchor_page_end, anchor_page_end + self.neighbor_pages)
        fetch_limit = max(1000, self.max_expanded * 20)
        if anchor_doc.get("canonical_source_id") and hasattr(self.store, "fetch_canonical_page_window"):
            fetched_raw = self.store.fetch_canonical_page_window(
                anchor_doc.get("canonical_source_id"),
                page_start,
                page_end,
                limit=fetch_limit,
            )
        else:
            fetched_raw = self.store.fetch_source_page_window(
                display_source,
                page_start,
                page_end,
                limit=fetch_limit,
            )
        fetched_docs = [self._clone_doc(doc) for doc in fetched_raw]
        if not any(build_row_key(doc) == anchor_row_key for doc in fetched_docs):
            fetched_docs.append(anchor_doc)

        bundle_key = build_bundle_key(source_key, anchor_row_key, page_start, page_end)
        fetched_neighbor_pages = sorted(
            {
                page
                for doc in fetched_docs
                for page in range(_doc_page_start(doc) or 0, (_doc_page_end(doc) or 0) + 1)
                if page >= 1 and not (anchor_page_start <= page <= anchor_page_end)
            }
        )
        return {
            "bundle_key": bundle_key,
            "bundle_rank": bundle_rank,
            "source_key": source_key,
            "display_source": display_source,
            "anchor_row_key": anchor_row_key,
            "anchor_page": anchor_page,
            "anchor_score": anchor_score,
            "requested_page_window_key": build_page_window_key(source_key, page_start, page_end),
            "requested_page_start": page_start,
            "requested_page_end": page_end,
            "fetched_neighbor_pages": fetched_neighbor_pages,
            "docs": self._dedupe_docs(fetched_docs),
            "is_page_less": False,
        }

    def retrieve_context_result(
        self,
        query,
        sources,
        excluded_page_windows=None,
        excluded_block_keys=None,
        covered_region_keys=None,
        requested_evidence_goal=None,
        router_hint=None,
        explicit_doc_ids=None,
    ):
        excluded_block_keys = set(excluded_block_keys or [])
        excluded_page_windows = list(excluded_page_windows or [])
        covered_region_keys_input = list(covered_region_keys or [])
        try:
            self.store.open_table()
        except Exception:
            return self._empty_result("Error: Database not initialized.")

        debug_print(f"\n🔍 Searching manual for: '{query}'...")
        self._emit_event(
            "retrieval_search_started",
            {
                "query": query,
                "sources": list(sources or []),
                "excluded_page_window_count": len(excluded_page_windows),
                "excluded_block_count": len(excluded_block_keys),
                "requested_evidence_goal": requested_evidence_goal or "",
            },
        )
        self.metadata_store.ensure_embedding_compatibility(self.embedding_provider, require_metadata=False)
        query_vec = self.embedding_provider.embed_query(query)
        documents = self._load_documents()
        hint = build_hint(
            query=query,
            router_hint=router_hint,
            explicit_doc_ids=tuple(explicit_doc_ids or ()),
        )
        scope_config = self._scope_config()
        chosen_candidates = select_candidate_docs(hint, documents)
        widenings_taken = 0

        while (
            not hint.explicit_doc_ids
            and
            should_widen(
                chosen_candidates,
                min_hit_count=scope_config.scope_min_hit_count,
                min_top_score=scope_config.scope_min_top_score,
            )
            and widenings_taken < scope_config.scope_max_widenings
        ):
            widenings_taken += 1
            hint = widen_hint(hint, step=widenings_taken)
            chosen_candidates = select_candidate_docs(hint, documents)

        candidate_ids = [candidate.canonical_source_id for candidate in chosen_candidates]
        self._emit_event(
            "retrieval_scope_selected",
            {
                "candidate_doc_ids": candidate_ids,
                "candidate_count": len(candidate_ids),
                "winning_filter": {
                    "os_family": hint.os_family,
                    "source_family": hint.source_family,
                    "package_managers": list(hint.package_managers),
                    "init_systems": list(hint.init_systems),
                    "major_subsystems": list(hint.major_subsystems),
                    "explicit_doc_ids": list(hint.explicit_doc_ids),
                },
                "tier_rankings": [
                    {
                        "canonical_source_id": candidate.canonical_source_id,
                        "canonical_title": candidate.canonical_title,
                        "score": round(candidate.score, 3),
                        "matched_fields": candidate.matched_fields,
                    }
                    for candidate in chosen_candidates[:10]
                ],
                "widenings_taken": widenings_taken,
                "documents_total": len(documents),
                "router_hint_present": router_hint is not None,
            },
        )
        candidates = self.store.search_hybrid_scoped(query_vec, query, self.initial_fetch, candidate_ids)
        self._emit_event(
            "retrieval_candidates_found",
            {
                "count": len(candidates),
                "initial_fetch": self.initial_fetch,
                "scoped_candidate_doc_count": len(candidate_ids),
            },
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
            return self._empty_result()

        ranked_results = self._rank_candidates(query, candidates, requested_evidence_goal=requested_evidence_goal or "")
        anchors = ranked_results[: self.final_top_k]
        anchor_pages = [
            page
            for page in (_doc_page_start(doc) for doc in anchors)
            if page is not None
        ]
        self._emit_event(
            "retrieval_expanding",
            {
                "neighbor_pages": self.neighbor_pages,
                "max_expanded": self.max_expanded,
                "anchor_count": len(anchors),
                "anchor_pages": anchor_pages,
                "requested_evidence_goal": requested_evidence_goal or "",
            },
        )

        excluded_windows_by_source = self._excluded_page_windows_by_source(excluded_page_windows)
        selected_docs = []
        selected_doc_keys = set()
        bundle_summaries = []
        fetched_neighbor_pages_by_source = defaultdict(set)
        excluded_seen_count = 0
        skipped_bundle_count = 0
        excluded_region_keys_seen = []
        _excluded_region_key_set = set()

        for bundle_rank, anchor in enumerate(anchors):
            anchor_doc = self._clone_doc(anchor)
            bundle = self._build_anchor_bundle(anchor_doc, bundle_rank)
            source = bundle["source_key"]
            for page in bundle["fetched_neighbor_pages"]:
                fetched_neighbor_pages_by_source[source].add(page)

            bundle_docs = []
            for raw_doc in bundle["docs"]:
                row_key = build_row_key(raw_doc)
                if self._doc_is_excluded(raw_doc, excluded_windows_by_source, excluded_block_keys):
                    excluded_seen_count += 1
                    # Record the region key so the pool can classify this as overlap/reused
                    page_start = _doc_page_start(raw_doc)
                    page_end = _doc_page_end(raw_doc)
                    if page_start is not None and page_end is not None:
                        rk = _build_region_key(source, page_start, page_end)
                    else:
                        rk = _build_region_key(source, row_key=row_key)
                    if rk not in _excluded_region_key_set:
                        _excluded_region_key_set.add(rk)
                        excluded_region_keys_seen.append(rk)
                    continue
                if row_key in selected_doc_keys:
                    continue
                bundle_doc = dict(raw_doc)
                bundle_doc["row_key"] = row_key
                bundle_doc["bundle_key"] = bundle["bundle_key"]
                bundle_doc["bundle_rank"] = bundle["bundle_rank"]
                bundle_doc["source_key"] = source
                bundle_doc["anchor_row_key"] = bundle["anchor_row_key"]
                bundle_doc["anchor_page"] = bundle["anchor_page"]
                bundle_doc["requested_page_window_key"] = bundle["requested_page_window_key"]
                bundle_doc["rerank_score"] = bundle["anchor_score"]
                bundle_docs.append(bundle_doc)

            if not bundle_docs:
                skipped_bundle_count += 1
                continue

            if selected_docs and len(selected_docs) + len(bundle_docs) > self.max_expanded:
                skipped_bundle_count += 1
                break

            delivered_pages = sorted(
                {
                    page
                    for doc in bundle_docs
                    for page in range(_doc_page_start(doc) or 0, (_doc_page_end(doc) or 0) + 1)
                    if page >= 1
                }
            )
            delivered_page_window_key = build_page_window_key(
                source,
                delivered_pages[0] if delivered_pages else None,
                delivered_pages[-1] if delivered_pages else None,
            )
            bundle_summaries.append(
                {
                    "bundle_key": bundle["bundle_key"],
                    "source": source,
                    "anchor_row_key": bundle["anchor_row_key"],
                    "anchor_page": bundle["anchor_page"],
                    "requested_page_window_key": bundle["requested_page_window_key"],
                    "requested_page_start": bundle["requested_page_start"],
                    "requested_page_end": bundle["requested_page_end"],
                    "delivered_page_window_key": delivered_page_window_key,
                    "delivered_pages": delivered_pages,
                    "row_keys": [doc["row_key"] for doc in bundle_docs],
                    "page_less": bundle["is_page_less"],
                }
            )
            for doc in bundle_docs:
                selected_docs.append(doc)
                selected_doc_keys.add(doc["row_key"])

        merged_results = merge_context_chunks(selected_docs)
        merged_blocks = serialize_context_blocks(merged_results)
        context_text, selected_sources = format_context_blocks(merged_results)

        delivered_page_windows = []
        delivered_page_window_keys = []
        delivered_block_keys = []
        for block in merged_blocks:
            delivered_block_keys.append(block.get("block_key"))
            page_window_key = block.get("page_window_key")
            if page_window_key and block.get("pages"):
                delivered_page_window_keys.append(page_window_key)
                delivered_page_windows.append(
                    {
                        "key": page_window_key,
                        "source": block.get("source"),
                        "page_start": block["pages"][0],
                        "page_end": block["pages"][-1],
                    }
                )

        # Derive region keys from delivered page windows + singleton block keys
        delivered_region_keys = []
        for window in delivered_page_windows:
            delivered_region_keys.append(
                _build_region_key(window["source"], window["page_start"], window["page_end"])
            )
        for block_key in delivered_block_keys:
            if block_key and ":singleton:" in block_key:
                parts = block_key.split(":", 3)
                if len(parts) == 4:
                    delivered_region_keys.append(_build_region_key(parts[1], row_key=parts[3]))

        retrieval_metadata = {
            "anchor_count": len(anchors),
            "anchor_pages": anchor_pages,
            "fetched_neighbor_pages": [
                {"source": src, "pages": sorted(pages)}
                for src, pages in sorted(fetched_neighbor_pages_by_source.items())
            ],
            "delivered_bundle_count": len(bundle_summaries),
            "delivered_bundle_keys": [bundle["bundle_key"] for bundle in bundle_summaries],
            "delivered_block_keys": [key for key in delivered_block_keys if key],
            "delivered_page_window_keys": delivered_page_window_keys,
            "delivered_page_windows": delivered_page_windows,
            "excluded_seen_count": excluded_seen_count,
            "skipped_bundle_count": skipped_bundle_count,
            # V2 region-key fields for the evidence pool
            "delivered_region_keys": delivered_region_keys,
            "excluded_region_keys_seen": excluded_region_keys_seen,
            "net_new_region_count": len(delivered_region_keys),
            "covered_region_keys_input": covered_region_keys_input,
            "requested_evidence_goal": requested_evidence_goal or "",
        }

        self._emit_event(
            "retrieval_complete",
            {
                "merged_blocks": len(merged_blocks),
                "selected_sources": selected_sources,
                "anchor_count": retrieval_metadata["anchor_count"],
                "anchor_pages": retrieval_metadata["anchor_pages"],
                "fetched_neighbor_pages": retrieval_metadata["fetched_neighbor_pages"],
                "delivered_bundle_count": retrieval_metadata["delivered_bundle_count"],
                "excluded_seen_count": retrieval_metadata["excluded_seen_count"],
                "skipped_bundle_count": retrieval_metadata["skipped_bundle_count"],
                "requested_evidence_goal": requested_evidence_goal or "",
            },
        )
        debug_print(
            f"   (Selected {len(bundle_summaries)} bundles / {len(merged_blocks)} merged blocks from: "
            f"{', '.join(selected_sources)}...)"
        )
        return {
            "context_text": context_text,
            "selected_sources": selected_sources,
            "merged_blocks": merged_blocks,
            "bundle_summaries": bundle_summaries,
            "retrieval_metadata": retrieval_metadata,
        }

    def retrieve_context(self, query, sources, **kwargs):
        return self.retrieve_context_result(query, sources, **kwargs)["context_text"]
