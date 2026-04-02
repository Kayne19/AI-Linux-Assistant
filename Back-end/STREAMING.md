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
- live `text_delta` events for the responder path over Redis fanout
- durable `text_checkpoint` events for reconnect/replay
- live Magi council role deltas/events when `magi` is `lite` or `full`
- durable `magi_role_text_checkpoint` events for in-progress council replay
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
- `seq: int`
- `created_at: str`
- `seq: int`
- `created_at: str`

`state` events represent router state transitions.

`event` represents lower-level events like:

- retrieval activity
- streamed text deltas
- durable text checkpoints
- durable Magi role text checkpoints
- memory events
- provider/tool events
- Magi phase and role events

`done` contains the final serialized user/assistant messages and debug payload.

`error` reports backend failure.

`cancelled` reports a run that observed `cancel_requested` at a safe checkpoint and terminated without normal completion persistence.

That same serialized event shape is reused for:

- `GET /runs/{run_id}/events`
- `GET /runs/{run_id}/events/stream`
- Redis fanout payloads

## Backend Flow

### API Layer

[app/api.py](app/api.py)

Owns:

- create-run and compatibility wrapper entry points
- reading durable run snapshots
- reading `chat_run_events`
- replay-first SSE delivery
- terminal fallback that replays the exact durable terminal event row before degrading to snapshot-derived data

### Router

[app/orchestration/model_router.py](app/orchestration/model_router.py)

Owns:

- the full turn FSM
- state transitions
- emitting state-visible and tool-visible events
- buffering and finalizing the completed answer

Important rule:

Memory extraction/resolution/commit still happen after the streamed answer completes, not on partial output.
First-turn chat auto-naming must not delay the terminal `done` handoff for streamed message runs.

Current streamed naming contract:

- the streamed message run may emit `auto_name_scheduled` before `done`
- the message run still terminalizes normally and hands off the final persisted messages immediately
- if naming is needed, the durable run layer queues a second internal `run_kind="auto_name"` follow-up run
- that follow-up run executes the router's explicit `AUTO_NAME` phase against the persisted opening exchange
- title generation is therefore post-response, but still router-owned and durably observable rather than hidden worker cleanup

### Durable Run System

[app/persistence/postgres_run_store.py](app/persistence/postgres_run_store.py)

Owns:

- durable `chat_run` records
- durable `chat_run_events`
- authoritative replay ordering
- snapshot fields for quick status reads

`chat_run_events` is the replay source of truth.

`chat_runs.latest_*` and `partial_assistant_text` are convenience snapshot fields only.

Terminal replay rule:

- reconnects should reuse the exact durable `done` / `error` / `cancelled` payload when that row exists
- snapshot-derived fallback is a degraded path for missing terminal rows, not the normal source of terminal truth

For partial assistant text:

- `text_delta` is live fanout
- `text_checkpoint` is the durable replay source
- reconnecting clients rebuild text from checkpoints, then only forward newer live delta windows
- active clients should render `text_delta` directly and treat checkpoints as reconnect seeds, not as live display replacements

### Worker

[app/chat_run_worker.py](app/chat_run_worker.py)

Owns:

- claiming eligible runs
- waking promptly when Redis signals newly queued work
- lease heartbeats
- safe cancellation checkpoints
- stale-run failure/recovery handling
- executing the router FSM for one claimed run
- publishing durable state/event records
- batching checkpoint writes off the provider hot path

### Responder

[app/agents/response_agent.py](app/agents/response_agent.py)

Owns:

- response request preparation
- provider streaming handoff
- maintaining responder-specific state transitions
- emitting provider lifecycle events that the router/API can forward over SSE

Important detail:

