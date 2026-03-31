# CLAUDE.md

Use this file for shared workflow and doc routing when working in this repository. Keep subsystem detail in the dedicated markdown files.

## Core Workflow

- Run backend Python commands inside the `AI-Linux-Assistant` conda environment.
- Before planning or implementing, read the markdown files that cover the surface you are about to change.
- When editing code, re-check the relevant markdown before finishing.
- If code changes affect a documented surface, update the relevant markdown in the same pass.
- If repository workflow or agent instructions change, update both `AGENTS.md` and `CLAUDE.md` in the same pass.

## Task Routing

- General repo orientation:
  - [README.md](/home/kayne19/projects/AI-Linux-Assistant/README.md)
- Backend orchestration, FSM ownership, providers, Magi boundaries:
  - [Back-end/ARCHITECTURE.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/ARCHITECTURE.md)
- Durable chat runs, worker queueing, leases, replay, and concurrency policy:
  - [Back-end/RUNS.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/RUNS.md)
- Memory extraction, resolution, storage, or prompt usage:
  - [Back-end/MEMORY.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/MEMORY.md)
- Retrieval, LanceDB, embeddings, reranking, retrieval providers:
  - [Back-end/RETRIEVAL.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/RETRIEVAL.md)
- Ingestion pipeline, registry updates, indexing flow:
  - [Back-end/INGESTION.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/INGESTION.md)
- FastAPI routes, request/response models, bootstrap/message API behavior:
  - [Back-end/API.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/API.md)
- SSE events, streaming lifecycle, live status behavior:
  - [Back-end/STREAMING.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/STREAMING.md)
- Frontend React state, optimistic streaming UI, council rendering:
  - [Front-end/FRONTEND.md](/home/kayne19/projects/AI-Linux-Assistant/Front-end/FRONTEND.md)
- Router evals and regression runners:
  - [Back-end/evals/README.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/evals/README.md)

## Common Commands

```bash
conda activate AI-Linux-Assistant

# Full dev stack
python run_dev.py

# Backend entry points
cd Back-end && python app/main.py
cd Back-end && python app/AI_Generated_TUI.py
cd Back-end && python -m uvicorn api:create_app --factory --app-dir app --host 0.0.0.0 --port 8000 --reload
cd Back-end && python app/chat_run_worker.py

# Tests
cd Back-end && python -m pytest tests/
cd Back-end && python -m pytest tests/test_router_runtime.py

# Evals
cd Back-end && python scripts/eval/evaluate_router.py
cd Back-end && python scripts/eval/evaluate_router_deep.py

# Ingestion / DB
cd Back-end && python scripts/ingest/ingest_pipeline.py
cd Back-end && python scripts/db/init_postgres_schema.py
```

## Testing Expectations

- Tests live under `Back-end/tests/` and should be named `test_*.py`.
- Coverage priorities are router flow, prompt regressions, provider tool-loop behavior, retrieval behavior, and the memory pipeline (`extract -> resolve -> commit`).
- Durable chat-run behavior should cover idempotent create-run, per-chat/per-user concurrency policy, event replay ordering, and terminalization (`completed` / `failed` / `cancelled`).
- When changing FSMs, update tests to assert the expected state trace.
- When changing memory behavior, cover both committed and unresolved/conflict outcomes.
- Keep tests importable and plain-assert friendly in case `pytest` is unavailable locally.

## Configuration And Security

- Store secrets in `Back-end/.env`; do not commit them.
- Keep default role/provider/model configuration centralized in [Back-end/app/config/settings.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/config/settings.py).
- Retrieval runtime/index configuration lives in [Back-end/app/retrieval/config.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/retrieval/config.py).
- Retrieval device placement may be overridden with `VECTORDB_EMBED_DEVICE` and `VECTORDB_RERANK_DEVICE`.

## Documentation Maintenance

Important maintained docs:

- [README.md](/home/kayne19/projects/AI-Linux-Assistant/README.md)
- [Back-end/ARCHITECTURE.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/ARCHITECTURE.md)
- [Back-end/RUNS.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/RUNS.md)
- [Back-end/MEMORY.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/MEMORY.md)
- [Back-end/RETRIEVAL.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/RETRIEVAL.md)
- [Back-end/INGESTION.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/INGESTION.md)
- [Back-end/API.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/API.md)
- [Back-end/STREAMING.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/STREAMING.md)
- [Front-end/FRONTEND.md](/home/kayne19/projects/AI-Linux-Assistant/Front-end/FRONTEND.md)
- [Back-end/evals/README.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/evals/README.md)

If code changes affect one of those surfaces, updating the relevant markdown is part of the implementation.

## Commits

- For substantial completed changes or multi-step implemented plans, make a git commit unless the user explicitly says not to.
- For those larger implementation commits, use this format:
  - `Added: ...`
  - `Removed: ...`
  - `Fixed: ...`
  - `Implemented: ...`
- Use `none` for sections that do not apply.
- Do NOT add a `Co-Authored-By` trailer or any attribution line to commit messages.
