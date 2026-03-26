import json
import os
import re
import math
from collections import Counter, defaultdict
import lancedb
from sentence_transformers import SentenceTransformer, CrossEncoder # <--- NEW IMPORT
from debug_utils import debug_print
from routing_registry import get_aliases_for_label, get_skip_rag_labels

class VectorDB:
    # ------------------------------------------------------------------
    # Pipeline overview:
    # extracted_clean_final.json -> LanceDB table -> retrieve_context()
    # ------------------------------------------------------------------
    def __init__(self):
        # Ingest source (post-enrichment) and LanceDB destination.
        # These are the key pivots if you split config/paths into a separate module.
        self.JSON_PATH = "extracted_clean_final.json"
        self.DB_PATH = "lancedb_data"
        self.TABLE_NAME = "debian_manual"
        
        # --- MODELS ---
        # Embedder = retrieval; Reranker = precision filtering.
        self.EMBED_MODEL_NAME = "all-MiniLM-L6-v2" # Fast, for initial retrieval
        self.RERANKER_MODEL_NAME = "BAAI/bge-reranker-v2-m3" # Smart, for filtering
        
        # --- SETTINGS ---
        # Retrieval knobs (candidate pool, final cut) and context expansion.
        self.INITIAL_FETCH = 40  # Fetch this many candidates (Wide Net)
        self.FINAL_TOP_K = 20    # Keep only this many highly relevant chunks
        self.NEIGHBOR_PAGES = 2  # Expand context with adjacent pages
        self.MAX_EXPANDED = 40   # Cap expanded results
        self.SOURCE_PROFILE_SAMPLE = 5000  # Limit rows for source profiling
        self._source_profiles_ready = False
        self._source_idf = {}
        self._source_top_tokens = {}
        # Runtime device controls. The eval runner overrides these to CPU so the
        # retrieval stack does not consume CUDA memory during prompt/model evals.
        self.EMBED_DEVICE = os.getenv("VECTORDB_EMBED_DEVICE", "").strip() or None
        self.RERANK_DEVICE = os.getenv("VECTORDB_RERANK_DEVICE", "cuda").strip() or "cuda"
        
        debug_print("🤖 Loading Models...")
        # 1. Load Embedder (Vectors)
        embedder_kwargs = {}
        if self.EMBED_DEVICE is not None:
            embedder_kwargs["device"] = self.EMBED_DEVICE
        self.embedder = SentenceTransformer(self.EMBED_MODEL_NAME, **embedder_kwargs)
        
        # 2. Load Reranker (The Judge)
        self.reranker = CrossEncoder(self.RERANKER_MODEL_NAME, device=self.RERANK_DEVICE)

    def _get_table(self):
        # Single place to open the LanceDB table (useful when refactoring storage).
        db = lancedb.connect(self.DB_PATH)
        return db.open_table(self.TABLE_NAME)

    def _tokenize(self, text):
        words = re.split(r"[^a-zA-Z0-9]+", text.lower())
        return [w for w in words if len(w) >= 3]

    # Check whether a document's source filename matches any allowed title tokens.
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

    def _build_source_profiles(self, tbl):
        # Build lightweight source profiles for soft boosting (keeps retrieval general).
        rows = tbl.to_pandas().head(self.SOURCE_PROFILE_SAMPLE)
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
            for t in texts:
                toks = self._tokenize(t)
                counts.update(toks)
                unique_tokens.update(toks)
            source_tokens[src] = counts
            for tok in unique_tokens:
                doc_freq[tok] += 1

        total_sources = max(1, len(source_tokens))
        self._source_idf = {
            tok: math.log((total_sources + 1) / (df + 1)) + 1.0
            for tok, df in doc_freq.items()
        }

        self._source_top_tokens = {}
        for src, counts in source_tokens.items():
            scored = []
            for tok, tf in counts.items():
                idf = self._source_idf.get(tok, 0.0)
                scored.append((tf * idf, tok))
            scored.sort(reverse=True)
            self._source_top_tokens[src] = {tok for _, tok in scored[:100]}

        self._source_profiles_ready = True

    def _source_boost(self, query):
        if not self._source_profiles_ready:
            return {}
        q_tokens = self._tokenize(query)
        if not q_tokens:
            return {}
        boosts = {}
        for src, top_tokens in self._source_top_tokens.items():
            score = 0.0
            for tok in q_tokens:
                if tok in top_tokens:
                    score += self._source_idf.get(tok, 0.0)
            boosts[src] = score / (len(q_tokens) + 1.0)
        return boosts

    def ingest_data(self):
        # Ingests the enriched JSON into LanceDB.
        # Split this if you want a dedicated "loader" module later.
        if not os.path.exists(self.JSON_PATH):
            print(f"Error: {self.JSON_PATH} not found.")
            return

        db = lancedb.connect(self.DB_PATH)
        
        # Check if DB is already populated (optional guard).
        # if self.TABLE_NAME in db.table_names():
        #     tbl = db.open_table(self.TABLE_NAME)
        #     if len(tbl) > 0:
        #         print(f"Table '{self.TABLE_NAME}' already has {len(tbl)} items. Skipping ingest.")
        #         return

        print(f"INGESTING: {self.JSON_PATH}")
        
        with open(self.JSON_PATH, 'r') as f:
            raw_data = json.load(f)
            
        print(f"   - Processing {len(raw_data)} elements...")
        data_to_insert = []
        texts_to_embed = []
        
        for idx, el in enumerate(raw_data):
            # SEARCH against the Enriched AI Context (embedding_text).
            search_text = el.get("metadata", {}).get("embedding_text", el.get("text", ""))
            display_text = el.get("text", "").strip()
            
            texts_to_embed.append(search_text)
            
            meta = el.get("metadata", {})
            # Minimal row schema for LanceDB; add fields here if you split metadata later.
            data_to_insert.append({
                "id": f"vec_{idx}",
                "text": display_text,       
                "search_text": search_text, 
                "page": meta.get("page_number", 0),
                "source": meta.get("filename", "Unknown"),
                "type": el.get("type", "Text")
            })

        # Vectorize and store.
        print("   - Generating vectors...")
        vectors = self.embedder.encode(texts_to_embed, show_progress_bar=True)
        
        for i, item in enumerate(data_to_insert):
            item["vector"] = vectors[i]

        # Append-or-create table logic lives here.
        print("   - Writing to LanceDB...")
        if self.TABLE_NAME in db.table_names():
            print(f"📦 Table exists. Appending {len(data_to_insert)} rows...")
            tbl = db.open_table(self.TABLE_NAME)
            tbl.add(data_to_insert)
        else:
            print(f"🆕 Table doesn't exist. Creating {self.TABLE_NAME}...")
            tbl = db.create_table(self.TABLE_NAME, data=data_to_insert)
        
        # Hybrid search depends on the FTS index.
        print("   - Building FTS (Keyword) Index...")
        tbl.create_fts_index("search_text", replace=True)
        print(f"SAVED {len(data_to_insert)} VECTORS TO DISK.")

    def retrieve_context(self, query, sources):
        # End-to-end retrieval pipeline: hybrid search -> rerank -> boost -> expand.
        try:
            tbl = self._get_table()
        except:
            return "Error: Database not initialized."

        skip_rag_labels = get_skip_rag_labels()
        if sources and any(label in skip_rag_labels for label in sources):
            return ""

        debug_print(f"\n🔍 Searching manual for: '{query}'...")
        
        # 1. Embed the User Query
        query_vec = self.embedder.encode(query)
        
        # 2. Perform Hybrid Search (The "Wide Net")
        # We fetch 50 candidates to make sure we don't miss the Proxmox/Nesting stuff
        candidates = tbl.search(query_type="hybrid") \
            .vector(query_vec) \
            .text(query) \
            .limit(self.INITIAL_FETCH) \
            .to_list()

        if sources:
            candidates = [
                doc for doc in candidates
                if self._matches_allowed_titles(doc.get("source", ""), sources)
            ]

        if not candidates:
            return ""

        # 3. RERANKING (The "Smart Filter")
        debug_print(f"   - Reranking {len(candidates)} chunks...")
        
        # Prepare pairs: [ [Query, Doc1], [Query, Doc2] ... ]
        # We use 'search_text' because it contains the Enriched AI Context
        pairs = [[query, doc['search_text']] for doc in candidates]
        
        # Score them (-10 to +10)
        scores = self.reranker.predict(pairs)
        
        # Attach scores and sort
        for i, doc in enumerate(candidates):
            doc['rerank_score'] = scores[i]
            
        # Sort descending (Highest score = Best match)
        ranked_results = sorted(candidates, key=lambda x: x['rerank_score'], reverse=True)

        # 4. Soft-boost by source relevance (no hard filters)
        if not self._source_profiles_ready:
            self._build_source_profiles(tbl)
        boosts = self._source_boost(query)
        for doc in ranked_results:
            doc['rerank_score'] += 0.2 * boosts.get(doc.get('source', 'Unknown'), 0.0)
        ranked_results = sorted(ranked_results, key=lambda x: x['rerank_score'], reverse=True)

        # 5. Filter: Keep only the Top K
        final_results = ranked_results[:self.FINAL_TOP_K]

        # 6. Expand with nearby pages from the same source (chunk neighborhood expansion).
        if self.NEIGHBOR_PAGES > 0:
            by_source_page = {}
            for doc in candidates:
                src = doc.get("source", "Unknown")
                page = doc.get("page", None)
                try:
                    page = int(page)
                except Exception:
                    continue
                by_source_page.setdefault(src, {}).setdefault(page, []).append(doc)

            expanded = []
            seen = set()

            def _doc_key(d):
                return d.get("id") or (d.get("source"), d.get("page"), d.get("text", "")[:64])

            for doc in final_results:
                key = _doc_key(doc)
                if key not in seen:
                    expanded.append(doc)
                    seen.add(key)
                src = doc.get("source", "Unknown")
                try:
                    page = int(doc.get("page"))
                except Exception:
                    page = None
                if page is None:
                    continue
                for p in range(page - self.NEIGHBOR_PAGES, page + self.NEIGHBOR_PAGES + 1):
                    for ndoc in by_source_page.get(src, {}).get(p, []):
                        nkey = _doc_key(ndoc)
                        if nkey in seen:
                            continue
                        expanded.append(ndoc)
                        seen.add(nkey)
                        if len(expanded) >= self.MAX_EXPANDED:
                            break
                    if len(expanded) >= self.MAX_EXPANDED:
                        break
                if len(expanded) >= self.MAX_EXPANDED:
                    break

            # Rerank expanded neighbors to keep adjacency context but drop weak pages.
            if expanded:
                exp_pairs = [[query, d.get('search_text', '')] for d in expanded]
                exp_scores = self.reranker.predict(exp_pairs)
                for i, d in enumerate(expanded):
                    d['rerank_score'] = exp_scores[i]
                expanded = sorted(expanded, key=lambda x: x['rerank_score'], reverse=True)
                expanded = expanded[:self.MAX_EXPANDED]
            final_results = expanded
        
        # 7. Format Output for LLM (keep source labels intact for citations).
        context_text = ""
        sources = []
        
        for doc in final_results:
            page = doc.get('page', '?')
            source_file = doc.get('source', 'Unknown')
            text = doc.get('text', '') 
            
            # Formatting clearly for the LLM
            context_text += f"---\n[Source: {source_file} (Page {page}) | Score: {doc['rerank_score']:.2f}]\n{text}\n"
            sources.append(f"{source_file}:{page}")
            
        debug_print(f"   (Selected top {len(final_results)} chunks from: {', '.join(sources)}...)")
        return context_text
