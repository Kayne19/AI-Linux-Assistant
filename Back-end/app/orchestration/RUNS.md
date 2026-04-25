# Durable Chat Runs

This document covers the durable chat-run system: queueing, worker execution, concurrency policy, event persistence, replay, cancellation, and operator/debug surfaces.

Read this before changing:

- `chat_runs` / `chat_run_events` persistence shape
- run creation, claiming, lease, or terminalization logic
- worker lifecycle or claim/recovery behavior
- replay/backfill semantics for streaming chat responses
- per-chat or per-user concurrency policy
- idempotent run creation or wrapper endpoint behavior

## Ownership Model

The system is split deliberately:

- The router owns workflow for a single run.
- The durable run system owns run records, concurrency policy, claimability, idempotency, and event persistence.
- Queueing and workers own execution placement, claiming, lease heartbeats, stale-work recovery, and durable event publication.
- A worker is where the router runs. It is not a second workflow orchestrator.

Lease rule:

- worker-owned event, checkpoint, and terminal writes are only valid while that worker still owns the active lease
- once the lease is lost, the old worker must stop writing durable state immediately

## Core Records

The durable run layer is built around two Postgres-backed records.

Startup compatibility rule:

- the run store ensures `chat_runs` and `chat_run_events` exist before querying them
- this keeps older databases from hard-failing bootstrap after the durable-run feature lands
- full schema initialization still belongs to `scripts/db/init_postgres_schema.py`

### `chat_runs`

`chat_runs` stores the durable snapshot for a run, including:

- identity and scope: `id`, `chat_session_id`, `project_id`, `user_id`
- lifecycle: `status`, `run_kind`, `started_at`, `finished_at`, `created_at`
- request metadata: `request_content`, `magi`, `client_request_id`
- convenience snapshot state: `latest_state_code`, `latest_event_seq`, `partial_assistant_text`
- execution tracking: `worker_id`, `lease_expires_at`, `cancel_requested`
- failure/terminal details: `error_message`, `final_user_message_id`, `final_assistant_message_id`

Important boundaries:

- `latest_*` fields are convenience snapshot fields for fast reads.
- `partial_assistant_text` is an optional cache/snapshot only.
- The snapshot record is not the authoritative replay source.
- `run_kind="message"` is the normal user-visible responder path.
- `run_kind="auto_name"` is an internal follow-up run used to execute the router-owned `AUTO_NAME` phase after a streamed first turn completes.

### `chat_run_events`

`chat_run_events` is the authoritative per-run event timeline.

Each event is appended with:

- `run_id`
- `seq`
- `type`
- `code`
- `payload_json`
- `created_at`

Rules:

- event ordering must be monotonic per run
- reconnect/backfill always replays from `chat_run_events`
- terminal UI state comes from durable run terminalization, not from a still-open request thread
- live `text_delta` fanout may exist only in Redis and does not need a durable row per token
- durable partial-text replay uses `text_checkpoint` events with absolute text and a checkpoint window
- live `magi_role_text_delta` fanout may exist only in Redis and does not need a durable row per token
- durable in-progress council replay uses `magi_role_text_checkpoint` events with absolute role text and a per-entry checkpoint window

## Lifecycle

The v1 state model is:

- `queued`
- `running`
- `cancel_requested`
- `pause_requested`
- `paused`
- `completed`
- `failed`
- `cancelled`

High-level lifecycle:

1. API/store validates scope, idempotency, and concurrency policy.
2. A durable `chat_runs` row is created or reused.
3. A worker claims an eligible run and begins execution.
4. The worker runs a fresh router instance for that run.
5. The worker appends durable events and updates run snapshots as the run progresses.
6. The worker terminalizes the run exactly once as `completed`, `failed`, or `cancelled`.
7. On normal completion, final chat messages are persisted once through the normal persistence path.

Paused MAGI lifecycle:

1. A running `message` run in `magi="lite"` or `magi="full"` may receive `POST /runs/{run_id}/pause`.
2. The run is marked `pause_requested` immediately, but the worker continues until the next MAGI pause-safe checkpoint.
3. At that checkpoint the worker writes a resumable `pause_state_json`, emits a durable `paused` row, clears the lease, and stops without persisting final chat messages.
4. `POST /runs/{run_id}/resume` appends optional user intervention, keeps the same `run_id`, and requeues that paused run for the worker.
5. The router resumes the same Magi deliberation from the durable snapshot, then continues to normal completion and single-pass final persistence.

