# AGENTS.md

Use this file for shared agent workflow and doc routing. Keep subsystem detail in the dedicated markdown files.

## Sub-Agent Routing Rules

- The orchestrator should preserve its own context for coordination, integration, and final judgment. Do not use the orchestrator as the default code writer when a scoped implementation can be delegated or returned to an existing capable sub-agent.
- Mandatory routing: only Gemini read-only agents may be launched through `mcp__ai-cli__run`. All GPT/Codex implementation, review, and audit agents must be launched through the native Codex sub-agent spawner.
- Use native Codex sub-agents for ChatGPT/GPT implementation, audit, and review work. Do not spawn ChatGPT/GPT agents through `mcp__ai-cli__run`; the MCP AI CLI is reserved for Gemini read-only agents in this repository workflow.
- Use Gemini only for read-only exploration, repo reading, audits, research, doc review, test-output analysis, and similar investigation. Gemini agents are explicitly allowed to read repository files for these read-only tasks. They must not modify files, run write-oriented commands, or be assigned code generation.
- For read-only exploration tasks, dispatch Gemini sub-agents with the latest available Gemini model via `mcp__ai-cli__run`. Google models are preferred for these tasks to conserve Codex/GPT tokens.
- Always instruct read-only Gemini agents explicitly that they have permission to read the repo and that they must not modify files.
- Use GPT/Codex agents for tasks that require code generation, editing, or any writes to the codebase. Default code-writing workers should be GPT 5.5 low unless the task is mechanical enough for GPT 5.4 mini high, or risky enough to justify a stronger tier.
- A mechanical worker is a GPT 5.4 mini high native Codex sub-agent assigned to a narrow, low-risk, already-designed edit where the correct pattern is obvious from nearby code. Appropriate mechanical work includes adding or updating straightforward tests for existing behavior, applying the same small change across repeated call sites, renaming symbols after the design is settled, updating docs to match completed code, fixing lint/type errors with clear compiler messages, or wiring a simple field through existing request/response models. Do not use a mechanical worker for architecture changes, state-machine changes, auth/security logic, persistence semantics, concurrency behavior, provider/tool-loop logic, retrieval ranking behavior, or ambiguous bugs that require deciding what the system should do.
- Use GPT 5.5 high as the preferred audit agent for completed implementation work. If the audit agent finds a concrete, localized issue it already understands, it should fix the issue itself instead of sending it back through the orchestrator or spawning another worker. Escalate broad, ambiguous, or cross-boundary findings back to the orchestrator for decision.
- Before spawning any new sub-agent, check whether an existing agent already has the relevant context and can safely perform the next step. Reuse existing agents for follow-up questions, fixes in their owned files, and test failures caused by their own patches.
- Keep ownership boundaries explicit: workers may fix issues inside their assigned scope; auditors may fix scoped findings they fully understand; explorers remain read-only; cross-cutting design or ownership changes go back to the orchestrator.
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

# Frontend only when the API is already running
python run_dev.py --frontend-only

# API + workers without the frontend
python run_dev.py --backend-only

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
- Run the smallest test that can disprove the change. Prefer focused test files or pytest node IDs over full suites while iterating.
- Avoid dumping large test output into the orchestrator context. Use quiet pytest output, short tracebacks, early failure, and log files when useful: `python -m pytest tests/test_file.py -q --tb=short --disable-warnings --maxfail=1`.
- If a broader suite is needed, run it near the end and summarize only the command, pass/fail result, failed test names, and the relevant traceback excerpt. Do not paste full logs unless the full output is needed for diagnosis.
- The worker that wrote a patch should run the first targeted verification for that patch. If those tests fail, return the failure to the same worker when possible because it already owns the context.
- Gemini read-only agents may inspect saved test logs and summarize failures, but they must not fix test failures or edit files.
- Docs-only changes do not require tests.
- Tiny mechanical edits may use no tests or one targeted test when the behavior risk is negligible.
- Localized backend changes should run one focused pytest file or node ID that covers the touched behavior.
- FSM, provider/tool-loop, retrieval, memory, streaming, auth/security, persistence, or concurrency changes require targeted tests for the touched behavior and relevant regression coverage.
- Large integration changes should run focused tests during implementation and a broader suite once near completion.
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

## graphify

This project uses a two-layer graphify navigation model:

- `graphify-out/architecture/` is the default navigation surface.
- `graphify-out/architecture/wiki/index.md` is the first file to read for repo orientation.
- `graphify-out/deep/` is reserved for exact code tracing and implementation-level path questions.

Rules:
- Before answering architecture or codebase questions, read `graphify-out/architecture/wiki/index.md` if it exists.
- Use `graphify-out/architecture/graph.json` for subsystem path tracing.
- Use `graphify-out/deep/graph.json` only when the question needs exact code-level tracing.
- If architecture outputs are missing or stale, run `python scripts/graphify_navigation.py architecture`.
- If code tracing outputs are needed or stale, run `python scripts/graphify_navigation.py deep`.
- After modifying code files in this session, run `python scripts/graphify_navigation.py architecture` to refresh default navigation. Run the deep build only when the change affects tracing needs.
- Token targets in `graphify-out/navigation.json` are advisory guardrails. Treat warnings as tuning signals, not automatic failures.