- the normal responder suppresses raw provider `text_delta` chunks during streaming
- once the streamed provider call completes, it emits one finalized assistant `text_delta` payload into the shared frontend pacing path
- this keeps normal chat streaming aligned with the Magi arbiter path and avoids partial-markdown or tool-round artifacts reaching the UI
- responder sub-states such as `PREPARE_REQUEST`, `REQUEST_MODEL`, `PROCESS_TOOL_CALLS`, and `COMPLETE` are emitted as `responder_state` events with structured `phase/state/details` payloads
- those sub-states are intentionally event-scoped rather than top-level router `state` rows, so the frontend can show them as nested execution detail under `GENERATE_RESPONSE`
- other non-streaming execution events such as provider lifecycle, tool calls, retrieval activity, memory events, and naming events remain normal `event` rows and can be grouped beneath the active router state in debug views

### Magi

[app/agents/magi/system.py](app/agents/magi/system.py)

Owns:

- the deliberation protocol for `magi="lite"` and `magi="full"`
- role lifecycle events such as `magi_phase`, `magi_role_start`, `magi_role_complete`
- explicit deliberation control events such as `magi_discussion_gate`, `magi_discussion_round`, `magi_synthesis_complete`
- live role text emission through `magi_role_text_delta`
- durable in-progress role replay through `magi_role_text_checkpoint`
- the final synthesized response handed back to the router

Important detail:

- `magi_role_text_delta` is visible `position` text emitted from the final parsed role output, not the raw partial JSON produced by the role model
- `magi_role_text_checkpoint` stores that same visible council text as an absolute replace for reconnect/replay
- Magi now exposes an explicit `DISCUSSION_GATE` decision before discussion rounds begin so the frontend/debug surfaces can distinguish "discussion skipped because openings aligned with strong grounding" from "discussion forced because openings diverged or grounding was weak / absent / conflicted"
- Historian grounding quality is part of normal Magi control flow, not just debug annotation
- Arbiter emits required internal synthesis metadata before the final answer is handed back to the router, but only the natural `final_answer` text is sent down the user-facing assistant message path
- Magi arbiter streaming also suppresses partial provider text and emits finalized assistant text into the normal `text_delta` path so the frontend can pace it without partial-JSON or tool-round artifacts

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
- seeding optimistic assistant text from `text_checkpoint` during replay/reconnect
- batching rapid `text_delta` appends into the in-progress assistant message
- rendering Magi council progress when present
- mapping backend event codes into human labels
- reconnecting with `after_seq` when a running chat is reopened
- replacing optimistic messages with the final persisted backend messages at completion

### Debug Drawer

[Front-end/src/debug/](../Front-end/src/debug/)

Owns the dev/admin run inspector UI. It reuses the same durable run APIs and SSE stream as the normal chat UX:

- `GET /chats/{chat_session_id}/runs`
- `GET /runs/{run_id}`
- `GET /runs/{run_id}/events?after_seq=...&limit=...`
- `GET /runs/{run_id}/events/stream`

It does not introduce a separate debug transport.

That replacement rule is important:

- backend owns persisted truth
- frontend owns temporary rendering

## Live Vs Durable Text

Text streaming intentionally uses two paths:

- live display path: worker publishes `text_delta` events to Redis immediately, SSE forwards them, and the frontend appends them with `requestAnimationFrame` batching
- durable replay path: worker writes `text_checkpoint` events to Postgres roughly every 200ms or once a buffered chunk reaches the byte threshold
- Magi uses the same split path per council entry: live `magi_role_text_delta` fanout over Redis plus durable `magi_role_text_checkpoint` rows carrying absolute role text

Frontend rule:

- while a live stream is actively receiving `text_delta`, checkpoint events should be recorded for reconnect state but must not replace the visible assistant text
- while a live council entry is actively receiving `magi_role_text_delta`, `magi_role_text_checkpoint` should seed reconnect state but must not replace the visible live council text
- when replaying after reconnect, checkpoints seed the visible text until live deltas resume

Fallback rule:

- without Redis, SSE falls back to Postgres polling, so clients only see checkpoint-style progress rather than smooth live deltas
- that fallback applies to Magi council text too, so council progress degrades to checkpoint pacing instead of token pacing

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

