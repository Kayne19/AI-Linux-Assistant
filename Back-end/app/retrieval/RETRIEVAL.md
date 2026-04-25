# Retrieval Subsystem

This document explains the vector-space side of the backend: what retrieval owns, what ingestion owns, and where each responsibility lives.

## Purpose

The retrieval subsystem is responsible for:

- building embeddings and vector rows during ingestion
- enforcing embedding/index compatibility
- searching the vector store at runtime
- reranking and formatting retrieved context for the responder

It is not responsible for:

- deciding whether retrieval should run on a turn
- storing chats, users, or projects
- owning ingestion orchestration

The router coordinates when and what retrieval runs; the router-owned `EvidencePool` tracks scopes, coverage, outcomes, usefulness, and gating. The retrieval pipeline executes that search and returns region-key metadata so the pool can update its coverage state.

## Ownership Split

### Ingestion owns indexing

The ingestion pipeline is end-to-end.

It extracts, cleans, enriches, finalizes, and then writes rows into LanceDB.

That indexing path lives in:

- [app/ingestion/indexer.py](app/ingestion/indexer.py)

### Runtime retrieval owns search

Normal chatbot runtime only reads and searches an already-built index.

That runtime retrieval path lives in:

- [app/retrieval/search_pipeline.py](app/retrieval/search_pipeline.py)

## Main Files

### `app/retrieval/config.py`

Owns retrieval configuration:

- LanceDB path
- table name
- metadata suffix
- embedder provider/model
- reranker provider/model
- retrieval knobs like fetch size, anchor count (`final_top_k`), neighbor expansion, and bundle caps

This is the single source of truth for retrieval runtime configuration.

Current runtime resolution rule:

- embedder/reranker provider-model settings still come from retrieval env/config
- retrieval numeric runtime knobs now resolve through effective app settings, so admin DB overrides can tune:
  - `initial_fetch`
  - `final_top_k` (public name retained; runtime meaning is max anchor count)
  - `neighbor_pages`
  - `max_expanded` (applied at bundle boundaries)
  - `source_profile_sample`
- if the DB schema is older than the running code, retrieval falls back to env/code defaults until migrations are applied

### `app/retrieval/factory.py`

Owns retrieval composition:

- embedding provider construction
- reranker provider construction
- LanceDB store construction
- index metadata store construction
- runtime search pipeline construction

Both runtime retrieval and ingestion indexing depend on the shared retrieval config/factory seam.

### `app/retrieval/retrieval_providers.py`

Owns provider adapters for:

- embeddings
- reranking

This is the provider/model swap seam.

Current examples include local and Voyage-backed implementations.

### `app/retrieval/store.py`

Owns LanceDB storage I/O:

- connect to LanceDB
- open table
- check whether the table exists
- add rows
- rebuild FTS index
- run hybrid search
- run scoped hybrid search and canonical page-window fetches
- sample rows for source profiling

It does not own ranking policy.

### `app/retrieval/index_metadata.py`

Owns index metadata and compatibility checks:

- read stored metadata
- write metadata
- verify that the current embedding provider/model is compatible with the existing index
- preserve legacy compatibility rules

This is what prevents silent vector mismatches.

### `app/retrieval/search_pipeline.py`

Owns runtime retrieval behavior:

- embed query
- hybrid candidate search
- optional source filtering
- reranking for anchor selection
- source-profile boosting
- direct same-document page-window fetches around selected anchors
- bundle/block dedupe before formatting
- merge retrieved chunks
- format prompt-ready context

It also emits retrieval events so the rest of the system can observe what retrieval did.

Current goal-hint rule:

- runtime retrieval may accept an optional `requested_evidence_goal`
- that hint is router-provided, internal, and backward-compatible
- it may influence rerank/anchor choice in a bounded way
- it does not move scope, usefulness, or gating policy into retrieval

### `app/retrieval/formatter.py`

Owns result shaping:

- merge adjacent/related chunks
- format source-labeled context blocks
- serialize merged context blocks into a stable debug shape for the canonical run-level `normalized_inputs` bundle

This keeps retrieval formatting separate from search and ranking logic.

### `app/retrieval/vectorDB.py`

This is now a thin runtime facade kept for compatibility.

It should remain runtime-only.

It should not regain indexing responsibilities.

Runtime config rule:

- the facade must build its store/metadata/search components from one coherent runtime config
- compatibility helpers must read the same configured path/table metadata that runtime retrieval uses
- compatibility overrides are runtime composition only, not ingestion/indexing behavior
- admin-tunable retrieval settings are runtime-only and must not mutate ingestion/index metadata policy

## Document Scope Pre-Narrowing

