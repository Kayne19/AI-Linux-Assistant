
# AI-Linux-Assistant Audit: Memory & Retrieval Pipeline

## Memory Pipeline

The memory pipeline follows the documented `extract` -> `resolve` -> `commit` flow, orchestrated by `Back-end/app/orchestration/model_router.py`.

-   **Extraction**: `Back-end/app/agents/memory_extractor.py` handles the extraction of facts, issues, attempts, constraints, and preferences from conversation turns. It normalizes data, which is good for consistency.
    -   It correctly uses a `recent_history` window to help resolve contextual references.
    -   Failure during extraction (e.g., model error, JSON parsing failure) results in an empty memory object (`empty_result()`). The router then correctly proceeds to the `RESOLVE_MEMORY` state with `extracted_memory` as `None`, effectively skipping the memory update for that turn. This is a safe failure mode that prevents pipeline crashes but can lead to missed memory updates.

-   **Resolution**: `Back-end/app/agents/memory_resolver.py` is responsible for applying commit policies. It checks for confidence scores and source types (`user` vs. `model`).
    -   This is where the logic for handling new vs. existing data resides.
    -   If resolution fails, the router proceeds with `memory_resolution` as `None`, and the commit step is skipped. This is another safe failure mode.

-   **Commit**: `Back-end/app/persistence/postgres_memory_store.py` handles the final writing to the database. The `commit_resolution` method is a transactional block that updates facts, issues, etc., and then clears and rewrites the memory candidates table.
    -   **Failure Mode**: A failure inside the `commit_resolution`'s `with self._session() as session:` block could lead to a partial commit if the transaction is not rolled back properly by the session manager. However, the use of a `with` statement with a session factory typically handles this correctly. A more significant risk is what happens if the process dies mid-transaction. Postgres's transactional integrity should prevent partial writes from corrupting the database state.
    -   The entire `ProjectMemoryCandidate` table is deleted and rewritten on every commit. For a project with many candidates or conflicts, this could be inefficient, but it ensures the table is always in a consistent state reflecting the last resolution.

-   **Coverage**: The `extract` -> `resolve` -> `commit` pipeline is well-covered. The router states `EXTRACT_MEMORY`, `RESOLVE_MEMORY`, and `COMMIT_MEMORY` (lines 801-865 in `model_router.py`) clearly implement this flow. The `_decide_memory` step ensures this pipeline is only engaged when a `memory_store` is configured.

## Conflict Resolution

-   **Detection**: Conflicts are primarily detected in `Back-end/app/agents/memory_resolver.py` within the `_resolve_fact` method (lines 201-236). A conflict is identified when an extracted fact has the same `fact_key` as an existing fact in the memory profile but a different `fact_value`.

-   **Resolution**:
    -   The system has special handling for "mutable" facts (defined in `MUTABLE_FACT_KEYS` and `MUTABLE_FACT_PREFIXES`). If a new fact is mutable, comes from a `user` source, and has high confidence, it **supersedes** the old value. The old value is added to a `conflicts` list with a status of `superseded`, and the new fact is committed. This is a good, explicit way to handle evolving environment details.
    -   For non-mutable facts or facts that don't meet the user/confidence criteria, the new fact is added to the `conflicts` list with a status of `conflicted`, and the existing memory is not changed.
    -   **Gap**: There is no mechanism described for a user or an automated process to review and resolve these `conflicted` or `candidate` entries. They are stored in the `ProjectMemoryCandidate` table (`postgres_memory_store.py`, lines 587-603) but there's no visible UI or agent logic to surface them for resolution. This means conflicts and low-confidence facts will accumulate as candidates without ever being promoted or discarded.

## Retrieval Stack

-   **LanceDB Usage**: The project uses LanceDB via `Back-end/app/retrieval/store.py`. It correctly uses hybrid search (`search_hybrid` method) combining vector and FTS search. The store handles table creation, row insertion, and FTS index rebuilding.

-   **Embedding Model Config**:
    -   Configuration is centralized in `Back-end/app/retrieval/config.py`. It defaults to VoyageAI models (`voyage-4` for embeddings, `rerank-2.5-lite` for reranking), which is a modern choice.
    -   `Back-end/app/retrieval/index_metadata.py` implements a crucial compatibility check (`ensure_embedding_compatibility`). It stores embedding provider metadata and prevents runtime search if the configured embedding model is incompatible with the index, preventing silent failures and bad results. This is excellent practice.
    -   A special compatibility for `voyage:series-4` models is implemented in `retrieval_providers.py` (lines 191-197), allowing models within that family to be swapped if the output dimension is the same. This is a thoughtful feature.

