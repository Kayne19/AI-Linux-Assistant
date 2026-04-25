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
- `LOAD_SIDECAR`
- `INTAKE_RAW`
- `EXPORT_TEXT`
- `EXTRACT_IDENTITY`
- `RESOLVE_IDENTITY`
- `UPDATE_REGISTRY`
- `CLEAN_ELEMENTS`
- `DETECT_SECTIONS`
- `ENRICH_PREPARE`
- `ENRICH_CONTEXT` (sync mode) or `AWAITING_ENRICHMENT` → `ENRICH_SUBMIT` → `ENRICH_POLL` → `ENRICH_MERGE` (batch mode)
- `FINALIZE_OUTPUT`
- `INGEST_VECTOR_DB`
- `CLEANUP_ARTIFACTS`
- `COMPLETED`

These states are explicit in [app/ingestion/pipeline.py](app/ingestion/pipeline.py). In `--batch-mode`, Phase 1 runs through `ENRICH_PREPARE` and parks the document under `ingest_state/<doc_id>/state.json`. Phase 2 is driven separately by `scripts/ingest/batch_runner.py`, which advances the doc through OpenAI Batch submission/poll/merge and onward to indexing.

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

## Mass-Mode and Intake Robustness

Pass `--mass-mode` to the CLI to enable unattended bulk ingestion. Mass mode activates three behaviors: (1) a sanitizer pre-pass (`stages/sanitizer.py`) that rewrites each PDF via pypdf before intake — stripping `/Annots` and normalizing structure — quarantining the document immediately if sanitization fails; (2) a page-coverage threshold (default 0.9) that quarantines any document whose successfully processed pages fall below the fraction; (3) `drop_boilerplate=True` in the cleaner to strip repeated headers/footers across pages. Fine-grained control is available via `--sanitize` (sanitizer only) and `--min-page-coverage <float>`. Quarantined documents are copied to `Back-end/data/failed/<stem>/` along with an `error.json` containing `{reason, page_coverage_pct, processed_pages, total_pages, failed_batches, ts}`. Every quarantine event is written to the per-run audit JSONL with `phase="intake_quarantine"`. Batch-level failures within a single document are now tracked as structured `failed_batches` in `IntakeResult` rather than silently dropped.

## Document Identity And Metadata

Every ingested document carries a structured `DocumentIdentity` (controlled-vocabulary enums for `source_family`, `vendor_or_project`, `doc_kind`, `trust_tier`, `freshness_status`, `os_family`, `init_systems`, `package_managers`, `major_subsystems`, `ingest_source_type`). Identity resolves through ordered deterministic layers — operator sidecar (`<pdf>.meta.yaml`) → first-page/TOC heuristics → PDF `/Info` metadata — and the highest-priority non-empty value wins per field. The optional LLM normalizer exists as a module but is not used by default in live ingestion. See `app/ingestion/identity/{vocabularies,schema,sidecar,pdf_meta,heuristics,llm_normalizer,resolver,registry}.py`. Each layer contribution is audit-logged.

`IngestPipelineRunner.run()` drives `LOAD_SIDECAR`, `EXTRACT_IDENTITY`, `RESOLVE_IDENTITY`, and `DETECT_SECTIONS` before enrichment. Section metadata is attached after cleaning and before sync or batch enrichment. The indexer (`app/ingestion/indexer.py`) receives the resolved `DocumentIdentity`, writes one row per document to the LanceDB `documents` table, and stamps `canonical_source_id`, `section_path`, `page_start`/`page_end`, `chunk_type`, and `entities` on every chunk row. The legacy no-identity indexer path remains only for direct test/backward-compatible callers.

Fresh re-ingest is the only supported path for populating this metadata. The old in-place update path for existing chunks has been removed; existing legacy-indexed documents must be re-ingested to receive document rows and section metadata.

## Two-Phase Batch Ingestion

For corpus-scale runs the enrichment stage can switch from per-chunk synchronous calls to the OpenAI Batch API (50% cost, ~24h SLA). Phase 1 runs through `ENRICH_PREPARE` and writes `batch_input.jsonl`, `requests.json`, `cleaned.json`, and `context.txt` to `ingest_state/<doc_id>/`, then parks the doc as `AWAITING_ENRICHMENT`. Phase 2 is driven by `scripts/ingest/batch_runner.py`, which walks the state directory, submits/polls/merges each batch via the transport-only client at `app/providers/openai_batch.py`, and continues into `INGEST_VECTOR_DB` and `CLEANUP_ARTIFACTS`. Per-doc state on disk makes the runner idempotent across restarts. `--sync` keeps the existing in-process worker for one-off ingests where prompt caching is preferable.

## Status Dashboard

`scripts/ingest/status.py` summarizes the in-flight FSM. It walks `ingest_state/` and the adjacent `failed/` quarantine dir, counts docs per state, and supports `--verbose` (per-doc rows) and `--json` (machine-readable) modes. Use it to answer "how many docs are still waiting on OpenAI?" without grepping the state JSON.

## Files To Read First

1. [app/ingestion/pipeline.py](app/ingestion/pipeline.py)
2. [app/ingestion/indexer.py](app/ingestion/indexer.py)
3. [app/ingestion/identity/resolver.py](app/ingestion/identity/resolver.py)
4. [app/ingestion/stages/context_enrichment.py](app/ingestion/stages/context_enrichment.py)
5. [app/ingestion/stages/sections.py](app/ingestion/stages/sections.py)
6. [app/ingestion/batch_runner.py](app/ingestion/batch_runner.py)
7. [scripts/ingest/ingest_pipeline.py](scripts/ingest/ingest_pipeline.py)
8. [scripts/ingest/batch_runner.py](scripts/ingest/batch_runner.py)
9. [scripts/ingest/status.py](scripts/ingest/status.py)
