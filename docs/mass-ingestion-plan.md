# Plan: Mass-Ingestion Readiness for the RAG Pipeline

## Context

The RAG ingestion pipeline is a working baseline but is not ready for a large corpus. Three concrete problems block "set and forget" mass ingestion:

1. **Human-in-the-loop gates.** `review_registry_suggestion` in `Back-end/app/ingestion/pipeline.py:188` blocks the pipeline on an interactive prompt every time the registry updater proposes a change. `scripts/ingest/ingest_pipeline.py` also blocks on `input("PDF path: ")` when run without args. Neither survives unattended batch runs.
2. **Thin document identity and chunk metadata.** `Back-end/app/ingestion/indexer.py:29` writes `source = metadata["filename"]` directly, so citations read as `DockerQuickGuide.pdf, Page 13`. The LanceDB schema is six fields (`id`, `text`, `search_text`, `page`, `source`, `type`, `vector`). There is no canonical document identity, no section hierarchy, no subsystem/OS/package-manager tagging, and no controlled vocabulary. As the corpus grows, retrieval will mix operationally different documents (e.g. Debian install guide vs Arch wiki vs a Proxmox admin guide) because they look semantically similar to the embedder.
3. **Sequential synchronous enrichment.** `Back-end/app/ingestion/stages/context_enrichment.py` enriches chunks one-by-one in a `for` loop against OpenAI. For a few thousand chunks per document and low-hundreds of documents, that is both slow and overpriced. The OpenAI Batch API gives a 50% discount and 24h SLA and is a natural fit for a set-and-forget corpus load.

Additional goals the user stated explicitly:
- Keep the local LanceDB; do not move to a hosted vector store.
- Retrieve by narrowing on document families **first** (scoped search), rerank within scope, fall back to broader search only when the scoped result is weak or empty.
- Ingestion continues to own identity extraction and indexing; retrieval reads but never writes.
- Use controlled enums for core identity fields to avoid taxonomy explosion.
- Keep a synchronous fast-path for ad-hoc single-doc ingests (prompt caching stays useful).

Intended outcome: a two-phase resumable queue that can walk hundreds of PDFs unattended, produce rich, controlled-vocabulary metadata, use the Batch API for enrichment, cite documents by their real names, and let retrieval pre-narrow by document family before rerank.

---

## High-Level Shape

```
Phase 1 (synchronous, per document)
  INITIALIZED → VALIDATE_INPUT → LOAD_SIDECAR → INTAKE_RAW → EXPORT_TEXT
  → EXTRACT_IDENTITY → RESOLVE_IDENTITY → UPDATE_REGISTRY
  → CLEAN_ELEMENTS → DETECT_SECTIONS → ENRICH_PREPARE
  → (writes batch JSONL, registers doc as AWAITING_ENRICHMENT, returns)

Phase 2 (asynchronous, corpus-wide)
  ENRICH_SUBMIT → AWAITING_ENRICHMENT → ENRICH_POLL → ENRICH_MERGE
  → FINALIZE_OUTPUT → INGEST_VECTOR_DB → CLEANUP_ARTIFACTS → COMPLETED
```

A document's FSM position and all intermediate artifacts live on disk under `Back-end/ingest_state/<canonical_source_id>/` so the queue survives restarts and partial failures. A single bad PDF quarantines to `failed/` with an error file and never blocks the rest.

Sync fast-path: `--sync` flag runs Phase 1 + Phase 2 in-process using the existing synchronous enrichment worker (prompt caching preserved) for one-off ingests.

---

## 1. Autonomous Registry + Audit Log

**Remove both HITL gates.**

- `Back-end/app/ingestion/pipeline.py:188` — replace `review_registry_suggestion` with `auto_apply_registry_suggestion`. Accept the LLM suggestion whenever confidence is present and structured output validates; otherwise log and skip. Never call `input()`.
- `Back-end/scripts/ingest/ingest_pipeline.py` — if no path argument and no `--queue-dir` is supplied, exit with a usage message. No interactive prompt.

**Audit log.** Every auto-decision (new domain, upserted alias, new canonical document identity, merged identity, fallback used) appends one JSON line to `Back-end/ingest_traces/audit_<run_id>.jsonl`. Shape:

