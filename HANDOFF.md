# Mass-Ingestion Overhaul — Handoff

**Branch:** `feature/mass-ingestion` (worktree at `.worktrees/mass-ingestion/`)
**Status:** Paused after Task 1 of 14. Resume from Task 2.
**Do not merge to `main`.** The old RAG system must stay functional on `main` until the full plan ships.

This handoff is written to be picked up by **any** coding assistant (Claude, Gemini, GPT, Codex, a human, etc.). Use whichever tools your environment gives you; the instructions below describe *what* to do, not *which tool* to use.

## Read This First

The plan is the source of truth. It is committed at `docs/mass-ingestion-plan.md`. Read it top-to-bottom before writing any code. Do not expand scope beyond what it specifies.

## What's Done

### Task 1 — Identity vocabularies + schema (commit `80c6d17`)

Created:

- `Back-end/app/ingestion/identity/__init__.py`
- `Back-end/app/ingestion/identity/vocabularies.py` — 11 controlled-vocabulary enums (`SourceFamily`, `VendorOrProject`, `DocKind`, `TrustTier`, `FreshnessStatus`, `OsFamily`, `InitSystem`, `PackageManager`, `MajorSubsystem`, `ChunkType`, `IngestSourceType`), plus `ALL_ENUMS` registry and `coerce_enum` / `coerce_enum_list` helpers.
- `Back-end/app/ingestion/identity/schema.py` — `DocumentIdentity` and `ChunkMetadata` dataclasses with `validate()`, `to_dict()`, `from_dict()`.
- `Back-end/tests/test_identity_schema.py` — 39 tests, all passing.

Verification (run from this worktree, with the `AI-Linux-Assistant` conda env active):

```bash
cd Back-end && python -m pytest tests/test_identity_schema.py -v
# → 39 passed
```

## What's Next — Tasks 2 through 14

Execute in this order. For each task: read the relevant `§` section of `docs/mass-ingestion-plan.md`, implement, write tests, run them, run the full `Back-end/tests/` suite to catch regressions, commit per the convention below, move on.

| ID | Task | Depends on | Plan section |
|---|---|---|---|
| T2 | Audit log writer (`app/ingestion/audit.py`) | — | §1 |
| T3 | Sidecar + PDF-meta + heuristics loaders (`identity/sidecar.py`, `pdf_meta.py`, `heuristics.py`) | T1 | §2 |
| T4 | LLM normalizer + resolver + document registry (`identity/llm_normalizer.py`, `resolver.py`, `registry.py`) | T1, T2, T3 | §2 |
| T5 | Autonomous registry + CLI HITL removal (kill `review_registry_suggestion` in `pipeline.py`, remove `input()` in `scripts/ingest/ingest_pipeline.py`) | T2 | §1 |
| T6 | Section hierarchy detection (`stages/sections.py`; preserve `parent_id` in `pdf_intake.py`) | T1 | §3 |
| T7 | Intake & cleaner robustness (kill silent page-drops in `pdf_intake.py`, `stages/sanitizer.py`, per-doc quarantine, mass-mode) | T6 (merge order) | §5 |
| T8 | OpenAI Batch API client (`app/providers/openai_batch.py`; extract `_build_request_kwargs` from `openAI_caller.py`) | — | §4 |
| T9 | Enrichment split into sync + batch modes (refactor `stages/context_enrichment.py`; token usage in `trace.py`) | T8 | §4 |
| T10 | Pipeline FSM + batch runner (new FSM states, durable `ingest_state/<id>/`, `app/ingestion/batch_runner.py`, `scripts/ingest/batch_runner.py`) | T4, T5, T6, T7, T9 | §4 |
| T11 | Indexer schema update — chunks table gets new fields + new `documents` LanceDB table; `source` becomes `canonical_title`; bump `index_metadata` version | T1, T4, T6 | §2 |
| T12 | Retrieval scope pre-narrowing + fallback (`retrieval/scope.py`; modify `search_pipeline.py`, `store.py`, `formatter.py`, `config.py`; emit `retrieval_scope_selected`) | T11 | §6 |
| T13 | Migration script (`scripts/ingest/migrate_identity.py` with `--dry-run`) | T4, T11 | §7 |
| T14 | Status dashboard + doc updates (`scripts/ingest/status.py`; update `INGESTION.md`, `RETRIEVAL.md`) | all prior | §8 |

Critical dependency chain: T3 → T4 → T11 → T12. Do not implement tasks in parallel on the same worktree; stay sequential.

## How to Resume Cleanly

1. `cd /home/kayne19/projects/AI-Linux-Assistant/.worktrees/mass-ingestion`
2. Confirm branch: `git branch --show-current` → `feature/mass-ingestion`
3. Confirm clean: `git status` → working tree clean
4. Confirm head: `git log --oneline -3` — you should see the Task 1 commit `80c6d17` plus any handoff-doc commits.
5. Read `docs/mass-ingestion-plan.md` top to bottom.
6. Pick up at Task 2 (audit log). Paste the relevant plan section into the implementer's prompt — do not make the implementer go hunt for context.

