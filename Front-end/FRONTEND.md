# Frontend

This document explains the current frontend structure and its ownership boundaries.

## Purpose

The frontend is a React + TypeScript client for:

- login
- project selection/creation/edit/delete
- chat selection/creation/edit/delete
- message history display
- streaming chat UI
- Magi council mode toggling and deliberation display
- live backend status rendering

It is intentionally thin relative to the backend.

The frontend should render backend truth, not recreate backend policy.

## Main Files

### `src/App.tsx`

This is now the composition root.

It currently owns:

- top-level composition of hooks and components
- shared error/status state
- sidebar shell state
- debug drawer toggle state

It should stay thin and should not absorb subsystem logic again.

### `src/hooks/`

Owns stateful frontend behavior, split by responsibility:

- `useAuth.ts`
  - bootstrap/login state
- `useProjects.ts`
  - project CRUD, selection, dialogs, form state
- `useChats.ts`
  - chat CRUD, selection, per-project chat caching
- `useMessages.ts`
  - per-chat message caching and composer text
- `useTextDeltaAnimation.ts`
  - paced `text_delta` draining into optimistic assistant text
- `useCouncilStreaming.ts`
  - council panel state, live role-delta batching, and deferred role completion until queued council text is visible
- `useStreamingRun.ts`
  - durable run attach/reconnect/cancel lifecycle and optimistic run UI state
- `useScrollManager.ts`
  - chat auto-scroll and stick-to-bottom behavior

These hooks are the main stateful frontend surfaces.

### `src/components/`

Owns presentational UI surfaces:

- `LoginScreen.tsx`
- `Sidebar.tsx`
- `ChatView.tsx`
- `MessageComposer.tsx`
- `CouncilPanel.tsx`
- `dialogs/`
  - project/chat dialog shells

### `src/types.ts`

Owns shared frontend types:

- user/project/chat/run API shapes
- streaming event types
- shared UI run state and optimistic batch types
- council entry shapes for persisted data and live UI rendering

### `src/utils.ts`

Owns pure frontend helpers:

- timestamp formatting
- council phase display formatting
- streaming preview text extraction
- optimistic run-id derivation
- text delta pacing constants

### `src/councilStreamLifecycle.ts`

Owns the pure council-stream ordering helpers used to avoid `magi_role_complete` replacing text that is still queued for rendering.

This file exists so the council completion ordering can be regression-tested without a browser runtime.

### `src/api.ts`

Owns API access:

- blocking JSON requests
- durable run creation
- run snapshot/cancel requests
- run-event streaming and replay attach
- SSE parsing
- startup bootstrap calls

The frontend does not call the router directly. It only talks to FastAPI.

### `src/runStreamSession.ts`

Owns the shared run-stream reconnect/backfill seam used by both the main chat UI and the debug inspector.

It keeps:

- reconnect-after-disconnect behavior
- replay of missed events before resuming live updates
- terminal-event stopping rules

### `src/renderMessage.tsx`

Owns message-content rendering, including markdown-like assistant and council formatting for completed text blocks.

### `src/streamStatusText.ts`

Owns mapping backend state/event codes into human-facing labels and aliases.

This is where product voice for streaming statuses belongs.

### `src/styles.css`

Owns the current app layout and visual treatment.

### `src/debug/`

Owns the dev/admin debug drawer:

- chat-scoped run history
- per-run snapshot and event inspection
- timing calculations
- live SSE attach/reconnect for active runs
- client-side event tab filtering
- grouped execution detail for responder, Magi, provider, tool, retrieval, memory, and naming events beneath top-level router states

## Application Model

The frontend mirrors the backend product model:

- user
- project
- chat session
- messages
- chat run

Important:

- project is the scope container
- chat is the thread inside the project
- project memory is backend-owned and implicit

The frontend should not attempt to manage project memory directly.

## Streaming Behavior

When a message is sent:

1. The frontend creates or reuses a durable run with a `client_request_id`.
2. It stores optimistic state keyed by `chatId`, not in one global in-flight slot.
3. It attaches to that run’s SSE event stream.
4. Backend states/events update the visible live status.
5. If Magi is enabled, council role events populate the live council panel, and live role deltas are batched before React renders them.
6. During active streaming, rapid `text_delta` events are batched and then drained into the optimistic assistant text at a paced `requestAnimationFrame` cadence.
7. During active Magi streaming, visible council text emitted from the parsed role output is drained with the same paced `requestAnimationFrame` model instead of reparsing partial JSON in the browser.
7. `text_checkpoint` events are tracked for reconnect seeding and are only applied to visible text while replaying after a reconnect.
8. When `done` arrives, the frontend lets any queued visible text finish draining before replacing the optimistic pair with the final persisted backend messages.
9. When `magi_role_complete` arrives, the frontend also waits for any queued council delta batch to drain before finalizing that council entry, and only uses the completion payload to catch up a missing suffix that the live council stream never rendered.