```json
{
  "ts": "...", "run_id": "...", "doc": "canonical_source_id",
  "phase": "registry_update" | "identity_resolution",
  "action": "upsert_domain" | "create_identity" | "merge_identity" | "reject_low_conf" | "fallback_filename",
  "inputs": { "sidecar": {...}, "pdf_meta": {...}, "toc_headings": [...], "llm": {...} },
  "chosen": { ... final resolved value ... },
  "confidence": 0.0,
  "rationale": "short string from LLM or resolver"
}
```

The existing `IngestTraceRecorder` stays, but audit lines are in a separate file so a reviewer can skim decisions without wading through operational telemetry.

**Files:**
- Modify: `Back-end/app/ingestion/pipeline.py` (kill `review_registry_suggestion`, add `auto_apply_registry_suggestion`).
- Modify: `Back-end/scripts/ingest/ingest_pipeline.py` (remove `input(...)`).
- New: `Back-end/app/ingestion/audit.py` (single `AuditLog` class writing JSONL).

---

## 2. Document Identity + Metadata Model

### Document-level schema (one row per document in a new `documents` LanceDB table + JSON registry mirror)

| Field | Type | Controlled? | Derivation |
|---|---|---|---|
| `canonical_source_id` | string | — | Slug from canonical_title + version; stable across re-ingests |
| `canonical_title` | string | — | Resolver output |
| `title_aliases` | list[str] | — | Filename stem, pdf `/Title`, any sidecar aliases |
| `source_family` | enum | yes | e.g. `debian`, `proxmox`, `arch`, `docker`, `kernel`, `linux_generic`, `systemd`, `btrfs` |
| `product` | string | — | Free within family (`proxmox-ve`, `debian-12`) |
| `vendor_or_project` | enum | yes | `debian-project`, `proxmox-server-solutions`, `docker-inc`, `linux-foundation`, `community` |
| `version` | string | — | Version/edition string if known |
| `release_date` | ISO date | — | From metadata/TOC when present |
| `doc_kind` | enum | yes | `admin_guide`, `reference`, `install_guide`, `tutorial`, `manpage`, `wiki`, `release_notes`, `faq`, `api_docs`, `book` |
| `trust_tier` | enum | yes | `canonical` (official + current), `official`, `community`, `unofficial` |
| `freshness_status` | enum | yes | `current`, `supported`, `legacy`, `deprecated`, `archived` |
| `os_family` | enum | yes | `linux`, `bsd`, `windows`, `macos`, `unix`, `any`, `proprietary` |
| `init_systems` | list[enum] | yes | `systemd`, `openrc`, `sysv`, `runit`, `launchd`, `none` |
| `package_managers` | list[enum] | yes | `apt`, `dpkg`, `rpm`, `dnf`, `pacman`, `portage`, `apk`, `nix`, `brew`, `none` |
| `major_subsystems` | list[enum] | yes | `networking`, `storage`, `virtualization`, `containers`, `security`, `kernel`, `boot`, `init`, `filesystems`, `clustering`, `backup`, `gui`, `cli`, `observability` |
| `applies_to` | list[str] | — | Specific targets (`proxmox-ve-8`, `debian-12`, `kernel-6.1+`) |
| `source_url` | string | — | When known (sidecar or LLM) |
| `ingest_source_type` | enum | yes | `pdf_operator`, `pdf_crawl`, `html`, `manpage`, `markdown` |
| `operator_override_present` | bool | — | True iff sidecar YAML supplied any field |
| `ingested_at` | ISO ts | — | Set at FINALIZE |
| `pipeline_version` | string | — | For index compatibility/auditing |

Enums live in `Back-end/app/ingestion/identity/vocabularies.py`. The resolver **rejects** freeform values on enum fields (falls back to closest valid value or `"unknown"` with an audit-log entry). Adding a new enum value is a deliberate code change, not an ingest-time decision — this is the taxonomy-explosion defense.

### Chunk-level schema (added to existing LanceDB `chunks` table)

Existing: `id`, `text`, `search_text`, `page`, `source`, `type`, `vector`.