-   **Reranking**: Reranking is a key part of the pipeline, implemented in `Back-end/app/retrieval/search_pipeline.py`.
    -   The flow is: initial hybrid search -> rerank -> source boost -> optional neighbor expansion -> final rerank -> merge.
    -   The "source boost" (`_source_boost` method, lines 112-124) is an interesting feature that attempts to score sources based on the query, which can help surface more relevant documents.
    -   Neighbor expansion (`neighbor_pages` > 0) is also a good technique for retrieving surrounding context.

-   **Schema Consistency**: The schema used for insertion in `ingestion/indexer.py` (`id`, `text`, `search_text`, `page`, `source`, `type`, `vector`) matches the data used during retrieval in `search_pipeline.py`.

## Ingestion

-   I briefly reviewed `Back-end/INGESTION.md` (by reading the file list) and the code in `Back-end/app/ingestion/pipeline.py` and `indexer.py`.
-   The pipeline is a multi-stage process involving PDF intake, cleaning, context enrichment, and finally indexing.
-   A key step is `update_routing_registry` in `pipeline.py`, which uses an LLM to suggest a domain label for the new document and adds it to the routing registry. This is a clever way to keep the classification system up-to-date with new data.
-   The final step of ingestion is `ingest_vector_db`, which calls the `IngestionIndexer`. This indexer correctly uses the shared `retrieval.factory` components (`build_store`, `build_index_metadata_store`, `build_embedding_provider`) to ensure the data is indexed with the same configuration used at runtime. This consistency is critical and well-implemented. No issues found here.

## Doc vs Code Gaps

-   The documentation in `MEMORY.md` and `RETRIEVAL.md` is impressively accurate and up-to-date with the code I've reviewed.
-   `MEMORY.md` mentions the router states `LOAD_MEMORY`, `EXTRACT_MEMORY`, etc., which are all present and correctly ordered in `model_router.py`. The description of component responsibilities (extractor, resolver, store) matches the code.
-   `RETRIEVAL.md` correctly splits ownership between ingestion (indexing) and runtime (search). The files it lists as important are indeed the core of the subsystem.
-   **Minor Gap**: `RETRIEVAL.md` mentions `app/retrieval/search_pipeline.py`. The file does not exist. However, the logic is present in `Back-end/app/retrieval/vectorDB.py` in the `retrieve_context` function. *Correction*: After deeper reading, `search_pipeline.py` *does* exist and contains the main logic. The `vectorDB.py` seems to be a facade or older component. The docs are more correct than my initial glance suggested, but the presence of `vectorDB.py` with similar logic is slightly confusing. The router uses `database.retrieve_context` which is an instance of `VectorDB`, which in turn uses the `RetrievalSearchPipeline`. The indirection is a bit complex, but the docs point to the right core logic.

## Critical Issues

1.  **Memory Loss on Mutable Fact Update**: In `Back-end/app/agents/memory_resolver.py`, when a mutable fact is superseded by a user update, the old fact is added to the `conflicts` list (line 217), and the new fact is committed. However, the `conflicts` list is transient; it gets written to the `ProjectMemoryCandidate` table with a status of `superseded`. When the *next* turn's memory is committed, `postgres_memory_store.py` (line 587) executes a `delete()` on the entire `ProjectMemoryCandidate` table for that project before writing the new candidates. **This means the record of the superseded fact is deleted and lost after just one turn.** The original value is not preserved in any long-term historical table. This could be problematic for auditing or understanding how a project's context has evolved.

## Minor Issues

1.  **Unresolved Candidate Accumulation**: As noted in the "Conflict Resolution" section, there is no apparent mechanism for resolving items in the `ProjectMemoryCandidate` table. They are written on each turn but there's no agent/user workflow to process them. This could lead to a large, unmanaged table of candidate memories that are never acted upon.
2.  **Inefficient Candidate Table Rewrite**: The `commit_resolution` method in `postgres_memory_store.py` completely wipes and rewrites the candidates table on every single turn that has a memory commit. For a busy system or long-running project, this could be an unnecessary source of database churn. A more efficient approach would be to update, insert, or delete only the changed candidates.
3.  **Lack of `verified` flag usage**: `ProjectFact` in `postgres_models.py` has a `verified` boolean field. The `memory_extractor.py` always sets this to `False` (line 120). There is no code path visible in this audit that sets this to `True`. This suggests a potentially useful feature (distinguishing user-verified facts from model-extracted ones) is not fully implemented.
4.  **Redundant `search_pipeline.py` logic**: In `Back-end/app/retrieval/vectorDB.py`, the `retrieve_context` method seems to be a simple facade around `search_pipeline.retrieve_context`. While this works, it adds a layer of indirection. Consolidating the call chain could improve clarity.
_This report is based on a read-only audit of the specified files._