For live assistant rendering:

- live assistant text continues rendering through the normal message formatter while `text_delta` drains
- if the backend schedules a first-turn auto-name follow-up run, the frontend performs a few delayed chat-list refreshes after `done` so the sidebar picks up the final title without keeping the main response run open
- the frontend receives the final title as normal chat-list data; any title reveal polish remains frontend-owned and does not require backend token streaming for the title itself

For live council rendering:

- the backend sends `magi_role_text_delta` as visible role text, not raw partial JSON
- live council entries append only `magi_role_text_delta` while active, matching the assistant text path
- `magi_role_text_checkpoint` is a reconnect seed, not a live replacement
- live council entries render directly from the run UI state when not viewing a past assistant message
- stored `councilEntries` state is only for past deliberation replay, not for duplicating the live council stream

This avoids the earlier end-of-stream flash and keeps the backend as the source of truth.

When the user switches away from a running chat:

- the run continues server-side
- the visible SSE attachment may be dropped
- reopening the chat seeds from the latest durable checkpoint and then resumes live deltas
- reopening the chat resumes from the highest durable sequence the client already saw, so reconnect does not replay already-consumed durable events
- if a detached run finishes before the chat is reopened, stale optimistic run UI is discarded and the chat reloads persisted messages instead of staying stuck on the optimistic pair
- the shared stream session client keeps chat and debug reconnect behavior aligned without moving policy into React

## Ownership Rules

### Frontend owns

- human-readable status labels
- temporary optimistic rendering
- per-chat attach/reconnect UX
- council UI rendering for live/persisted deliberation
- dev-only debug inspection UX
- layout/state for sidebar/dialogs
- local UX polish

### Backend owns

- session/project/chat truth
- persistence
- routing and memory policy
- retrieval policy
- tool/retrieval event generation
- final assistant text

## Current Limitations

1. Cross-hook coordination still exists for streaming/council integration and should stay explicit rather than hidden in ad hoc shared state.
2. The frontend is intentionally not a general-purpose state machine; the backend remains the real control plane.
3. Hidden chats rely on run snapshot state rather than live SSE until reopened.
4. `App.tsx` is intentionally thin, but it still orchestrates several hooks and remains the place where cross-surface wiring is easiest to audit.
5. Chat and debug streaming now share one reconnect/backfill seam, but their UI state stays intentionally separate.
6. The debug drawer is intentionally operator-focused rather than a polished end-user surface.
7. Composer input stays editable while a run is active, but sending remains disabled until the active run ends or is cancelled.
8. Live council streaming remains plain-text while incomplete so partial markdown markers do not flicker during delta rendering.
9. The current UI is usable, but still product-iteration code rather than a finished design system.

## Safe Change Guidelines

If you modify the frontend, preserve these invariants:

1. Frontend talks only to FastAPI.
2. Backend remains the source of truth for persisted messages.
3. Human status labels remain frontend-owned.
4. The debug drawer should reuse the durable run APIs rather than invent a parallel debug channel.
5. Do not reimplement backend routing or memory logic in React.

## Files To Read First

1. [src/App.tsx](src/App.tsx)
2. [src/hooks/useStreamingRun.ts](src/hooks/useStreamingRun.ts)
3. [src/hooks/useCouncilStreaming.ts](src/hooks/useCouncilStreaming.ts)
4. [src/hooks/useTextDeltaAnimation.ts](src/hooks/useTextDeltaAnimation.ts)
5. [src/api.ts](src/api.ts)
6. [src/runStreamSession.ts](src/runStreamSession.ts)
7. [src/types.ts](src/types.ts)
8. [src/utils.ts](src/utils.ts)
9. [src/components/Sidebar.tsx](src/components/Sidebar.tsx)
10. [src/components/ChatView.tsx](src/components/ChatView.tsx)
11. [src/debug/DebugPanel.tsx](src/debug/DebugPanel.tsx)
