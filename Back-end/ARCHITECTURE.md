# Backend Architecture Doctrine

This document defines the architectural rules that keep the backend coherent as the project grows.

It is not a general code style guide. It is a control-boundary guide.

## Purpose

The backend is designed to be:

- explicit instead of magical
- phase-driven instead of helper-driven
- task-shaped instead of provider-shaped
- observable instead of opaque
- scoped by backend state instead of model choice

The system should always make it easy to answer:

- What phase is running?
- What component owns this phase?
- Why did retrieval happen or not happen?
- Where did memory or session state change?
- Which tool calls occurred?

## Core Principles

### 1. The Router Owns Workflow

The router in [model_router.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration/model_router.py) is the control plane.

It owns:

- turn lifecycle
- state transitions
- phase ordering
- tool visibility
- memory pipeline coordination
- retrieval decisions

It should not delegate workflow ownership to helpers or provider adapters.

If a behavior materially changes the lifecycle of a turn, it belongs in the router or in a router-owned phase.

### 1a. Durable Run State Owns Concurrency Policy

Durable chat-run state does not replace the router.

It owns:

- run records
- idempotent create-run semantics
- one-active-run-per-chat enforcement
- configurable per-user active-run caps
- claimability
- durable event persistence

Workers do not become a second orchestrator.

They claim eligible runs, execute the router FSM, heartbeat leases, and publish durable events.

The router still owns the workflow of the turn that is running inside that worker.

### 2. Important Work Must Be Visible

Important backend behavior should appear as explicit phases, traces, events, or persisted state transitions.

Prefer:

- `LOAD_MEMORY`
- `EXTRACT_MEMORY`
- `RESOLVE_MEMORY`
- `COMMIT_MEMORY`
- `AUTO_NAME`

over:

- hidden helper calls with silent side effects

If a subsystem has meaningful internal phases, model them.

### 3. Agents Stay Task-Shaped

Agents exist to perform distinct backend jobs.

Examples in [agents](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents):

- classifier
- contextualizer
- responder
- memory extractor
- memory resolver
- summarizers

These should remain task-shaped, not provider-shaped.

Good:

- `Classifier(worker=...)`
- `Contextualizer(worker=...)`

Bad:

- `OpenAIClassifier`
- `AnthropicContextualizer`

Provider choice is an injected implementation detail, not the identity of the task.

### 4. Providers Own Transport Only

Provider adapters in [providers](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/providers) should only own transport/API behavior.

They may own:

- request formatting
- response parsing
- tool-call transport semantics
- retries/timeouts specific to the provider

They should not own:

- router state changes
- memory commits
- project/session scoping
- policy decisions
- retrieval decisions

Providers are adapters, not orchestrators.

### 5. Persistence Layers Stay Persistence-Only

Persistence modules in [persistence](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/persistence) should store, query, merge, and return data.

They should not decide:

- what should be remembered
- which memory wins policy conflicts
- when a lifecycle phase should run
- how the router should behave

Good:

- load snapshot
- search attempts
- append messages
- resolve session context

Bad:

- deciding whether a memory candidate should be committed
- silently changing workflow behavior

Policy belongs in the router and task agents. Storage belongs in persistence.

### 6. Retrieval Must Be Observable and Bounded

Retrieval lives in [retrieval](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/retrieval).

The system is intentionally designed so retrieval decisions are inspectable.

Key rules:

- local RAG remains primary when in-domain context is likely
- web search is fallback behavior, not default behavior
- classifier output is a search suggestion, not a hard prohibition
- control labels like `no_rag` affect router prefetch, not tool-level retrieval rights

Retrieval should remain:

- traceable
- scoped
- replaceable at the provider/model layer

The router decides when retrieval matters. Retrieval components execute that decision.

### 7. Tool Use Must Stay Visible

If a model can call a tool, that tool use must be visible through events, traces, or explicit returned debug data.

Avoid silent helper behavior that looks like model intelligence but cannot be inspected later.

This applies to:

- RAG search
- web search
- system-profile lookup
- conversation-history search

The goal is not only capability. The goal is auditable capability.

### 8. Scope Is Backend-Enforced

The model does not choose where memory or chat state lives.

Scope is derived from backend context:

- `user`
- `project`
- `chat_session`

Current application rule:

- `chat_session.project_id` is the memory boundary

That means:

- each project owns its own memory
- each chat session belongs to one project
- all memory reads/writes for that chat are scoped to that project
- remembered project environment is the default operating context for answers in that chat unless the user clearly changes target scope

This prevents:

- cross-project contamination
- model-chosen persistence scope
- hidden data leakage between devices/stacks
- technically correct but project-incompatible recommendations caused by ignoring remembered environment context

### 9. The Backend Should Answer Product Questions Cleanly

