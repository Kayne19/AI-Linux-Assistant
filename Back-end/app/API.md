# API

This document describes the current FastAPI surface and the backend ownership rules behind it.

## Purpose

The API is a thin backend entry point.

It should:

- validate request shapes
- resolve user/project/chat context
- enforce durable run/idempotency policy through the run store
- serialize persistent state
- expose run creation, snapshot, cancel, and event-stream endpoints
- keep compatibility wrappers for blocking and streaming message sends

It should not:

- run the durable worker FSM path inside the web request
- own memory policy
- own retrieval policy
- duplicate worker claim/recovery logic

That logic belongs deeper in the backend.

## Main File

- [app/api.py](app/api.py)

## Current Route Surface

### Health

- `GET /health`

Simple liveness check.

### Auth

- `GET /auth/me`
- `GET /app/bootstrap`

Current web auth is Auth0-backed.

- the React app sends `Authorization: Bearer <access_token>`
- FastAPI validates Auth0 access tokens only, using RS256 JWT verification against JWKS, issuer, audience, expiry, and signature
- `GET /auth/me` returns the authenticated local user row
- `GET /app/bootstrap` returns the authenticated user plus the initial owned project/chat tree used by the web app
- legacy `POST /auth/login` and `POST /auth/bootstrap` remain dev-only behind `ENABLE_LEGACY_BOOTSTRAP_AUTH`

Public eval-harness note:

- the eval harness talks to this backend API directly
- it does not need the React frontend to be exposed
- the supported public path is normal bearer-token auth against the existing protected routes

### Projects

- `GET /projects`
- `POST /projects`
- `GET /projects/{project_id}`
- `PATCH /projects/{project_id}`
- `DELETE /projects/{project_id}`

Projects are the memory boundary for chats.

### Chats

- `GET /projects/{project_id}/chats`
- `POST /projects/{project_id}/chats`
- `GET /chats/{chat_session_id}`
- `GET /chats/{chat_session_id}/runs`
- `PATCH /chats/{chat_session_id}`
- `DELETE /chats/{chat_session_id}`

### Messages

- `GET /chats/{chat_session_id}/messages`
- `POST /chats/{chat_session_id}/messages`
- `POST /chats/{chat_session_id}/messages/stream`

Compatibility wrappers over the durable run system.

### Runs

- `POST /chats/{chat_session_id}/runs`
- `GET /runs/{run_id}`
- `GET /runs/{run_id}/events`
- `GET /runs/{run_id}/events/stream`
- `POST /runs/{run_id}/pause`
- `POST /runs/{run_id}/resume`
- `POST /runs/{run_id}/cancel`
- `POST /runs/{run_id}/fail`
- `POST /runs/{run_id}/requeue`

Current request body:

- `content: str` with `1..20000` characters
- `magi: "off" | "lite" | "full"` (defaults to `"off"`)
- `client_request_id: str` with max length `120` for idempotent create-run semantics

Resume request body:

- `input_text: str = ""`
- `input_kind: "fact" | "correction" | "constraint" | "goal_clarification" = "fact"`

Pause semantics:

- `POST /runs/{run_id}/pause` is accepted for active MAGI message runs during opening arguments or discussion
- if pause is requested during opening arguments, the request stays queued and is honored at the first discussion checkpoint so resumed input can appear before the first discussion role speaks

`GET /chats/{chat_session_id}/runs` is the chat-scoped run history endpoint used by the dev/admin debug drawer.

Current query params:

- `page: int = 1` with `page >= 1`
- `page_size: int = 20` with `1 <= page_size <= 100`
- `status: str | None = None`

Current response body:

- `runs: list[ChatRunResponse]`
- `total: int`
- `page: int`
- `page_size: int`
- `has_more: bool`

### Admin Settings

- `GET /admin/settings`
- `PUT /admin/settings`

Admin-only runtime settings surface for:

- model-role configuration
- retrieval runtime tuning
- conversation-history tuning

Current response shape includes:

- top-level model-role entries such as `classifier`, `responder`, and `chat_namer`
- `retrieval`
  - `initial_fetch`
  - `final_top_k`
  - `neighbor_pages`
  - `max_expanded`
  - `source_profile_sample`
- `history_context`
  - `max_recent_turns`
  - `summarize_turn_threshold`
  - `summarize_char_threshold`

Each retrieval/history field is returned as:

- `value: int`
- `is_default: bool`

Current validation rules:

- retrieval/history scalar values must be positive integers except `neighbor_pages`, which allows `0`
- `retrieval.final_top_k <= retrieval.initial_fetch`
- `retrieval.max_expanded >= retrieval.final_top_k`

Current override rule:

- DB `NULL` means "use env/code default"
- admin writes persist explicit override values
- reset-to-default is not exposed by the API yet

## Backend Ownership

### Public Deployment Note

For a public deployment on an existing host:

- run the API as its own long-lived service
- run `chat_run_worker.py` as a separate long-lived service
- keep the API bound to loopback and put Cloudflare Tunnel in front of it

Deployment artifacts live in:

- [Back-end/infra/public-api/README.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/infra/public-api/README.md)
- [ai-linux-assistant-api.service](/home/kayne19/projects/AI-Linux-Assistant/Back-end/infra/public-api/systemd/ai-linux-assistant-api.service)
- [ai-linux-assistant-worker.service](/home/kayne19/projects/AI-Linux-Assistant/Back-end/infra/public-api/systemd/ai-linux-assistant-worker.service)

### App Store

The app store owns:

- users
- projects
- chat sessions
- chat messages

Current implementation:

