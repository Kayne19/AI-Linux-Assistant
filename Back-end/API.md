# API

This document describes the current FastAPI surface and the backend ownership rules behind it.

## Purpose

The API is a thin backend entry point.

It should:

- validate request shapes
- resolve user/project/chat context
- build the correct router for the active chat session
- serialize persistent state
- expose streaming and non-streaming chat endpoints

It should not:

- reimplement router policy
- own memory policy
- own retrieval policy

That logic belongs deeper in the backend.

## Main File

- [app/api.py](app/api.py)

## Current Route Surface

### Health

- `GET /health`

Simple liveness check.

### Auth

- `POST /auth/login`

Current auth is username-only. This creates or reuses a user record.

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

The blocking and streaming send-message endpoints both rely on the router and persistent chat store.

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

The API builds a session-scoped router using:

- app store
- memory store
- active `chat_session_id`

That router is responsible for the actual answer workflow.

## Streaming Endpoint

`POST /chats/{chat_session_id}/messages/stream` streams server-sent events.

The backend:

1. builds the router for the active chat session
2. runs the router in a background thread
3. emits state and event updates over SSE
4. persists the final user and assistant messages through the normal chat store path
5. emits a final `done` event with serialized message payloads

The frontend is responsible for turning backend event codes into polished human labels.

## Data Models

Current top-level API models include:

- `UserResponse`
- `ProjectResponse`
- `ChatSessionResponse`
- `ChatMessageResponse`
- `AssistantDebugResponse`
- `SendMessageResponse`

These are defined in [app/api.py](app/api.py).

## Important Rules

1. The API should stay thin.
2. Session/project scoping should be enforced in the backend, not inferred by the frontend.
3. Streaming should not bypass persistence or memory rules.
4. The blocking endpoint should remain available as a safe fallback unless deliberately removed.

## Files To Read First

1. [app/api.py](app/api.py)
2. [app/persistence/postgres_app_store.py](app/persistence/postgres_app_store.py)
3. [app/orchestration/model_router.py](app/orchestration/model_router.py)
4. [app/persistence/postgres_memory_store.py](app/persistence/postgres_memory_store.py)
