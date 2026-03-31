# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

All Python commands must run inside the `AI-Linux-Assistant` conda environment:
```bash
conda activate AI-Linux-Assistant
```

## Common Commands

```bash
# Run full dev stack (FastAPI backend + Vite frontend)
python run_dev.py

# Run CLI assistant loop
cd Back-end && python app/main.py

# Run curses TUI (router state + tool visibility)
cd Back-end && python app/AI_Generated_TUI.py

# Run all tests
cd Back-end && python -m pytest tests/

# Run a single test file
cd Back-end && python -m pytest tests/test_router_runtime.py

# Start backend API only
cd Back-end && python -m uvicorn api:create_app --factory --app-dir app --host 0.0.0.0 --port 8000 --reload

# Database schema init
cd Back-end && python scripts/db/init_postgres_schema.py

# Router evaluation
cd Back-end && python scripts/eval/evaluate_router.py
cd Back-end && python scripts/eval/evaluate_router_deep.py

# Document ingestion
cd Back-end && python scripts/ingest/ingest_pipeline.py
```

`run_dev.py` supports `AILA_BACKEND_PORT` (default 8000) and `AILA_FRONTEND_PORT` (default 5173) env overrides.

## Architecture

The system is built around **explicit state machines and observable phases**. The core principle: make it easy to answer "what phase is running, what component owns it, where did state change."

### Router FSM (`Back-end/app/orchestration/model_router.py`)

The router is the top-level orchestration layer and owns the entire turn lifecycle. It runs through these states in order:

`START → LOAD_MEMORY → SUMMARIZE_CONVERSATION_HISTORY → CLASSIFY → DECIDE_RAG → REWRITE_QUERY → RETRIEVE_CONTEXT → GENERATE_RESPONSE → SUMMARIZE_RETRIEVED_DOCS → UPDATE_HISTORY → DECIDE_MEMORY → EXTRACT_MEMORY → RESOLVE_MEMORY → COMMIT_MEMORY → DONE`

State transitions are **conditional** — the trace reflects only actual work performed. Key skip paths:
- No memory store → skip `LOAD_MEMORY`
- `no_rag` label → skip `REWRITE_QUERY` and `RETRIEVE_CONTEXT`
- Empty retrieved docs → skip `SUMMARIZE_RETRIEVED_DOCS`
- No memory store or extractor → `DECIDE_MEMORY` skips to `DONE`
- Memory extraction **never** skips based on labels when a store is present

The router calls agents and providers as injected dependencies — it does not delegate workflow control to them.

### Magi System (`Back-end/app/agents/magi/`)

Alternative response mode toggled per-turn (`magi=True`). Runs a bounded multi-agent deliberation: Eager (hypothesis) → Skeptic (critique) → Historian (ground truth) → optional discussion rounds → Arbiter (synthesis). All roles use GPT-5.4 with full tool access. Has its own `MagiState` FSM with `MAGI_` prefixed trace markers. Deliberation text streams to the frontend via SSE events.

### Layer Boundaries (enforce strictly)

| Layer | Location | Owns | Must NOT |
|---|---|---|---|
| Router | `orchestration/model_router.py` | State transitions, phase ordering, memory pipeline | Contain provider-specific logic |
| Agents | `agents/` | Task-shaped reasoning (classify, respond, extract, resolve) | Hardcode a provider; accept injected workers |
| Providers | `providers/` | Transport/API calls, request formatting, tool-call mechanics | Mutate router state or write to persistence |
| Persistence | `persistence/` | DB queries, storage, merge logic | Contain extraction/policy logic |
| Retrieval | `retrieval/` | Vector search, RAG orchestration | Drive router state directly |

### Product Data Model

- `user` → `project` → `chat_session` → `chat_message`
- Memory is project-scoped: facts, issues, attempts, constraints
- Projects own settings and RAG context

### Entry Points

- `app/main.py` — CLI loop (bootstraps user/project/chat via `orchestration/session_bootstrap.py`, then loops on `router.ask_question()`)
- `app/api.py` — FastAPI factory (`create_app()`); both blocking (`POST /chats/{id}/messages`) and streaming SSE (`POST /chats/{id}/messages/stream`) endpoints
- `app/AI_Generated_TUI.py` — curses TUI

### Configuration

- `Back-end/app/config/settings.py` — `AppSettings` dataclass with per-role model/provider defaults (classifier, responder, extractor, etc.). Override via env vars: `CLASSIFIER_PROVIDER`, `CLASSIFIER_MODEL`, etc.
- `Back-end/.env` — secrets (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DATABASE_URL`, etc.)
- `Back-end/app/retrieval/vectorDB.py` — LanceDB path and embed/rerank device; override via `VECTORDB_EMBED_DEVICE`, `VECTORDB_RERANK_DEVICE`

### Frontend

React 18 + TypeScript + Vite. Thin client — mirrors the backend product model. Key files: `src/App.tsx`, `src/api.ts`, `src/streamStatusText.ts`. Run with `npm run dev` from `Front-end/`.

### Database

PostgreSQL (Neon cloud) with SQLAlchemy ORM and Alembic migrations (`Back-end/alembic.ini`). Local vector store: LanceDB (`Back-end/lancedb_data/`).

## Testing

- Tests in `Back-end/tests/`, named `test_*.py`
- Coverage priorities: router state traces, prompt regressions, provider tool-call formatting, memory pipeline (`extract → resolve → commit`)
- When changing an FSM: assert the expected state trace — trace regressions matter
- When changing memory behavior: test both committed and unresolved/conflict outcomes
- `in_memory_memory_store.py` is the test double for the Postgres memory store

## Documentation

When a change alters any of these surfaces, update the corresponding doc in the same pass:
`README.md`, `Back-end/ARCHITECTURE.md`, `Back-end/MEMORY.md`, `Back-end/RETRIEVAL.md`, `Back-end/INGESTION.md`, `Back-end/API.md`, `Back-end/STREAMING.md`, `Front-end/FRONTEND.md`

## Coding Conventions

- 4-space indentation
- `snake_case` for functions/variables/files, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants
- Imports: standard library → third-party → local
