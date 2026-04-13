# CLAUDE.md

Use this file for shared workflow and doc routing when working in this repository. Keep subsystem detail in the dedicated markdown files.

## Sub-Agent Model Preference

- For read-only tasks (audits, exploration, research, doc review) dispatch sub-agents using the latest available Gemini model via the `mcp__ai-cli__run` tool. Google models are preferred for these tasks to conserve Claude tokens.
- Use Claude models for tasks that require code generation, editing, or any writes to the codebase.
- Always instruct read-only Gemini agents explicitly that they must not modify files.
- **Parallel agent rate limits:** Running 5+ Gemini agents simultaneously will hit capacity limits (`429 MODEL_CAPACITY_EXHAUSTED`). The agents retry with backoff and eventually complete, but total wall time can stretch to 30+ minutes. Expect this and use `mcp__ai-cli__wait` with a generous timeout (600s+), or poll with `mcp__ai-cli__list_processes` / `mcp__ai-cli__get_result` instead of blocking. Stagger large batches if rate pressure is a concern.
- **Rate limits are per model tier:** Quota is shared across all concurrent agents using the same model (e.g. all `gemini-3.1-pro-preview` agents share one bucket). If rate pressure is high, spreading agents across tiers (e.g. some on `gemini-3.1-pro-preview`, others on `gemini-3-flash-preview`) can reduce contention.

## Core Workflow

- Run backend Python commands inside the `AI-Linux-Assistant` conda environment.
- Before planning or implementing, read the markdown files that cover the surface you are about to change.
- When editing code, re-check the relevant markdown before finishing.
- If code changes affect a documented surface, update the relevant markdown in the same pass.
- If repository workflow or agent instructions change, update both `AGENTS.md` and `CLAUDE.md` in the same pass.

## Task Routing

- General repo orientation:
  - [README.md](/home/kayne19/projects/AI-Linux-Assistant/README.md)
- Web authentication, Auth0 setup, secure-origin requirements, and legacy-auth audit notes:
  - [Back-end/app/auth/AUTHENTICATION.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/auth/AUTHENTICATION.md)
- Backend orchestration, FSM ownership, providers, Magi boundaries:
  - [Back-end/app/ARCHITECTURE.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/ARCHITECTURE.md)
- Durable chat runs, worker queueing, leases, replay, and concurrency policy:
  - [Back-end/app/orchestration/RUNS.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration/RUNS.md)
- Memory extraction, resolution, storage, or prompt usage:
  - [Back-end/app/persistence/MEMORY.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/persistence/MEMORY.md)
- Retrieval, LanceDB, embeddings, reranking, retrieval providers:
  - [Back-end/app/retrieval/RETRIEVAL.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/retrieval/RETRIEVAL.md)
- Ingestion pipeline, registry updates, indexing flow:
  - [Back-end/app/ingestion/INGESTION.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/ingestion/INGESTION.md)
- FastAPI routes, request/response models, bootstrap/message API behavior:
  - [Back-end/app/API.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/API.md)
- SSE events, streaming lifecycle, live status behavior:
  - [Back-end/app/streaming/STREAMING.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/streaming/STREAMING.md)
- Agent roles, Magi deliberation, and reasoning units:
  - [Back-end/app/agents/AGENT_ROLES.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents/AGENT_ROLES.md)
- Provider adapters and LLM transport:
  - [Back-end/app/providers/PROVIDERS.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/providers/PROVIDERS.md)
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
- [Back-end/app/auth/AUTHENTICATION.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/auth/AUTHENTICATION.md)
- [Back-end/app/ARCHITECTURE.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/ARCHITECTURE.md)
- [Back-end/app/orchestration/RUNS.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration/RUNS.md)
- [Back-end/app/persistence/MEMORY.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/persistence/MEMORY.md)
- [Back-end/app/retrieval/RETRIEVAL.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/retrieval/RETRIEVAL.md)
- [Back-end/app/ingestion/INGESTION.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/ingestion/INGESTION.md)
- [Back-end/app/API.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/API.md)
- [Back-end/app/streaming/STREAMING.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/streaming/STREAMING.md)
- [Back-end/app/agents/AGENT_ROLES.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents/AGENT_ROLES.md)
- [Back-end/app/providers/PROVIDERS.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/providers/PROVIDERS.md)
- [Front-end/FRONTEND.md](/home/kayne19/projects/AI-Linux-Assistant/Front-end/FRONTEND.md)
- [Back-end/evals/README.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/evals/README.md)

If code changes affect one of those surfaces, updating the relevant markdown is part of the implementation.

## Commits

- For substantial completed changes or multi-step implemented plans, make a git commit unless the user explicitly says not to.
- Generate one short, human-readable commit title for the whole commit, such as a one-to-three word summary.
- Write that title once at the top of the commit message, then follow it with these sections:
  - `Added: ...`
  - `Removed: ...`
  - `Fixed: ...`
  - `Implemented: ...`
- Use `none` for sections that do not apply.
- Do NOT add a `Co-Authored-By` trailer or any attribution line to commit messages.