A strong backend should make product debugging straightforward.

At minimum, the architecture should let us answer:

- Why did this answer use RAG?
- Why did it skip RAG?
- Which project was active?
- Which messages were loaded?
- Which tool calls happened?
- What memory was extracted?
- What memory was committed?
- What provider handled the generation?

If a new feature makes those questions harder to answer, it is probably architecturally wrong.

## Current Package Responsibilities

### Orchestration

[orchestration](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration)

Owns:

- lifecycle control
- turn state
- routing domain logic
- session bootstrap
- history preparation
- run cancellation checkpoints consumed by router-owned phases
- structured success/failure outcomes consumed by workers without parsing assistant text

Current router turn phases remain explicit and inspectable:

- `START`
- `LOAD_MEMORY`
- `SUMMARIZE_CONVERSATION_HISTORY`
- `CLASSIFY`
- `DECIDE_RAG`
- `REWRITE_QUERY`
- `RETRIEVE_CONTEXT`
- `GENERATE_RESPONSE`
- `SUMMARIZE_RETRIEVED_DOCS`
- `UPDATE_HISTORY`
- `DECIDE_MEMORY`
- `EXTRACT_MEMORY`
- `RESOLVE_MEMORY`
- `COMMIT_MEMORY`
- `AUTO_NAME`
- `DONE` / `ERROR`

`AUTO_NAME` runs only after the first completed turn in a chat. It stays router-owned, uses the cheap `chat_namer` role settings (default `gpt-5.4-nano`), and persists the generated title through the chat store instead of hiding naming inside persistence or the API layer.

For non-streamed turns, `AUTO_NAME` runs inline after memory work.

For streamed durable runs, the message run emits an explicit `auto_name_scheduled` event and then terminalizes normally. The durable run layer then queues a second internal `auto_name` run, and that follow-up run executes the router's explicit `AUTO_NAME -> DONE` path against the persisted opening exchange. This keeps title generation out of the visible answer stream without moving workflow ownership into the worker.

### Durable Run Execution

[chat_run_worker.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/chat_run_worker.py)

Owns:

- execution placement outside the web process
- claiming eligible runs
- lease heartbeats
- lease-owned durable writes
- stale-run recovery/failure handling
- worker-local runtime reuse boundaries

Important:

- per-worker heavyweight retrieval/runtime components may be reused
- per-run router instances, listeners, and mutable turn state must stay isolated

### Agents

[agents](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents)

Owns:

- task-specific reasoning units
- memory extraction/resolution policy
- contextualization
- response generation behavior

### Magi System (Multi-Agent Deliberation)

[agents/magi](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents/magi)

The Magi system is an alternative response mode toggled per-turn via the `magi` flag on `ask_question()` / the API.

Current modes:

- `off`: normal single-responder flow
- `lite`: smaller-model council with fewer discussion rounds
- `full`: full council with the main Magi role settings

Instead of a single responder, Magi runs a bounded deliberation protocol with four roles:

| Role | Purpose |
|---|---|
| **Eager** | Hypothesis generator — proposes most likely explanation and next step |
| **Skeptic** | Validator — challenges assumptions, identifies contradictions and missing evidence |
| **Historian** | Ground truth — retrieves project memory, prior actions, documentation |
| **Arbiter** | Synthesizer — reads the full deliberation transcript, produces the final user-facing response |

Role models are configured through `AppSettings`; `full` and `lite` use different role defaults. All roles have the same tool surface as the standard responder (RAG search, memory tools when available, history search, and provider-native web search when the worker supports it).

**Protocol:**

1. **Opening Arguments** — Eager → Skeptic → Historian each produce a structured position (JSON with `position`, `confidence`, `key_claims`)
2. **Discussion** (bounded, up to `magi_max_discussion_rounds` or `magi_lite_max_discussion_rounds`) — Each role responds only if adding new information. Early-stops when no role contributes new info.
3. **Closing Arguments** — Eager → Skeptic → Historian produce a final position after discussion.
4. **Arbiter Synthesis** — Reads the full transcript + original context and produces the final user-facing response.

**FSM:** `MagiState` is explicit and traceable. The router appends `MAGI_`-prefixed trace markers via `_handle_magi_state`. Current states include `OPENING_ARGUMENTS`, `ROLE_EAGER`, `ROLE_SKEPTIC`, `ROLE_HISTORIAN`, `DISCUSSION`, `DISCUSSION_EAGER`, `DISCUSSION_SKEPTIC`, `DISCUSSION_HISTORIAN`, `CLOSING_ARGUMENTS`, `CLOSING_EAGER`, `CLOSING_SKEPTIC`, `CLOSING_HISTORIAN`, `ARBITER`, `COMPLETE`, `ERROR`.

