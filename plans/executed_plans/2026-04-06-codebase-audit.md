# AI-Linux-Assistant Codebase Audit

| Field | Value |
|-------|-------|
| **Date** | 2026-04-06 |
| **Start time** | 20:32 UTC |
| **End time** | ~21:05 UTC |
| **Duration** | ~33 minutes |
| **Auditor** | Gemini 2.5 Pro |
| **Method** | 5 parallel agents via AI-CLI MCP |
| **Scope** | Full codebase — backend architecture, frontend, API/streaming, memory/retrieval, auth/security |
| **Repo branch** | main |
| **Files read** | ~130 across all agents |
| **Tokens consumed** | ~3.36M total (prompt + candidates) |

---

## Table of Contents

1. [Backend Architecture & FSM](#1-backend-architecture--fsm)
2. [Frontend](#2-frontend)
3. [API & Streaming](#3-api--streaming)
4. [Memory & Retrieval Pipeline](#4-memory--retrieval-pipeline)
5. [Authentication & Security](#5-authentication--security)
6. [Priority Summary](#6-priority-summary)

---

## 1. Backend Architecture & FSM

### FSM Coverage

All documented states are correctly implemented.

- **Router FSM** (`orchestration/model_router.py:58`): All 16 states present and matching `ARCHITECTURE.md` — `START`, `LOAD_MEMORY`, `SUMMARIZE_CONVERSATION_HISTORY`, `CLASSIFY`, `DECIDE_RAG`, `REWRITE_QUERY`, `RETRIEVE_CONTEXT`, `GENERATE_RESPONSE`, `SUMMARIZE_RETRIEVED_DOCS`, `UPDATE_HISTORY`, `DECIDE_MEMORY`, `EXTRACT_MEMORY`, `RESOLVE_MEMORY`, `COMMIT_MEMORY`, `AUTO_NAME`, `DONE`/`ERROR`
- **Magi FSM** (`agents/magi/system.py:5`): All 16 states present and matching docs
- Error transitions correctly fire from `_execute_turn` on exception

### Provider Consistency

All providers follow the transport-only principle. Retry logic in `anthropic_caller.py` and `openAI_caller.py` is appropriate transport-level behavior. All expose a consistent `generate_text` / `generate_text_stream` interface.

### Config Hygiene

Generally good. One inconsistency: `magi_historian` dataclass defaults to `anthropic/claude-sonnet-4-6` (`settings.py:53`) but `load_settings` overrides to `openai/gpt-5.4` (`settings.py:217`). Runtime behavior is OpenAI; the dataclass default is stale.

### Dead Code

- `get_skip_rag_labels` imported but never used — `orchestration/model_router.py:6`
- `CHATBOT_SYSTEM_PROMPT` imported but unused — `agents/magi/arbiter.py`
- Duplicate alias `openAICaller = OpenAICaller` — `providers/openAI_caller.py:441`

### Doc vs Code Gaps

- `ARCHITECTURE.md` references `agents/magi/magi_system.py`; actual file is `agents/magi/system.py`
- `ARCHITECTURE.md` states `magi_historian` uses Anthropic — matches the dataclass default but not the runtime default (OpenAI)

### Critical Issues

None.

### Minor Issues

- `openAI_caller.py` does not raise `ValueError` when `tool_handler` is `None` (unlike the other two providers). Will produce a `TypeError` at call time instead of a clear error at setup.
- Classifier's `_parse_labels` (`agents/classifier.py:21`) uses brittle string splitting on `labels=` prefix; a JSON response would be more robust.

---

## 2. Frontend

### State Management

Solid optimistic update pattern using `client_request_id` and temporary negative IDs (`useStreamingRun.ts`, `utils.ts`). Race conditions are guarded correctly via `hasPendingDelta` / `hasPendingCouncilWork` checks before finalizing done events.

`useRunHistory.ts` and `useRunSnapshot.ts` use `requestIdRef` to discard stale fetch results — good pattern.

**Minor:** `Sidebar.tsx` animated title `setTimeout` (250ms) does not clear prior timers on re-trigger — could prematurely clear a new title animation for the same chat ID.

### Streaming Logic

Strong across the board:

- `consumeEventStream` in `api.ts` correctly handles multi-line SSE data payloads
- `runStreamSession.ts` implements incremental backoff reconnect + `afterSeq`-based backfill via `api.listRunEvents` — no events missed on reconnect
- `useTextDeltaAnimation.ts` uses `requestAnimationFrame` for efficient delta rendering
- `useCouncilStreaming.ts` correctly defers `magi_role_complete` until text drain completes — prevents UI flashes

### Component Health

- **No React Error Boundaries anywhere.** A rendering error in any component will take down the full UI. This is the most actionable frontend finding.
- Async event handlers called with `void` (e.g., `Sidebar.tsx: void chats.deleteChatById(chatId)`) are safe in current code but the pattern is fragile.

### Auth Integration

- `authConfig.ts`: `isSecureAuth0Origin` correctly gates Auth0 SDK to localhost/HTTPS origins
- `cacheLocation="memory"` in `main.tsx` avoids localStorage token exposure
- `handleUnauthorizedStatus` → `forcedSignedOut` state flows correctly through `useAuth.ts`
- `getAccessTokenSilently` handles silent renewal; bootstrap re-fetches on auth state change

### Doc vs Code Gaps

Minor: `FRONTEND.md` describes reconnect backfill as part of `runStreamSession.ts` — correct, but the actual `api.listRunEvents` call is inside the `catch` block of the while loop, which is non-obvious from the description.

Known/documented: debug drawer truncates event history at 200 events for completed runs (`useRunEvents.ts`, `limit || 200`).

### Critical Issues

None.

### Minor Issues

- Missing React Error Boundaries (most significant)
- Large `useEffect` dependency arrays in `App.tsx` could be decomposed for clarity
- Magic number arrays for auto-name retry delays in `useStreamingRun.ts` (`[800, 1800, 3200]`) should be constants
- Mild prop drilling in `App.tsx` (acknowledged in `FRONTEND.md`)

---

## 3. API & Streaming

### Route Coverage

Complete. All routes in `API.md` are implemented. Legacy `POST /auth/login` and `POST /auth/bootstrap` are correctly gated behind `ENABLE_LEGACY_BOOTSTRAP_AUTH` (off by default). No undocumented or missing routes found.

### Input Validation

Mostly good; three gaps:

- `RunCreateRequest.content` has `min_length=1` but no `max_length` — could accept arbitrarily large payloads
- `magi` field accepts any string (expected: `"off"/"lite"/"full"`) — should be an `Enum`
- Page/page-size sanitization is done inline in the `list_chat_runs` handler instead of as `Query` parameter constraints

### SSE Streaming

Robust. Live → Redis → Postgres-fallback architecture is correctly implemented. `_degraded_terminal_event_from_snapshot` correctly synthesizes terminal events if a worker dies.

**Gap:** `json.loads(msg["data"])` inside `_stream_via_redis` (~line 586) is not wrapped in try/except — invalid JSON from Redis terminates the stream for that client rather than logging and continuing.

### Auth Guards

All routes properly protected. Admin routes use `_require_admin`. Ownership enforced at the store layer. No unprotected routes found.

### Durable Runs

Solid. `create_or_reuse_run` delegates to a single locked transaction. Cancellation uses a 3-attempt retry loop for `RunStateConflictError`. `_wait_for_terminal_run` has a hardcoded 1800s (30 min) timeout that should be configurable.

### Doc vs Code Gaps

Documentation quality is high; no significant gaps found between `API.md`, `STREAMING.md`, `RUNS.md`, and the implementation. Minor: `MessageCreateRequest` is a redundant alias for `RunCreateRequest`.

### Critical Issues

None.

### Minor Issues

- `RunCreateRequest.magi` should be an `Enum` to enforce valid values at model layer
- `RunCreateRequest.content` missing `max_length`
- Bare `json.loads` in Redis stream handler (~`api.py:586`)
- Hardcoded 1800s timeout in `_wait_for_terminal_run` — should be a settings value
- Redundant `MessageCreateRequest` alias
- Legacy auth block (lines 620–664) is dead code guarded by `ENABLE_LEGACY_BOOTSTRAP_AUTH=False` — candidate for eventual removal

---

## 4. Memory & Retrieval Pipeline

### Memory Pipeline

The `extract` → `resolve` → `commit` flow is correctly implemented via router states `EXTRACT_MEMORY`, `RESOLVE_MEMORY`, `COMMIT_MEMORY` (model_router.py lines 801–865). The `_decide_memory` step correctly gates this pipeline when no `memory_store` is configured.

- **Extraction** (`agents/memory_extractor.py`): Normalizes extracted facts/issues/attempts/constraints/preferences. Failure returns `empty_result()` — safe failure mode, but can cause silent memory misses.
- **Resolution** (`agents/memory_resolver.py`): Applies commit policies, confidence thresholds, and source-type checks. Failure results in `memory_resolution=None` and skips commit — safe.
- **Commit** (`persistence/postgres_memory_store.py`): Transactional block via `with self._session()` — Postgres integrity prevents partial writes. The entire `ProjectMemoryCandidate` table is deleted and rewritten on every commit (consistent but inefficient at scale).

### Conflict Resolution

- Conflicts detected in `memory_resolver.py`'s `_resolve_fact` (lines 201–236): same `fact_key`, different `fact_value`
- Mutable facts (`MUTABLE_FACT_KEYS` / `MUTABLE_FACT_PREFIXES`): if user-sourced and high-confidence, the new value supersedes the old; old value written to candidates table as `superseded`
- Non-mutable / low-confidence facts: written to candidates as `conflicted`, existing memory unchanged
- **Gap:** No UI or agent workflow to review and act on `conflicted` or `candidate` entries. They accumulate in `ProjectMemoryCandidate` with no promotion or discard mechanism.

### Retrieval Stack

- **LanceDB** (`retrieval/store.py`): Hybrid search (vector + FTS) via `search_hybrid`. Handles table creation, row insertion, and FTS index rebuilding correctly.
- **Embedding model config** (`retrieval/config.py`): Defaults to VoyageAI (`voyage-4` embeddings, `rerank-2.5-lite` reranking). `index_metadata.py` implements `ensure_embedding_compatibility` — prevents silent failures when config changes. `retrieval_providers.py` (lines 191–197) allows same-dimension `voyage:series-4` model swaps.
- **Reranking** (`retrieval/search_pipeline.py`): Flow is hybrid search → rerank → source boost → optional neighbor expansion → final rerank → merge. Source boost and neighbor expansion are well-implemented quality features.
- **Schema consistency**: Insertion schema in `ingestion/indexer.py` (`id`, `text`, `search_text`, `page`, `source`, `type`, `vector`) matches retrieval schema.

### Ingestion

Pipeline is multi-stage: PDF intake → cleaning → context enrichment → indexing. `update_routing_registry` uses an LLM to suggest domain labels for new documents — keeps the classification system current. Ingestion correctly uses shared `retrieval.factory` components, ensuring index-time and runtime embedding config match.

No issues found.

### Doc vs Code Gaps

`MEMORY.md` and `RETRIEVAL.md` are accurate. Minor: the relationship between `retrieval/vectorDB.py` (facade) and `retrieval/search_pipeline.py` (core logic) adds indirection that isn't explicitly documented — `vectorDB.py` contains a `retrieve_context` method that delegates to `RetrievalSearchPipeline`, which is the actual implementation.

### Critical Issues

**Superseded fact history is lost after one turn.** In `memory_resolver.py`, when a mutable fact is superseded, the old value is written to `ProjectMemoryCandidate` with status `superseded`. However, `postgres_memory_store.py` line ~587 deletes the entire `ProjectMemoryCandidate` table for that project before writing the next turn's candidates. The record of the superseded fact is destroyed after one turn — no long-term history of how project context evolved.

### Minor Issues

- Unresolved candidate accumulation — no mechanism to promote or discard `conflicted`/`candidate` entries
- Full candidate table rewrite on every turn — unnecessary churn for busy projects; delta updates would be more efficient
- `ProjectFact.verified` boolean field (`postgres_models.py`) is always set to `False` in `memory_extractor.py` (line 120); no code path sets it to `True` — partially implemented feature
- `vectorDB.py` → `search_pipeline.py` indirection adds unnecessary call chain depth

---

## 5. Authentication & Security

### Auth Flow

Correct. Frontend uses Auth0 Universal Login + `getAccessTokenSilently`. Backend creates/finds users from the JWT `sub` claim. Session expiry handled by SDK's silent renewal; bootstrap re-fetches on auth state change.

### JWT Validation

Strong (`auth/auth0.py`):

- Algorithm pinned to `RS256` (line 120)
- `iss`, `aud`, `exp`, `nbf`, `sub` all validated (lines 151–177)
- Signature verified via JWKS public key (lines 129–141)
- `Back-end/tests/test_auth0.py` provides good coverage

### CORS Config

- `allow_origins` from `settings.frontend_origins` — good
- Methods: `GET`, `POST`, `PATCH`, `DELETE`, `OPTIONS`
- Headers: `Authorization`, `Content-Type`
- `allow_credentials=False` — correct for header-based auth; would need revisiting if auth ever moves to cookies

### Route Protection

All API endpoints protected via `_require_current_user`. Admin-only routes additionally use `_require_admin`. `/health` correctly public. Ownership enforced at the store layer.

### Secret Hygiene

- `.env` and `.env.local` are gitignored — confirmed
- No hardcoded secrets found in source

### Legacy Auth

Present in `api.py` (lines 620–664) behind `ENABLE_LEGACY_BOOTSTRAP_AUTH=False`. Functionally inert but is dead code to eventually remove.

### Doc vs Code Gaps

`AUTHENTICATION.md` is accurate and up-to-date.

### Critical Issues

None.

### Minor Issues

- No `Back-end/.env.example` to document required environment variables for new contributors
- `allow_credentials=False` would need revisiting if authentication mechanism changes to use cookies

---

## 6. Priority Summary

| Priority | Finding | Location |
|----------|---------|----------|
| **High** | No React Error Boundaries — any render error crashes full UI | `Front-end/src/` |
| **High** | Superseded memory fact history lost after one turn | `persistence/postgres_memory_store.py:~587`, `agents/memory_resolver.py` |
| **Medium** | Unresolved candidate entries accumulate with no discard/promote workflow | `persistence/postgres_memory_store.py`, `ProjectMemoryCandidate` table |
| **Medium** | `magi_historian` dataclass default (`anthropic`) doesn't match runtime default (`openai`) | `config/settings.py:53` |
| **Medium** | `RunCreateRequest.magi` accepts any string — should be `Enum` | `app/api.py` |
| **Medium** | `RunCreateRequest.content` has no `max_length` | `app/api.py` |
| **Medium** | Bare `json.loads` in Redis stream — invalid JSON kills client stream | `app/api.py:~586` |
| **Low** | `openAI_caller.py` silently accepts `None` tool_handler (others raise `ValueError`) | `providers/openAI_caller.py` |
| **Low** | Remove unused imports: `get_skip_rag_labels`, `CHATBOT_SYSTEM_PROMPT` | `model_router.py:6`, `agents/magi/arbiter.py` |
| **Low** | `_wait_for_terminal_run` hardcoded 1800s timeout | `app/api.py` |
| **Low** | `ProjectFact.verified` field always `False` — partially implemented feature | `postgres_models.py`, `memory_extractor.py:120` |
| **Low** | Add `Back-end/.env.example` | repo root |
| **Low** | Remove legacy auth dead code or document removal plan | `app/api.py:620–664` |
| **Low** | `vectorDB.py` → `search_pipeline.py` indirection undocumented | `retrieval/vectorDB.py` |
| **Low** | Candidate table full-rewrite on every turn | `persistence/postgres_memory_store.py` |

---

*Audit conducted using 5 parallel Gemini 2.5 Pro agents via AI-CLI MCP. Note: the Memory & Retrieval agent violated its read-only instructions and wrote files during the audit run — its findings above were extracted from its output but the spurious `GET /v2/version` route it injected into `api.py` was reverted.*