Internal follow-up rule:

- a streamed first-turn message run may queue one internal `auto_name` follow-up run after the main message run completes
- that follow-up run does not persist chat messages and exists only to execute the router-owned naming phase durably and observably

## Concurrency Policy

Concurrency policy belongs in the durable run system, not in ad hoc worker code.

v1 rules:

- one active run per chat is fixed behavior
- same-chat second send while an active run exists returns `409`
- per-user active-run cap is configurable
- the default cap is `MAX_ACTIVE_RUNS_PER_USER_DEFAULT=3`
- paused MAGI runs still count as active for same-chat blocking

Run-kind boundary:

- only `run_kind="message"` counts toward same-chat active-run blocking
- only `run_kind="message"` counts toward the per-user active-run cap
- internal `auto_name` follow-up runs remain durable and claimable, but they do not block the user from sending the next chat message

Design intent:

- future role/plan overrides may change the per-user cap without redesigning the run system
- multi-chat concurrency is allowed subject to the configured user cap and overall capacity
- same-chat queueing is intentionally not supported in v1
- the queue is overload protection, not steady-state UX
- global backpressure still applies even if user-level caps differ

## Idempotent Run Creation

Run creation must be idempotent.

`POST /chats/{chat_session_id}/runs` accepts a `client_request_id` token. If the same chat receives the same token again, the API returns the existing run instead of creating a duplicate.

This protects:

- double-click submit
- browser retries after transient failures
- stream-wrapper retries after network interruption

Compatibility requirements:

- `POST /chats/{id}/messages` must pass or generate a `client_request_id`
- `POST /chats/{id}/messages/stream` must pass or generate a `client_request_id`

The idempotency scope is the chat session, not the entire user or project.

Serialization rule:

- create-run decisions are made inside one run-store transaction
- the store locks the owning user/chat rows before checking idempotency, same-chat activity, and per-user caps
- commit-time uniqueness conflicts must re-read durable state rather than create a second run

## Worker Claiming, Leases, And Recovery

Workers do not invent policy. They claim runs that the durable run system has made eligible.

Expected responsibilities:

- claim eligible queued work
- wake promptly when queued work is signaled through Redis
- stamp a worker identifier / claimant
- set and extend `lease_expires_at`
- append durable run events
- recover stale work when leases expire

Recovery rules:

- lease expiry means the original worker is no longer trusted to own the run
- reclaim logic must avoid duplicate completion and stale-worker durable writes
- stale work must either be safely resumed or explicitly failed/requeued according to run-store policy

Worker-local reuse boundary:

- heavyweight retrieval/provider/runtime components may be reused per worker
- router instances remain ephemeral and per-run
- per-run mutable state, listeners, emitted events, and response buffers must remain isolated
- mutable retrieval/response state must never be shared across concurrent chats
- buffered text must be flushed before any terminal transition is written

Router retrieval scope boundary:

- the `search_rag_database` tool accepts optional `scope_hints` (`os_family`, `source_family`, `package_managers`, `init_systems`, `major_subsystems`) and optional `canonical_source_ids`
- `relevant_documents` remains a routing-domain hint; metadata scoping is carried separately as router hints into the retrieval runtime
- the router validates scope hints against the ingestion vocabularies and filters canonical document IDs against the database facade's known document IDs
- direct responder tool calls and router-owned regular-responder decision searches must forward the same validated `router_hint` and `explicit_doc_ids`
- the EvidencePool fingerprint includes `router_hint` and `explicit_doc_ids`, so a scoped search cannot reuse an unscoped cached result for the same query

Local dev note:

- `run_dev.py` starts `4` worker processes by default
- `python run_dev.py --frontend-only` is the intended local workflow when the API is already running and you only need the Vite client
- each dev worker process defaults to `CHAT_RUN_WORKER_CONCURRENCY=1`, so local dev gets four independent claimers unless overridden

## Cancellation Semantics

Cancellation is cooperative and checkpoint-based.

When a cancel request is accepted:

1. the control plane reads the current run status
2. queued runs are terminalized durably as `cancelled` immediately
3. running runs are marked `cancel_requested`
4. the active worker observes that flag at the next safe checkpoint and finalizes as `cancelled`