- [app/persistence/postgres_app_store.py](app/persistence/postgres_app_store.py)

Auth-owned user rules:

- local users are keyed by `(auth_provider, auth_subject)` for web auth
- Auth0 profile fields like `email`, `display_name`, and `avatar_url` are synced profile data, not authorization identifiers
- local `role` remains the authorization source for admin/debug capabilities

### Memory Store

The memory store is project-scoped and is built per active chat session using that session's `project_id`.

Current implementation:

- [app/persistence/postgres_memory_store.py](app/persistence/postgres_memory_store.py)

### Router Construction

The API no longer executes the durable router path directly.

The worker builds a session-scoped router using:

- app store
- memory store
- active `chat_session_id`

That router is responsible for the actual answer workflow for one claimed run.

### Run Store

The run store owns:

- durable `chat_runs`
- durable `chat_run_events`
- idempotent run creation by `client_request_id`
- one-active-run-per-chat enforcement
- configurable per-user active-run cap enforcement
- run snapshots for reconnect and operator/debug inspection
- startup-time creation of missing durable run tables for older databases
- internal run kinds such as the router-owned `auto_name` follow-up path

Current implementation:

- [app/persistence/postgres_run_store.py](app/persistence/postgres_run_store.py)

## Streaming Endpoint

Streaming is run-based.

The backend:

1. creates or reuses a durable run
2. streams persisted run events from `chat_run_events`
3. replays backlog first, then live events
4. emits terminal `done` / `error` / `cancelled` from the durable terminal event row when present
5. keeps `/messages/stream` as a wrapper over that run stream

For partial text:

- assistant live token pacing uses Redis-only `text_delta` plus durable `text_checkpoint`
- Magi council live token pacing uses Redis-only `magi_role_text_delta` plus durable `magi_role_text_checkpoint`

The frontend is responsible for turning backend event codes into polished human labels.

Auth rule for streaming:

- the browser stream path stays `fetch`/`ReadableStream`, not native `EventSource`
- bearer tokens are sent in the `Authorization` header, never in query params
- malformed live Redis payloads are skipped with a warning instead of terminating the client stream

Blocking wait timeout rule:

- the compatibility `POST /chats/{chat_session_id}/messages` path waits for terminal completion using `CHAT_RUN_WAIT_TIMEOUT_SECONDS`
- default is `1800` seconds

## Data Models

Current top-level API models include:

- `UserResponse`
- `AppBootstrapResponse`
- `ProjectResponse`
- `ChatSessionResponse`
- `ChatRunResponse`
- `ChatRunListResponse`
- `ChatMessageResponse`
- `AssistantDebugResponse`
- `SendMessageResponse`

These are defined in [app/api.py](app/api.py).

Current notable fields:

- `ChatMessageResponse.council_entries` carries persisted Magi deliberation entries when present.
- `UserResponse` now includes `display_name`, `email`, `email_verified`, `avatar_url`, optional legacy `username`, and local `role`.
- persisted council entries may now include `entry_kind="user_intervention"` plus `input_kind` for paused-run user intervention rows
- `AssistantDebugResponse` includes `state_trace`, `tool_events`, `retrieval_query`, `retrieved_sources`, and a terminal mirror of the canonical `normalized_inputs` bundle.
- `ChatSessionResponse.active_run_id` / `active_run_status` expose only user-visible active `message` runs, not internal follow-up runs like `auto_name`.
- `ChatRunResponse.run_kind` distinguishes normal `message` runs from internal follow-up runs such as `auto_name`.
- `ChatRunResponse.normalized_inputs` is the canonical run-level debug bundle for prompt-facing inputs such as conversation summary, recent turns, loaded memory snapshot, retrieval query, and merged retrieved context blocks.
- `ChatRunResponse.latest_*` fields are snapshot conveniences; `chat_run_events` remains the replay source of truth.
- Run-event payloads returned by `/runs/{run_id}/events` and `/runs/{run_id}/events/stream` include durable `created_at` timestamps so the frontend can compute timing diagnostics from backend event time.
- `GET /runs/{run_id}/events` supports `after_seq` plus `limit`, and serialized run events include `created_at` for operator/debug timing inspection.
- retrieval tool-completion events for `search_rag_database` may include tool-owned `result_text`, `result_blocks`, `selected_sources`, plus progression metadata such as `cached`, `anchor_pages`, `fetched_neighbor_pages`, `delivered_bundle_count`, and `excluded_seen_count`
- non-terminal stream-stop replay now also includes `type="paused"` for MAGI runs that paused at a safe checkpoint

## Important Rules

1. The API should stay thin.
2. Session/project/run scoping should be enforced in the backend, not inferred by the frontend.
3. Streaming should not bypass persistence or memory rules.
4. The blocking endpoint should remain available as a safe fallback unless deliberately removed.
5. Run creation must be idempotent when the same `client_request_id` is retried for the same chat.
6. Concurrency policy belongs in the durable run system, not in ad hoc API threads.
7. Cancel policy selection is explicit in the control plane: queued runs are terminalized immediately, running runs are marked `cancel_requested`.
8. Paused MAGI intervention is run-scoped control data, not a normal chat-thread user message.
9. No client-supplied `user_id` is trusted on web routes; ownership checks are owner-scoped in the stores.

## Files To Read First

1. [app/api.py](app/api.py)
2. [app/persistence/postgres_app_store.py](app/persistence/postgres_app_store.py)
3. [app/persistence/postgres_run_store.py](app/persistence/postgres_run_store.py)
4. [app/chat_run_worker.py](app/chat_run_worker.py)
5. [app/orchestration/model_router.py](app/orchestration/model_router.py)
