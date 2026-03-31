# Streaming

This document explains how chat streaming works across the backend and frontend.

## Purpose

Streaming exists to improve perceived responsiveness without breaking the backend architecture.

The key rule is:

- partial text can stream live
- final persistence and memory work still happen on the completed response

## Current Scope

Current streaming covers:

- live router state updates
- live backend event updates
- live OpenAI text deltas for the responder path
- final persisted message handoff at completion

Current non-goals:

- Anthropic streaming parity
- generalized multi-provider token streaming
- frontend-owned truth for partial messages

## Event Transport

The transport is Server-Sent Events.

The endpoint is:

- `POST /chats/{chat_session_id}/messages/stream`

Defined in:

- [app/api.py](app/api.py)

## Event Types

The stream currently sends JSON payloads shaped like:

- `type: "state"`
- `type: "event"`
- `type: "done"`
- `type: "error"`

`state` events represent router state transitions.

`event` represents lower-level events like:

- retrieval activity
- streamed text deltas
- memory events
- provider/tool events

`done` contains the final serialized user/assistant messages and debug payload.

`error` reports backend failure.

## Backend Flow

### API Layer

[app/api.py](app/api.py)

Owns:

- building the router
- attaching state/event listeners
- running the router in a background thread
- converting emitted backend events into SSE payloads
- yielding them to the client

### Router

[app/orchestration/model_router.py](app/orchestration/model_router.py)

Owns:

- the full turn FSM
- state transitions
- emitting state-visible and tool-visible events
- buffering and finalizing the completed answer

Important rule:

Memory extraction/resolution/commit still happen after the streamed answer completes, not on partial output.

### Responder

[app/agents/response_agent.py](app/agents/response_agent.py)

Owns:

- response request preparation
- provider streaming handoff
- maintaining responder-specific state transitions

### Provider

[app/providers/openAI_caller.py](app/providers/openAI_caller.py)

Owns:

- transport-level streaming with the Responses API
- text delta emission
- provider-specific request/retry/caching behavior

Providers should not own persistence or router state.

## Frontend Flow

### API Client

[Front-end/src/api.ts](../Front-end/src/api.ts)

Owns:

- opening the streaming POST request
- parsing SSE payloads
- routing `state`, `event`, `done`, and `error` to callbacks

### App UI

[Front-end/src/App.tsx](../Front-end/src/App.tsx)

Owns:

- optimistic temporary user/assistant messages
- applying text deltas into the in-progress assistant message
- mapping backend event codes into human labels
- replacing optimistic messages with the final persisted backend messages at completion

That replacement rule is important:

- backend owns persisted truth
- frontend owns temporary rendering

## Human Labels

The frontend owns the human-readable status copy.

Backend should emit literal machine-friendly codes.

Examples of frontend-facing statuses:

- Thinking
- Reading manuals
- Searching documentation
- Expanding search
- Consulting the marketplace of ideas

The backend should not hardcode those labels.

## Architectural Rules

1. Streaming must not bypass the router FSM.
2. Streaming must not bypass final persistence.
3. Streaming must not commit memory on partial output.
4. Backend emits real state/event signals; frontend owns polished language.
5. Tool and retrieval activity should remain observable.

## Files To Read First

1. [app/api.py](app/api.py)
2. [app/orchestration/model_router.py](app/orchestration/model_router.py)
3. [app/agents/response_agent.py](app/agents/response_agent.py)
4. [app/providers/openAI_caller.py](app/providers/openAI_caller.py)
5. [Front-end/src/api.ts](../Front-end/src/api.ts)
6. [Front-end/src/App.tsx](../Front-end/src/App.tsx)