Safe checkpoints include:

- before starting a model call
- after a model or tool call returns
- between major router FSM phases
- before final persistence
- during long replay/publication loops if applicable

Persistence rule:

- do not partially persist a final assistant message unless the run has already reached normal completion semantics
- queued-vs-running cancel choice is explicit control-plane logic; the store only exposes atomic transitions

## Pause And Resume Semantics

Pause is also cooperative and checkpoint-based.

Rules:

- only `run_kind="message"` with `magi="lite"` or `magi="full"` can pause
- pause may be requested during opening arguments or discussion, but it is only honored at MAGI discussion checkpoints
- a pause requested during opening arguments stays queued until the first discussion checkpoint, so resume input can land before the first discussion role speaks
- pause does not create a second run or a second user message
- user intervention submitted during pause is stored as run-scoped MAGI data/events, not as a normal chat-thread message
- a paused run remains the active run for that chat until resumed or cancelled

Durable snapshot rule:

- `pause_state_json` stores the resumable MAGI state needed to continue the same deliberation
- replayable pause/resume/intervention visibility still comes from `chat_run_events`
- the snapshot is control state; it is not a substitute for the durable event timeline

## API Surface

The FastAPI layer stays thin.

FastAPI responsibilities:

- validate request payloads and scope
- enforce concurrency and idempotency through the run store
- create durable run records
- expose run snapshots
- expose replayable event streams
- relay durable events to the frontend

FastAPI should not:

- run the durable worker FSM path inline
- own retry/recovery semantics
- duplicate claim logic that belongs in workers and the run store

Primary durable-run endpoints:

- `POST /chats/{chat_session_id}/runs`
- `GET /runs/{run_id}`
- `GET /runs/{run_id}/events`
- `GET /runs/{run_id}/events/stream?after_seq=...`
- `POST /runs/{run_id}/pause`
- `POST /runs/{run_id}/resume`
- `POST /runs/{run_id}/cancel`

Operator/debug endpoints may also expose fail/requeue controls for v1 operations support.

## Wrapper Endpoint Semantics

Existing chat endpoints remain compatibility wrappers over the durable run system.

### `POST /chats/{id}/messages`

Behavior:

1. create or reuse a run
2. wait for terminalization
3. return the final persisted messages and debug payload expected by existing callers

### `POST /chats/{id}/messages/stream`

Behavior:

1. create or reuse a run
2. attach to the durable run event stream immediately
3. stream backlog first when needed
4. continue with live events
5. emit final `done` from durable run terminalization, not from in-request execution

## Frontend Integration Contract

The frontend should treat run state as per-chat, not global.

Expected client behavior:

- track active `runId` per chat
- keep `last_seen_seq` for reconnect
- show hidden-chat status from run snapshots when no live SSE connection exists
- reconnect with `after_seq` and rebuild live state from durable events
- treat `text_checkpoint` as an absolute replace of optimistic assistant text
- treat `text_delta` as live-only append and ignore stale windows already covered by replayed checkpoints
- block same-chat duplicate sends while an active run exists
- reuse the same `client_request_id` for duplicate submit/retry cases

Only the visible chat needs a live SSE attachment. Hidden chats can rely on snapshots until reopened.

## Operator / Debug Surfaces

The durable run system must remain inspectable from the backend.

Useful inspection fields:

- run id
- chat id
- user id
- project id
- status
- worker id / claimant
- lease expiration
- started time / age
- latest state code
- latest event seq
- error message
- cancel flag
- final message ids

Useful v1 admin actions:

- cancel run
- mark failed
- inspect latest event backlog
- requeue stale queued work when appropriate

## Testing Focus

Coverage for this system should include:

- idempotent create-run behavior
- one-active-run-per-chat enforcement
- configurable per-user cap enforcement
- stale lease reclaim without duplicate completion
- monotonic event ordering per run
- snapshot/event-log consistency
- cancellation before terminal persistence
- per-run router isolation across concurrent chats
- wrapper endpoint compatibility for `/messages` and `/messages/stream`

## Related Docs

- [ARCHITECTURE.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/ARCHITECTURE.md)
- [API.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/API.md)
- [STREAMING.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/STREAMING.md)
- [FRONTEND.md](/home/kayne19/projects/AI-Linux-Assistant/Front-end/FRONTEND.md)
