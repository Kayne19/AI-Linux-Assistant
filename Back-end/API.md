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

- `POST /auth/login`
- `POST /auth/bootstrap`

Current auth is username-only.

- `POST /auth/login` creates or reuses a user record.
- `POST /auth/bootstrap` creates or reuses the user and returns the initial project/chat tree used by the web app.

### Projects

- `GET /users/{user_id}/projects`
- `POST /projects`
- `GET /projects/{project_id}`
- `PATCH /projects/{project_id}`
- `DELETE /projects/{project_id}`

Projects are the memory boundary for chats.

### Chats

- `GET /projects/{project_id}/chats`
- `POST /projects/{project_id}/chats`
- `GET /chats/{chat_session_id}`
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
- `POST /runs/{run_id}/cancel`
- `POST /runs/{run_id}/fail`
- `POST /runs/{run_id}/requeue`

Current request body:

- `content: str`
- `magi: "off" | "lite" | "full"` (defaults to `"off"`)
- `client_request_id: str` for idempotent create-run semantics

## Backend Ownership

### App Store

The app store owns:

- users
- projects
- chat sessions
- chat messages

Current implementation:

- [app/persistence/postgres_app_store.py](app/persistence/postgres_app_store.py)

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

Current implementation:

- [app/persistence/postgres_run_store.py](app/persistence/postgres_run_store.py)

## Streaming Endpoint

Streaming is run-based.

The backend:

1. creates or reuses a durable run
2. streams persisted run events from `chat_run_events`
3. replays backlog first, then live events
4. emits terminal `done` / `error` / `cancelled` from durable run state
5. keeps `/messages/stream` as a wrapper over that run stream

The frontend is responsible for turning backend event codes into polished human labels.

## Data Models

Current top-level API models include:

- `UserResponse`
- `BootstrapResponse`
- `ProjectResponse`
- `ChatSessionResponse`
- `ChatRunResponse`
- `ChatMessageResponse`
- `AssistantDebugResponse`
- `SendMessageResponse`

These are defined in [app/api.py](app/api.py).

Current notable fields:

- `ChatMessageResponse.council_entries` carries persisted Magi deliberation entries when present.
- `AssistantDebugResponse` includes `state_trace`, `tool_events`, `retrieval_query`, and `retrieved_sources`.
- `ChatSessionResponse.active_run_id` / `active_run_status` expose per-chat background activity.
- `ChatRunResponse.latest_*` fields are snapshot conveniences; `chat_run_events` remains the replay source of truth.

## Important Rules

1. The API should stay thin.
2. Session/project scoping should be enforced in the backend, not inferred by the frontend.
3. Streaming should not bypass persistence or memory rules.
4. The blocking endpoint should remain available as a safe fallback unless deliberately removed.
5. Run creation must be idempotent when the same `client_request_id` is retried for the same chat.
6. Concurrency policy belongs in the durable run system, not in ad hoc API threads.

## Files To Read First

1. [app/api.py](app/api.py)
2. [app/persistence/postgres_app_store.py](app/persistence/postgres_app_store.py)
3. [app/persistence/postgres_run_store.py](app/persistence/postgres_run_store.py)
4. [app/chat_run_worker.py](app/chat_run_worker.py)
5. [app/orchestration/model_router.py](app/orchestration/model_router.py)
