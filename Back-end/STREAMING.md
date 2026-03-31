# Streaming

This document explains how chat streaming works across the backend and frontend.

## Purpose

Streaming exists to improve perceived responsiveness without breaking the backend architecture.

The key rule is:

- partial text can stream live
- final persistence and memory work still happen on the completed response
- replay comes from durable run events, not a live in-request thread buffer

## Current Scope

Current streaming covers:

- durable run-event replay and live follow
- live router state updates emitted by the claimed worker
- live backend event updates emitted by the claimed worker
- live OpenAI text deltas for the responder path
- live Magi council role deltas/events when `magi` is `lite` or `full`
- final persisted message handoff at completion

Current non-goals:

- Anthropic streaming parity
- generalized multi-provider token streaming
- frontend-owned truth for partial messages

## Event Transport

The transport is Server-Sent Events.

The endpoint is:

- `GET /runs/{run_id}/events/stream`

Compatibility wrapper:

- `POST /chats/{chat_session_id}/messages/stream`

Defined in:

- [app/api.py](app/api.py)

## Event Types

The stream currently sends JSON payloads shaped like:

- `type: "state"`
- `type: "event"`
- `type: "done"`
- `type: "error"`
- `type: "cancelled"`

`state` events represent router state transitions.

`event` represents lower-level events like:

- retrieval activity
- streamed text deltas
- memory events
- provider/tool events
- Magi phase and role events

`done` contains the final serialized user/assistant messages and debug payload.

`error` reports backend failure.

`cancelled` reports a run that observed `cancel_requested` at a safe checkpoint and terminated without normal completion persistence.

## Backend Flow

### API Layer

[app/api.py](app/api.py)

Owns:

- create-run and compatibility wrapper entry points
- reading durable run snapshots
- reading `chat_run_events`
- replay-first SSE delivery
- terminal fallback when a run snapshot is terminal before the client sees the final event

### Router

[app/orchestration/model_router.py](app/orchestration/model_router.py)

Owns:

- the full turn FSM
- state transitions
- emitting state-visible and tool-visible events
- buffering and finalizing the completed answer

Important rule:

Memory extraction/resolution/commit still happen after the streamed answer completes, not on partial output.

### Durable Run System

[app/persistence/postgres_run_store.py](app/persistence/postgres_run_store.py)

Owns:

- durable `chat_run` records
- durable `chat_run_events`
- authoritative replay ordering
- snapshot fields for quick status reads

`chat_run_events` is the replay source of truth.

`chat_runs.latest_*` and `partial_assistant_text` are convenience snapshot fields only.

### Worker

[app/chat_run_worker.py](app/chat_run_worker.py)

Owns:

- claiming eligible runs
- lease heartbeats
- safe cancellation checkpoints
- stale-run failure/recovery handling
- executing the router FSM for one claimed run
- publishing durable state/event records

### Responder

[app/agents/response_agent.py](app/agents/response_agent.py)

Owns:

- response request preparation
- provider streaming handoff
- maintaining responder-specific state transitions
- emitting provider lifecycle events that the router/API can forward over SSE

### Magi

[app/agents/magi/system.py](app/agents/magi/system.py)

Owns:

- the deliberation protocol for `magi="lite"` and `magi="full"`
- role lifecycle events such as `magi_phase`, `magi_role_start`, `magi_role_complete`
- live role text emission through `magi_role_text_delta`
- the final synthesized response handed back to the router

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

- creating runs
- attaching to run event streams
- parsing SSE payloads
- routing `state`, `event`, `done`, `error`, and `cancelled` to callbacks

### App UI

[Front-end/src/App.tsx](../Front-end/src/App.tsx)

Owns:

- per-chat temporary run state
- optimistic temporary user/assistant messages
- applying text deltas into the in-progress assistant message
- rendering Magi council progress when present
- mapping backend event codes into human labels
- reconnecting with `after_seq` when a running chat is reopened
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
6. Durable replay ordering lives in `chat_run_events`, not in frontend cache state.

## Files To Read First

1. [app/api.py](app/api.py)
2. [app/persistence/postgres_run_store.py](app/persistence/postgres_run_store.py)
3. [app/chat_run_worker.py](app/chat_run_worker.py)
4. [app/orchestration/model_router.py](app/orchestration/model_router.py)
5. [app/agents/response_agent.py](app/agents/response_agent.py)
6. [Front-end/src/api.ts](../Front-end/src/api.ts)