## Live Fanout via Redis

When `REDIS_URL` is set, events are pushed to connected SSE clients immediately after each Postgres commit rather than on the next 500 ms poll cycle. Live `text_delta` fanout is also published directly to Redis before the next checkpoint reaches Postgres.

### Architecture

```
Worker
  ├─ publish live text_delta to Redis immediately
  └─ append_event() / append_text_checkpoint() / mark_failed() / mark_cancelled() / complete_run_with_messages()
       ├─ INSERT INTO chat_run_events   ← Postgres, always for durable events
       └─ PUBLISH run:{run_id}:events   ← Redis, if REDIS_URL is set
              │
       SSE client (api.py _stream_via_redis)
              ├─ SUBSCRIBE run:{run_id}:events   (opened before Postgres backlog query)
              ├─ replay backlog from Postgres via list_events_after()
              └─ forward Redis messages, filtering stale text_delta windows already covered by replayed checkpoints
```

### Subscribe-before-replay ordering guarantee

The SSE handler subscribes to the Redis channel **before** querying the Postgres backlog. Any events published during the backlog query are buffered by the Redis client and delivered after the backlog is exhausted. Duplicate coverage (seq already sent via backlog) is dropped by seq comparison.

### Health-check fallback

`ps.get_message(timeout=N)` is used instead of a blocking `listen()`. On each timeout the handler checks the run snapshot in Postgres. If the run has reached a terminal status without a matching terminal event in Redis (e.g. worker crashed after Postgres commit but before Redis publish), the handler synthesises the terminal event from the snapshot and closes the stream.

### Degraded mode

When `REDIS_URL` is absent, empty, or the Redis server is unreachable at startup, `get_shared_client()` returns `None` and the SSE path transparently falls back to the existing Postgres poll loop. No configuration change is required to operate without Redis.

### Configuration

| Env var | Default | Purpose |
|---|---|---|
| `REDIS_URL` | _(none)_ | Redis connection URL (e.g. `redis://localhost:6379/0`). Absent = Postgres polling only. |

### Shared serializer

`app/streaming/event_serializer.py` contains `serialize_run_event(seq, event_type, code, payload_json)`. Both the Redis publish path (`postgres_run_store._publish`) and the Postgres replay path (`api._serialize_event_row`) call this function, so the wire format is guaranteed to be identical for live and replayed events.

### Postgres remains authoritative

Redis is fire-and-forget pub/sub. It stores nothing. Every durability guarantee, every replay guarantee, and every recovery guarantee continues to be provided by Postgres `chat_run_events`.

## Text Checkpoint Model

The worker keeps an in-memory delta buffer per run.

- each provider delta is published immediately as live `text_delta`
- the buffer flushes every ~200 ms or when the buffered chunk grows large
- each flush writes one durable `text_checkpoint` event containing absolute assistant text and a monotonic `window`
- reconnecting clients track the highest replayed checkpoint window and drop older live deltas from Redis

## Architectural Rules

1. Streaming must not bypass the router FSM.
2. Streaming must not bypass final persistence.
3. Streaming must not commit memory on partial output.
4. Backend emits real state/event signals; frontend owns polished language.
5. Tool and retrieval activity should remain observable.
6. Durable replay ordering lives in `chat_run_events`, not in frontend cache state.
7. Redis is live-notification only; Postgres is the sole source of truth and replay.

## Files To Read First

1. [app/api.py](app/api.py)
2. [app/persistence/postgres_run_store.py](app/persistence/postgres_run_store.py)
3. [app/streaming/redis_events.py](app/streaming/redis_events.py)
4. [app/streaming/event_serializer.py](app/streaming/event_serializer.py)
5. [app/chat_run_worker.py](app/chat_run_worker.py)
6. [app/orchestration/model_router.py](app/orchestration/model_router.py)
7. [app/agents/response_agent.py](app/agents/response_agent.py)
8. [Front-end/src/api.ts](../Front-end/src/api.ts)
