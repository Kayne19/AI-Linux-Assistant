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

The router decides when retrieval matters. Retrieval executes that decision.

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
- retrieval knobs like fetch size, final top-k, neighbor expansion

This is the single source of truth for retrieval runtime configuration.

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
- reranking
- source-profile boosting
- neighbor expansion
- merge retrieved chunks
- format prompt-ready context

It also emits retrieval events so the rest of the system can observe what retrieval did.

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

## Runtime Flow

At runtime, the flow is:

1. Router decides retrieval is needed.
2. Search pipeline checks index compatibility.
3. Query is embedded.
4. Hybrid search runs against LanceDB.
5. Candidates are reranked.
6. Source boosting and neighbor expansion run.
7. Results are merged and formatted into prompt-ready context.
8. Responder receives that context.

## Retrieval Events

The runtime search pipeline emits events such as:

- `retrieval_search_started`
- `retrieval_candidates_found`
- `retrieval_sources_filtered`
- `retrieval_reranking`
- `retrieval_source_boosting`
- `retrieval_expanding`
- `retrieval_complete`

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
5. [app/retrieval/index_metadata.py](app/retrieval/index_metadata.py)
6. [app/ingestion/indexer.py](app/ingestion/indexer.py)