## Conventions Established During Task 1

These are the specific patterns already in play in this repo. Mirror them.

- **Imports:** `Back-end/app/` is on `sys.path`. Imports look like `from ingestion.identity.vocabularies import ...`, **not** `from app.ingestion.identity...`. Mirror this for every new module and every test file.
- **Tests:** `Back-end/tests/test_*.py`, plain `assert` / `pytest` style, no custom fixtures unless necessary. Run with `python -m pytest tests/<file>.py` from inside `Back-end/`.
- **Python env:** Conda env `AI-Linux-Assistant`. On the current host the interpreter lives at `/home/kayne19/miniforge3/envs/AI-Linux-Assistant/bin/python`, but any Python 3.10+ with the env's deps installed works.
- **Dataclasses:** Use `@dataclass(slots=True)` consistently.
- **No emojis, no multi-line docstrings, no explanatory comments unless they document a non-obvious invariant.**
- **Commit convention** (per task): one-line title (e.g. `T2: audit log writer`), blank line, then these sections:

  ```
  Added: <files or features>
  Removed: <files or features or "none">
  Fixed: <issues or "none">
  Implemented: <short explanation>
  ```

  Use `none` where not applicable. Do **not** add a `Co-Authored-By` trailer.

## Controller Discipline

Whoever executes these tasks — as a subagent, as a separate assistant session, or manually:

- **Fresh context per task.** Open a fresh subagent / session / window per task where possible, and pass the full task text inline. Don't make a subagent re-discover context from the plan — quote the relevant plan section into its prompt.
- **Audit after each task.** Read the diff. Run the new tests AND the existing `Back-end/tests/` suite. Only then mark the task complete and move on.
- **One at a time.** Never run multiple implementation subagents in parallel on this worktree — they will conflict on git state.
- **Worktree stays clean between tasks.** Every commit leaves `git status` empty.

## External Context

- **Plan:** committed at `docs/mass-ingestion-plan.md` in this worktree.
- **Ignored directories:** `.worktrees/` and `plans/` are in `.gitignore`. Don't try to commit files under those paths — `docs/` is the right place for persistent notes.
- **Existing routing registry:** `Back-end/app/orchestration/routing_domains.json`. The new document-identity registry (introduced in T4) should sit next to it as `Back-end/app/orchestration/routing_documents.json`.
- **Existing ingested PDFs** (useful for migration verification in T13): `Back-end/data/ingested/` — contains `proxmox-cleaned.pdf`, `Debian_Install_Guide.pdf`, `DockerQuickGuide.pdf`, `The_Linux_Command_Line.pdf`.
- **Existing ingest traces:** `Back-end/ingest_traces/`.

## Invariants to Preserve Until the Branch Is Ready to Merge

- `main` branch / old RAG system must keep working the entire time. Do not break existing retrieval callers before T11 lands.
- `Back-end/app/retrieval/vectorDB.py` stays a thin runtime-only facade (see `Back-end/app/retrieval/RETRIEVAL.md`). Do not reintroduce indexing responsibilities there.
- The memory pipeline and `EvidencePool` stay untouched. Scope pre-narrowing in T12 is additive, not a replacement.
- Controlled enums are code-level artifacts. Adding an enum value is a deliberate code change, not an ingest-time decision.
- `Back-end/app/retrieval/config.py` numeric knobs continue to be the single source of truth for retrieval runtime behavior.

## Known Issues to Watch For

- **Silent page-drops in `pdf_intake.py`** (current behavior): `ProcessPoolExecutor` batches can raise, and the current code swallows the exception and continues without the affected pages. T7 fixes this. Until T7 lands, do not claim a document "ingested successfully" without reading the trace.
- **HITL prompts still live** until T5: `review_registry_suggestion` in `pipeline.py` blocks on `input()`; `scripts/ingest/ingest_pipeline.py` prompts for a path when run with no args. Do not run batch ingestion until T5 is done.
- **Sequential enrichment** stays live until T9. Expect slow single-doc ingests.

## If You're Starting From Zero

1. Clone / fetch the repo, `cd` into it.
2. Check out the branch: `git fetch && git checkout feature/mass-ingestion` — or create the worktree if absent: `git worktree add .worktrees/mass-ingestion feature/mass-ingestion`.
3. Activate the conda env: `conda activate AI-Linux-Assistant`.
4. Run the existing test suite to confirm a green baseline: `cd Back-end && python -m pytest tests/`.
5. Read `docs/mass-ingestion-plan.md`.
6. Read this file.
7. Start Task 2.

Good luck. Read the plan.