Add:
- `canonical_source_id` (string, foreign key to documents table)
- `section_path` (list[str]) — e.g. `["Chapter 3 Storage", "3.2 ZFS", "3.2.1 ZFS Pool Creation"]`
- `section_title` (string) — innermost title
- `page_start` (int), `page_end` (int) — range this chunk covers
- `chunk_type` (enum: `narrative`, `list_item`, `code`, `table`, `heading`, `caption`, `uncategorized`)
- `local_subsystems` (list[enum], may be subset of doc-level)
- `entities` (json object): `{"commands": ["systemctl"], "paths": ["/etc/pve"], "services": ["pve-cluster"], "packages": ["proxmox-ve"], "daemons": ["corosync"]}`
- `applies_to_override` (list[str], optional; when set, supersedes doc-level `applies_to` for scoping)

Keep `source` alive as a legacy/display alias equal to `canonical_title` (not the filename) so retrieval's existing filename-based paths still work. The filename is moved to `title_aliases`.

### Precedence for identity resolution

Exactly as requested:

1. **Operator YAML sidecar** (`<pdf>.meta.yaml` next to the PDF).
2. **First-page + TOC heuristics** (pypdf outlines, heading detection in first 5 pages, version-pattern regex, publisher/vendor strings).
3. **PDF /Info metadata** (`/Title`, `/Author`, `/Subject`, `/Producer`).
4. **LLM normalization / gap filling** — single call per document (structured-output, cached). Fills missing enum fields using doc-level context; cannot override values supplied by the three higher-priority sources.

Each layer declares which fields it can set; the resolver merges highest-priority-wins. Every fill event is audit-logged with `source_layer`.

### Files

- New: `Back-end/app/ingestion/identity/__init__.py`
- New: `Back-end/app/ingestion/identity/vocabularies.py` (all enums)
- New: `Back-end/app/ingestion/identity/schema.py` (`DocumentIdentity` dataclass, `ChunkMetadata` dataclass, validators)
- New: `Back-end/app/ingestion/identity/sidecar.py` (`load_sidecar(pdf_path) -> dict | None`)
- New: `Back-end/app/ingestion/identity/pdf_meta.py` (pypdf /Info + outline extraction)
- New: `Back-end/app/ingestion/identity/heuristics.py` (TOC, first-page, version regex, vendor detection)
- New: `Back-end/app/ingestion/identity/llm_normalizer.py` (single structured-output call, cached per-document prefix)
- New: `Back-end/app/ingestion/identity/resolver.py` (precedence merge + enum validation + audit hooks)
- New: `Back-end/app/ingestion/identity/registry.py` (document registry persistence — `routing_documents.json` alongside `routing_domains.json`; also writes rows to a new LanceDB `documents` table at finalize)
- Modify: `Back-end/app/ingestion/pipeline.py` (new FSM states `LOAD_SIDECAR`, `EXTRACT_IDENTITY`, `RESOLVE_IDENTITY`; wire `auto_apply_registry_suggestion` to call the new resolver before the domain-registry update)
- Modify: `Back-end/app/ingestion/indexer.py` (extend chunk row schema, stop writing filename to `source`, write `canonical_title` instead, add all new chunk fields, write doc row to `documents` table)

**Reuse**: the existing `extract_document_identity` (`pipeline.py:124`) already samples front-matter and heading candidates — absorb it into `heuristics.py` rather than replace it. The existing registry-updater LLM call keeps its shape; we replace the interactive gate only.

---

## 3. Section Hierarchy (DETECT_SECTIONS)

The current pipeline discards `unstructured` parent-child relationships in `pdf_intake.py` and never attaches a `section_path` to chunks. Fix this cheaply:

- In `pdf_intake.py`: preserve `parent_id` from `unstructured` elements end-to-end.
- New stage `Back-end/app/ingestion/stages/sections.py`:
  - Walk cleaned elements in reading order.
  - Maintain a stack of active headings keyed by `type in ("Title", "Header")` plus depth inference from font size / numbering patterns (e.g. `3.2.1`).
  - Attach `section_path` and `section_title` to every non-heading element.
  - Emit `chunk_type` by mapping unstructured types → the controlled vocab enum.
- Feed the resulting chunks into enrichment and the indexer unchanged otherwise.

**Reuse**: existing `is_code_like` in `cleaner.py:21` for `chunk_type` classification.

---

## 4. OpenAI Batch API for Enrichment

### Two-mode enrichment

Split `context_enrichment.py` into a request builder + two executors:

- `build_enrichment_requests(elements, doc_context) -> list[BatchRequest]` — produces the exact per-chunk request payloads (same system prompt + `<document_context>` + `<chunk>` shape as today; `pipeline.py:188`-style prompt stays).
- `enrich_sync(requests, worker) -> results` — the existing sequential loop with prompt caching. Used only by `--sync` mode.
- `enrich_batch_prepare(requests, out_path)` — serializes requests to OpenAI Batch JSONL.
- `enrich_batch_submit(jsonl_path) -> batch_id` — uploads file, creates batch.
- `enrich_batch_poll(batch_id) -> status`.
- `enrich_batch_merge(batch_id, results_path, elements) -> enriched_elements` — downloads result JSONL and merges `ai_context` back onto the matching element by `custom_id`.

### Per-doc or corpus-wide batch?

For low-hundreds of PDFs with thousands of chunks each, **per-document batches** are the right unit:
- Simpler merge semantics (one result file → one doc).
- Fault isolation: a bad chunk fails only its doc.
- 24h SLA per batch is fine at this scale; docs progress independently.
- Optional knob `batch_mode: per_doc | corpus_pack` to switch later without structural change.

### Batch runner

- New: `Back-end/app/ingestion/batch_runner.py` — orchestrator that walks `ingest_state/*/status=AWAITING_ENRICHMENT`, polls each `batch_id`, advances the FSM on completion, quarantines on terminal failure.
- New CLI: `Back-end/scripts/ingest/batch_runner.py` — thin wrapper, invokable from cron or manually.
- Runner state lives on disk (one JSON file per doc under `ingest_state/`) so restarts are safe.

### Provider glue

- New: `Back-end/app/providers/openai_batch.py` — thin client around `files.create`, `batches.create`, `batches.retrieve`, `files.content`. No logic beyond transport; retries with exponential backoff matching the existing caller.
- Modify: `Back-end/app/providers/openAI_caller.py` — extract the request-shape building so `openai_batch.py` can reuse the exact same `responses` payload. (The existing retry/backoff wrapper keeps covering synchronous calls.)

### Token / cost telemetry

Persist token usage (cached/input/output) from both sync and batch paths into the existing `IngestTraceRecorder` output. Currently measured but dropped (`trace.py`).

### Files

- Modify: `Back-end/app/ingestion/stages/context_enrichment.py` (split into builder + sync/batch executors)
- New: `Back-end/app/ingestion/batch_runner.py`
- New: `Back-end/scripts/ingest/batch_runner.py`
- New: `Back-end/app/providers/openai_batch.py`
- Modify: `Back-end/app/providers/openAI_caller.py` (extract `_build_request_kwargs` so it is batch-reusable; already at line 225 per exploration)
- Modify: `Back-end/app/ingestion/pipeline.py` (new FSM states `ENRICH_PREPARE`, `ENRICH_SUBMIT`, `AWAITING_ENRICHMENT`, `ENRICH_POLL`, `ENRICH_MERGE`)
- Modify: `Back-end/app/ingestion/trace.py` (record token usage)

---

## 5. Intake & Cleaner Robustness for Mass Ingestion

- **Kill silent page-drops.** `pdf_intake.py:160-163` currently swallows `partition_pdf` exceptions and continues with missing pages. Record each dropped batch to the trace as a hard error, compute a `page_coverage_pct`, and if coverage drops below a threshold (default 0.9) quarantine the document to `data/failed/<name>/` with an error file. Operator sees it in the next audit review; queue moves on.
- **Input sanitization for problem PDFs.** Add a pre-intake `sanitize_pdf` step behind a flag (off by default, on for sources matching a regex list in config). For Proxmox-class files that currently require the `-cleaned.pdf` workaround, the sanitizer uses `pypdf` to strip encrypted bits, normalize annotations, and re-write the file. If the sanitizer fails the doc is quarantined, not crashed.
- **Per-document isolation.** Wrap each doc's FSM in a `try/except`; exceptions move the doc to `failed/` and continue the queue. Already mostly true at the queue level; make it explicit and consistent.
- **Turn `drop_boilerplate` back on by default.** Current `pipeline.py:467` disables it. Default to enabled for corpus runs (`--mass-mode`) because repeated headers/footers hurt embedding quality at scale.

### Files
- Modify: `Back-end/app/ingestion/stages/pdf_intake.py`
- New: `Back-end/app/ingestion/stages/sanitizer.py`
- Modify: `Back-end/app/ingestion/pipeline.py` (coverage threshold, quarantine, mass-mode flag)