**Streaming:** Each role emits lifecycle events (`magi_phase`, `magi_role_start`, `magi_role_complete`) and live text deltas (`magi_role_text_delta`) so the frontend can render the council in real time. The Arbiter's final response streams through the normal text-delta channel.

**Router integration:** The `MagiSystem` is lazily constructed on first Magi turn and cached. It plugs into `_generate_response` as an alternative to `self.responder` — no new router states needed.

**Settings:** `magi_eager`, `magi_skeptic`, `magi_historian`, `magi_arbiter`, `magi_max_discussion_rounds`, plus the matching `magi_lite_*` role settings and `magi_lite_max_discussion_rounds`. Override via env vars such as `MAGI_EAGER_PROVIDER`, `MAGI_EAGER_MODEL`, `MAGI_LITE_EAGER_PROVIDER`, and `MAGI_LITE_MAX_DISCUSSION_ROUNDS`.

### Providers

[providers](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/providers)

Owns:

- provider API transport
- provider-specific tool-call mechanics

### Retrieval

[retrieval](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/retrieval)

Owns:

- vector search
- reranking
- source filtering
- retrieval provider abstraction

### Ingestion

[ingestion](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/ingestion)

Owns:

- document-ingest orchestration
- ingest state transitions
- queue processing behavior
- artifact lifecycle during ingest
- reusable ingest stage implementations

The same rule applies here as elsewhere:

- orchestration belongs in `app`
- scripts should stay thin wrappers

Suggested split inside ingestion:

- `pipeline.py`: orchestration and state machine
- `stages/`: stage-specific implementations such as PDF intake, cleaning, and enrichment

Current ingestion lifecycle:

- `INITIALIZED`
- `VALIDATE_INPUT`
- `INTAKE_RAW`
- `EXPORT_TEXT`
- `UPDATE_REGISTRY`
- `CLEAN_ELEMENTS`
- `ENRICH_CONTEXT`
- `FINALIZE_OUTPUT`
- `INGEST_VECTOR_DB`
- `CLEANUP_ARTIFACTS`
- `COMPLETED`

In queue mode, this lifecycle repeats once per document. That is intentional. Folder ingest is modeled as repeated traversal of the same document FSM, not as a separate hidden code path.

Directory ingest behavior:

- create `to_ingest/` and `ingested/` under the queue root
- process PDFs from `to_ingest/` one by one
- move successful PDFs into `ingested/`
- clean intermediate artifacts only after successful vector ingest

Observability rules for ingestion:

- console output should make queue position, active document, and active ingest state obvious
- each run should emit a persisted JSON trace for later inspection
- stage implementations may report local progress, but the pipeline owns lifecycle visibility

Current ingestion trace behavior:

- traces are written under `Back-end/ingest_traces/` by default
- each run records mode, target path, config snapshot, completed/failed counts, and one document entry per processed PDF
- each document entry records state trace, timing, element counts, artifacts, archive path, and any failure error

### Persistence

[persistence](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/persistence)

Owns:

- Postgres models
- app/session/project storage
- memory persistence
- DB connectivity

### Prompting

[prompting](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/prompting)

Owns:

- prompt text and policy language
- rules for how remembered project-scoped environment context should constrain responder behavior
- instructions that remembered environment facts should gate generic technical recommendations unless the user clearly changes target scope

Memory-specific design and operating rules are documented in [MEMORY.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/MEMORY.md).

### Config

[config](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/config)

Owns:

- defaults
- environment-driven runtime config
- role-specific worker/model settings including `chat_namer`

### API / UI Entry Points

Top-level backend entry points:

- [api.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/api.py)
- [main.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/main.py)
- [AI_Generated_TUI.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/AI_Generated_TUI.py)

These should stay thin.

They should wire application flow into the router, not duplicate backend policy.

Script entry points should follow the same rule.

Example:

- [scripts/ingest/ingest_pipeline.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/scripts/ingest/ingest_pipeline.py)

should stay a CLI wrapper around:

- [app/ingestion/pipeline.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/ingestion/pipeline.py)

## What To Prefer

Prefer:

- explicit state transitions
- task boundaries
- injected workers/providers
- backend-owned scope
- traceable tool use
- storage layers that stay dumb and reliable

## What To Avoid

Avoid:

- hidden side effects
- provider-specific business logic
- memory policy inside storage layers
- retrieval policy buried inside adapters
- model-controlled persistence scope
- convenience helpers that bypass router visibility

## Architectural Test

Before adding a feature, ask:

1. Which component owns this behavior?
2. Is the phase visible?
3. Does this preserve task/provider separation?
4. Does this preserve backend-enforced scope?
5. Will debugging this later be easier or harder?

If the answers are unclear, the design is probably not clean enough yet.