The corpus now carries a structured `documents` table populated by ingestion (`canonical_source_id`, `canonical_title`, `source_family`, `vendor_or_project`, `os_family`, `init_systems`, `package_managers`, `major_subsystems`, `trust_tier`, `freshness_status`, …). The intent is to narrow by document family before hybrid search so a Debian install guide does not contaminate an Arch wiki query and vice-versa.

The selector module lives in [app/retrieval/scope.py](app/retrieval/scope.py) and is fully unit-tested:

- `build_hint(query, router_hint)` merges router hints with query-entity extraction (e.g. `apt-get` → `apt`, `systemctl` → `systemd`, `docker` → `containers`). Router hints win on scalar fields; list fields union. Scalar strings on list-typed router fields are wrapped, not iterated as characters.
- `select_candidate_docs(hint, documents)` ranks candidates by tier-weighted field match plus trust and freshness. `explicit_doc_ids` short-circuits ranking when the router pins a specific document.
- `widen_hint(hint, step)` drops constraints in this order — `package_managers` → `init_systems` → `major_subsystems` → `os_family` → `source_family` — preserving `explicit_doc_ids`.
- `should_widen(candidates, …)` gates retry on `scope_min_hit_count` and `scope_min_top_score` (see `RetrievalConfig`).

The store-side hook is also in place: `LanceDBStore.search_hybrid_scoped(...)` adds a `canonical_source_id IN (...)` predicate with single-quote-escaped values, falling back to the unscoped hybrid search when the candidate list is empty.

### Scope Selection Runtime

Runtime retrieval loads the `documents` table once per `RetrievalSearchPipeline` instance and applies document scope before chunk-level hybrid search:

1. `build_hint(query, router_hint, explicit_doc_ids)` merges router hints, query-derived scope signals, and optional canonical document pins.
2. `select_candidate_docs(hint, documents)` ranks document candidates by matched fields plus trust and freshness.
3. If candidate quality is weak, the widening loop calls `widen_hint(hint, step)` and re-ranks until the configured limit is reached.
4. `search_hybrid_scoped(query_vec, query, initial_fetch, candidate_doc_ids)` searches only chunks whose `canonical_source_id` is in the selected document set.

The widening loop uses three config knobs from `RetrievalConfig`:

- `scope_min_hit_count`
- `scope_min_top_score`
- `scope_max_widenings`

The widening ladder drops constraints in this order: `package_managers`, `init_systems`, `major_subsystems`, `os_family`, `source_family`. Explicit document pins are preserved and do not widen away.

The pipeline emits `retrieval_scope_selected` before chunk search:

- `candidate_doc_ids`
- `candidate_count`
- `winning_filter` (`os_family`, `source_family`, `package_managers`, `init_systems`, `major_subsystems`, `explicit_doc_ids`)
- `tier_rankings` with `canonical_source_id`, `canonical_title`, rounded `score`, and `matched_fields`
- `widenings_taken`
- `documents_total`
- `router_hint_present`

### LLM-Facing Tool Surface

The router may pass tool-provided scope into retrieval as:

- `router_hint` — a dict of scope axes such as `os_family`, `source_family`, `package_managers`, `init_systems`, and `major_subsystems`; accepted values are aligned with [app/ingestion/identity/vocabularies.py](app/ingestion/identity/vocabularies.py)
- `explicit_doc_ids` — canonical document IDs, passed through to `ScopeHint.explicit_doc_ids`

The public tool schema surfaces these as `scope_hints` and `canonical_source_ids` on `search_rag_database`. Retrieval treats them as pre-search narrowing signals only; evidence usefulness, gating, and coverage ownership remain with the router/evidence pool.

## Runtime Flow

At runtime, the flow is:

1. Router decides retrieval is needed.
2. Search pipeline checks index compatibility.
3. Query is embedded.
4. The documents table is scoped and widened as needed.
5. Hybrid search runs against LanceDB with the selected `canonical_source_id` set.
6. Candidates are reranked.
7. Source boosting selects anchor chunks.
8. Each anchor builds a preserved same-document bundle:
   - page-based anchors fetch a direct `canonical_source_id` + `page_start`/`page_end` overlap window from the store
   - page-less rows become singleton non-expandable bundles
9. Overlapping same-document bundles are resolved in anchor-rank order:
   - earlier bundles keep overlapping rows/pages
   - later bundles only contribute unseen rows
10. `max_expanded` is enforced at bundle boundaries, never by trimming neighbors out of a chosen bundle.
11. Results are merged and formatted into prompt-ready context with section/page-range citation labels.
12. Responder receives that context.

## Retrieval Keys

Runtime retrieval now uses explicit stable keys:

- `bundle_key`
  - identifies one selected anchor-centered bundle
  - page-based shape: `bundle:{source}:{page_start}-{page_end}:anchor:{anchor_row_key}`
  - page-less shape: `bundle:{source}:singleton:{anchor_row_key}`
- `page_window_key`
  - identifies one delivered contiguous page range from a formatted block
  - shape: `window:{source}:{page_start}-{page_end}`
  - page-less rows do not get a page-window key
- `block_key`
  - identifies one final formatted block delivered to the model
  - page-based shape: `block:{source}:{page_start}-{page_end}`
  - page-less shape: `block:{source}:singleton:{row_key}`
- `region_key`
  - identifies a covered evidence region for pool tracking
  - paged shape: `region:{source}:{page_start}-{page_end}`
  - page-less shape: `region:{source}:singleton:{row_key}`
  - derived from delivered page windows and singleton block keys
  - used by `EvidencePool` to track what evidence is already covered

### Evidence Pool Inputs (accepted by `retrieve_context_result`)

The router's `EvidencePool` passes per-call exclusion and coverage inputs to the search pipeline:

- `excluded_page_windows` — list of `{"key", "source", "page_start", "page_end"}` dicts; the pipeline skips bundles whose page window overlaps these
- `excluded_block_keys` — list of block key strings; used to exclude singleton blocks already covered
- `covered_region_keys` — list of region key strings (informational; echoed back in metadata for pool reconciliation)
- `requested_evidence_goal` — optional internal goal hint used only to bias anchor selection within the retrieval pipeline

### Retrieval Metadata (returned by `retrieve_context_result`)

In addition to existing fields, the pipeline now returns:

- `delivered_region_keys` — region keys for all evidence delivered in this call
- `excluded_region_keys_seen` — region keys that were requested but skipped because they were in `excluded_page_windows` / `excluded_block_keys`
- `net_new_region_count` — count of delivered regions not already in the covered set
- `covered_region_keys_input` — echo of the `covered_region_keys` input (for pool reconciliation)
- `requested_evidence_goal` — echo of the goal hint used for this retrieval call

Current overlap rule:

- same-source anchor bundles may overlap
- overlap is resolved in anchor rank order
- once a row/page has been claimed by an earlier bundle, later bundles do not re-deliver it
- this dedupe happens before formatting, so the final rendered blocks stay coherent

## Retrieval Events

The runtime search pipeline emits events such as:

- `retrieval_search_started`
- `retrieval_candidates_found`
- `retrieval_sources_filtered`
- `retrieval_reranking`
- `retrieval_source_boosting`
- `retrieval_expanding`
- `retrieval_complete`

The router/evidence pool emits additional events:

- `evidence_pool_update` — emitted after every retrieval (fresh or cached) with pool summary state: `query_count`, `evidence_count`, `covered_region_count`, `soft_exhausted_scope_keys`, `hard_exhausted_scope_keys`, `last_outcome`, `last_usefulness`
- `retrieval_gated` — emitted when router-owned scope gating stops a repeated retrieval; includes gate action, exhaustion level, scope key, and caller context

Current observability fields on retrieval pipeline events include:

- anchor count / anchor pages
- fetched neighbor pages
- delivered bundle count
- excluded-seen count
- skipped bundle count
- requested evidence goal

These events are used for observability and live frontend status updates.

Current debug ownership rule:

- the full merged chunk text that the responder reads belongs to the run-level `normalized_inputs` bundle
- retrieval events should stay lightweight and report search/rerank/selection progress plus source metadata, not duplicate the same merged text in every event payload
- responder-triggered retrieval tool calls are separate: the `tool_complete` event for that retrieval call may carry the returned prompt-facing text and merged blocks because that event owns that tool result

## Ingestion Boundary

The retrieval subsystem should not auto-run ingestion at app startup.

Operator ingestion is separate.

The correct boundary is:

- ingestion writes
- retrieval reads/searches

## Safe Change Guidelines

If you modify retrieval, preserve these invariants:

1. Runtime retrieval does not own indexing.
2. Ingestion does not depend on runtime retrieval facades.
3. Provider choice stays abstracted behind provider adapters/factories.
4. Index compatibility checks remain explicit.
5. Tool and retrieval phases remain observable.

## Files To Read First

1. [app/retrieval/config.py](app/retrieval/config.py)
2. [app/retrieval/factory.py](app/retrieval/factory.py)
3. [app/retrieval/search_pipeline.py](app/retrieval/search_pipeline.py)
4. [app/retrieval/store.py](app/retrieval/store.py)
5. [app/retrieval/scope.py](app/retrieval/scope.py)
6. [app/retrieval/index_metadata.py](app/retrieval/index_metadata.py)
7. [app/ingestion/indexer.py](app/ingestion/indexer.py)