---

## 6. Retrieval: Scoped Search With Metadata-Aware Pre-Narrowing

Retrieval now has a structured document table to consult. Add a **scope selection** step before hybrid search.

### New retrieval flow

1. Receive query and (optionally) router scope hint.
2. **Build a scope filter** by inspecting:
   - Router-provided hints (OS family, subsystem, explicit doc name).
   - Query entities (detect commands, paths, package names via a lightweight extractor — reuse the entity list produced at ingest via a reverse index).
   - Memory-derived hints (left intact; retrieval still reads these via existing `requested_evidence_goal`).
3. Produce a candidate document set by intersecting filters on the `documents` table. Rank documents by:
   - Trust tier (canonical > official > community > unofficial)
   - Freshness (current > supported > legacy > archived)
   - Subsystem / OS / package-manager match strength
4. **Scoped hybrid search** over chunks filtered to `canonical_source_id IN candidate_docs`, with anchor/rerank as today.
5. **Fallback policy**:
   - If scoped top-score < threshold, or scoped result count < `min_scoped_hits`, widen by one scope tier (e.g. drop package-manager constraint, then OS family), and re-run.
   - If still weak after two widenings, run global search with a score penalty flag so the responder knows the evidence is out-of-scope.
6. Emit `retrieval_scope_selected` event carrying the chosen candidate doc IDs, the winning filter, tier rankings, and widenings taken. This is the observability channel for "why was this family picked."

### Files
- Modify: `Back-end/app/retrieval/config.py` (scope thresholds, widening policy knobs)
- New: `Back-end/app/retrieval/scope.py` (candidate doc selection, tier ranking)
- Modify: `Back-end/app/retrieval/search_pipeline.py` (call scope first, apply `canonical_source_id` filter on the hybrid query, wire fallback/widening, emit `retrieval_scope_selected`)
- Modify: `Back-end/app/retrieval/store.py` (add filter-by-doc-id support; LanceDB supports `where` predicates natively)
- Modify: `Back-end/app/retrieval/formatter.py` (cite by `canonical_title` + `section_path` + page range instead of filename)
- Modify: `Back-end/app/retrieval/index_metadata.py` (bump index-metadata schema version; enforce doc-table compatibility check)

**Reuse**: existing `EvidencePool` scope/gating logic stays untouched; this plan only adds a pre-filter, not a replacement.

---

## 7. Fresh Re-Ingest Boundary

Existing legacy-indexed documents are not updated in place. The supported path for populating `DocumentIdentity`, document-table rows, and section metadata is a fresh ingestion run from the source PDF and optional sidecar. This keeps the merge focused on correct forward ingestion and avoids maintaining a one-off legacy update path whose output would be weaker than a full re-ingest.

---

## 8. Observability

Everything that makes an autonomous decision or chooses a narrowing scope must be inspectable.

- **Audit JSONL**: per-run file under `ingest_traces/audit_<run>.jsonl` (see §1).
- **Identity resolution trace**: store the layer-by-layer contribution under each doc's trace JSON (`sidecar_layer`, `pdf_meta_layer`, `heuristics_layer`, `llm_layer`).
- **Retrieval scope event**: `retrieval_scope_selected` (see §6) surfaced in the frontend stream alongside existing retrieval events.
- **Token/cost telemetry**: persisted in `IngestTraceRecorder` for both sync and batch paths.
- **Queue dashboard** (optional, small): `scripts/ingest/status.py` — prints the count of docs in each FSM state from `ingest_state/`.

---

## 9. Verification

End-to-end verification for this plan:

1. **Unit tests** (under `Back-end/tests/`):
   - `test_identity_resolver.py` — precedence order, enum validation, field-merge correctness, audit-log contents.
   - `test_sections.py` — `section_path` attached correctly from synthetic elements.
   - `test_batch_runner.py` — FSM advances correctly across AWAITING_ENRICHMENT → ENRICH_MERGE given a mocked OpenAI batch response.
   - `test_scope_selection.py` — scope candidate ranking + fallback widening given synthetic doc metadata.
   - `test_indexer_schema.py` — new chunk/doc rows round-trip in a temp LanceDB table.
   - legacy update removal check — no old update CLI, module, or tests remain.

