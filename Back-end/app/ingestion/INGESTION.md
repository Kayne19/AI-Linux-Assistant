# Ingestion Pipeline

This document explains the end-to-end ingestion workflow.

The ingestion system is intentionally treated as a formal backend subsystem, not as an ad hoc script chain.

## Purpose

The ingestion pipeline converts source PDFs into:

- cleaned structured document elements
- enriched context fields
- vector-store rows in LanceDB
- routing-registry updates
- persisted ingest traces

It is an operator workflow.

It is not part of normal chat runtime.

## Entry Point

The CLI entry point is:

- [scripts/ingest/ingest_pipeline.py](scripts/ingest/ingest_pipeline.py)

The real orchestrator lives in:

- [app/ingestion/pipeline.py](app/ingestion/pipeline.py)

That is the main architectural rule:

- `scripts/` are thin wrappers
- `app/` owns the real logic

## Queue Mode

If you point the script at a directory, the pipeline creates:

- `to_ingest/`
- `ingested/`

It will:

1. stage root-level PDFs into `to_ingest/`
2. process PDFs one by one
3. index each successful document into LanceDB
4. move the finished PDF into `ingested/`

This means the ingest runner behaves like a repeated FSM over a document queue.

## FSM States

Per document, the pipeline walks these states:

- `INITIALIZED`
- `VALIDATE_INPUT`
- `INTAKE_RAW`
- `EXPORT_TEXT`
- `UPDATE_REGISTRY`
- `CLEAN_ELEMENTS`
- `ENRICH_CONTEXT`
- `FINALIZE_OUTPUT`
- `INGEST_VECTOR_DB`
- `CLEANUP_ARTIFACTS`
- `COMPLETED`

These states are explicit in [app/ingestion/pipeline.py](app/ingestion/pipeline.py).

## Main Files

### `app/ingestion/pipeline.py`

Owns:

- pipeline config
- per-run context
- per-document FSM progression
- queue mode
- registry update orchestration
- enrichment worker construction
- vector-store indexing handoff
- artifact cleanup
- ingest trace writing

This is the control plane for ingestion.

### `app/ingestion/stages/pdf_intake.py`

Owns PDF extraction into raw structured elements.

### `app/ingestion/stages/cleaner.py`

Owns cleaning and deduping of extracted elements.

### `app/ingestion/stages/context_enrichment.py`

Owns enrichment of eligible chunks with:

- `ai_context`
- `embedding_text`

Current enrichment uses an injected text worker and prompt caching.

### `app/ingestion/indexer.py`

Owns ingestion-side vector indexing.

It reads the finalized JSON artifact, embeds rows, and writes them into LanceDB.

This is ingestion-owned on purpose.

### `app/ingestion/trace.py`

Owns persisted JSON traces for single-file and queue runs.

### `app/ingestion/console.py`

Owns human-readable ingest console output.

## End-To-End Document Flow

For one PDF:

1. Validate input path.
2. Extract raw structured elements from the PDF.
3. Export full document text.
4. Update the routing registry if needed.
5. Clean the extracted elements.
6. Enrich eligible elements with contextual summaries.
7. Finalize the enriched JSON artifact.
8. Index the finalized document into LanceDB.
9. Delete intermediate artifacts after successful indexing.
10. Mark the document complete and record the trace.

## Artifact Lifecycle

The pipeline writes artifacts like:

- raw extraction JSON
- cleaned JSON
- full text export
- final enriched JSON

After a successful vector DB ingest, the pipeline cleans up intermediate artifacts.

If a run fails earlier, artifacts remain available for debugging.

## Registry Update

The pipeline also includes a routing-registry update phase.

That phase:

- extracts document identity
- samples front/tail text
- asks a dedicated updater worker whether the routing registry should change
- merges the suggestion into the registry

This keeps corpus-domain discovery close to ingestion, where it belongs.

## Prompt Caching

Context enrichment uses prompt caching for repeated per-chunk requests within the same document.

The intent is:

- stable document-scoped prefix
- dynamic chunk payload at the end

That keeps repeated enrichment calls cheaper than they would otherwise be.

## Persisted Traces

Every run writes a machine-readable JSON trace under:

- `Back-end/ingest_traces/`

Traces record:

- run mode
- config snapshot
- document count
- state trace per document
- raw/cleaned counts
- duration
- artifacts
- archive destination
- errors

## Safe Change Guidelines

If you modify ingestion, preserve these invariants:

1. The CLI stays thin.
2. The orchestrator stays in `app/ingestion/pipeline.py`.
3. FSM phases stay explicit.
4. Indexing stays ingestion-owned.
5. Console and trace outputs remain useful enough to answer what happened and where a run failed.

## Autonomous Registry Decisions

As of T5, registry update decisions are applied autonomously without human prompts. The `auto_apply_registry_suggestion` function validates LLM output and accepts or rejects it: a suggestion is skipped (treated as a no-op) if the LLM returns nothing, an unrecognized action, or an `upsert` with no label; otherwise the suggestion is accepted as-is. Every decision is written as a JSON line to `Back-end/ingest_traces/audit_<run_id>.jsonl` via the `AuditLog` instance that `IngestPipelineRunner` creates at the start of each `run()` call and closes in a `finally` block. The CLI (`scripts/ingest/ingest_pipeline.py`) no longer prompts interactively when no path argument is given; it prints usage to stderr and exits with code 2.

## Files To Read First

1. [app/ingestion/pipeline.py](app/ingestion/pipeline.py)
2. [app/ingestion/indexer.py](app/ingestion/indexer.py)
3. [app/ingestion/stages/context_enrichment.py](app/ingestion/stages/context_enrichment.py)
4. [app/ingestion/stages/cleaner.py](app/ingestion/stages/cleaner.py)
5. [app/ingestion/stages/pdf_intake.py](app/ingestion/stages/pdf_intake.py)
6. [scripts/ingest/ingest_pipeline.py](scripts/ingest/ingest_pipeline.py)