2. **Integration smoke**:
   - Put two PDFs in `Back-end/data/to_ingest/` (one with a `.meta.yaml` sidecar, one without).
   - Run `python scripts/ingest/ingest_pipeline.py --queue-dir Back-end/data/to_ingest --mass-mode` — it should exit without blocking, having parked both docs as `AWAITING_ENRICHMENT` in `ingest_state/`.
   - Run `python scripts/ingest/batch_runner.py` (with OpenAI creds) — both docs progress to `COMPLETED`.
   - Inspect `routing_documents.json` and the new `documents` LanceDB table for both canonical identities.
   - Issue a RAG query via the API and confirm:
     - `retrieval_scope_selected` event names the expected `source_family`.
     - Citations render as `[canonical_title] §[section_path], p.[page_start]` — not `Something.pdf, Page N`.
   - Inspect `ingest_traces/audit_<run>.jsonl` and verify every auto-decision was logged with rationale and confidence.

3. **Router eval regression**:
   - `cd Back-end && python scripts/eval/evaluate_router.py` — overall accuracy must not regress after fresh re-ingestion.

---

## Critical Files To Modify / Create

**Create**
- `Back-end/app/ingestion/audit.py`
- `Back-end/app/ingestion/identity/{__init__,vocabularies,schema,sidecar,pdf_meta,heuristics,llm_normalizer,resolver,registry}.py`
- `Back-end/app/ingestion/stages/sections.py`
- `Back-end/app/ingestion/stages/sanitizer.py`
- `Back-end/app/ingestion/batch_runner.py`
- `Back-end/app/providers/openai_batch.py`
- `Back-end/app/retrieval/scope.py`
- `Back-end/scripts/ingest/batch_runner.py`
- `Back-end/scripts/ingest/status.py`
- `Back-end/tests/test_identity_resolver.py`
- `Back-end/tests/test_sections.py`
- `Back-end/tests/test_batch_runner.py`
- `Back-end/tests/test_scope_selection.py`
- `Back-end/tests/test_indexer_schema.py`

**Modify**
- `Back-end/app/ingestion/pipeline.py` (new FSM states, autonomous registry, per-doc quarantine, mass-mode flag)
- `Back-end/app/ingestion/indexer.py` (chunk + doc row schema, canonical_title as source)
- `Back-end/app/ingestion/stages/pdf_intake.py` (preserve parent_id, coverage reporting, no silent drops)
- `Back-end/app/ingestion/stages/cleaner.py` (drop_boilerplate default in mass-mode)
- `Back-end/app/ingestion/stages/context_enrichment.py` (split into builder + sync/batch executors)
- `Back-end/app/ingestion/trace.py` (token usage persisted)
- `Back-end/app/providers/openAI_caller.py` (extract request builder for batch reuse)
- `Back-end/app/retrieval/config.py` (scope knobs)
- `Back-end/app/retrieval/search_pipeline.py` (scope pre-filter, widening, `retrieval_scope_selected` event)
- `Back-end/app/retrieval/store.py` (doc-id filter predicate)
- `Back-end/app/retrieval/formatter.py` (citation by canonical_title + section_path)
- `Back-end/app/retrieval/index_metadata.py` (schema version bump, doc-table compat check)
- `Back-end/scripts/ingest/ingest_pipeline.py` (remove `input()`, add `--mass-mode`, `--sync`, `--queue-dir`)
- `Back-end/app/ingestion/INGESTION.md` and `Back-end/app/retrieval/RETRIEVAL.md` (document new FSM states, scope flow, metadata model)

**Do not touch**
- `Back-end/app/retrieval/vectorDB.py` (stays a runtime-only facade per RETRIEVAL.md rule)
- The EvidencePool / router gating surface (scope pre-filter is additive, not a replacement)
- Memory pipeline (retrieval remains the doc-backed detail source)

---

## Out Of Scope (intentionally)

- Moving to a hosted vector DB — user wants to keep LanceDB for hybrid search.
- Rewriting the `unstructured` layout parser — we work around its edge cases, not replace it.
- Building a web UI for audit review — the JSONL file + `status.py` CLI is enough.
- Making the batch runner a daemon/service — a cron-invokable script covers the set-and-forget case.
- Expanding the controlled vocabularies at ingest time — adding enum values stays a deliberate code change.
