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

This is the main application surface.

It currently owns:

- login state
- bootstrap loading from the backend
- project list state
- selected project/chat state
- chat list caching by project
- per-chat message caching
- per-chat run/reconnect state
- optimistic streaming message behavior
- council panel state and live council-entry rendering
- sidebar state and edit dialogs
- debug drawer toggle state

This file is the main stateful UI container.

### `src/api.ts`

Owns API access:

- blocking JSON requests
- durable run creation
- run snapshot/cancel requests
- run-event streaming and replay attach
- SSE parsing
- startup bootstrap calls

The frontend does not call the router directly. It only talks to FastAPI.

### `src/renderMessage.tsx`

Owns message-content rendering, including markdown-like assistant output formatting.

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
7. `text_checkpoint` events are tracked for reconnect seeding and are only applied to visible text while replaying after a reconnect.
8. When `done` arrives, the optimistic pair is replaced by the final persisted backend messages.

This avoids the earlier end-of-stream flash and keeps the backend as the source of truth.

When the user switches away from a running chat:

- the run continues server-side
- the visible SSE attachment may be dropped
- reopening the chat seeds from the latest durable checkpoint and then resumes live deltas

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

1. `src/App.tsx` is still large and could eventually be split.
2. The frontend is intentionally not a general-purpose state machine; the backend remains the real control plane.
3. Council rendering lives in `App.tsx` today rather than in isolated components.
4. Hidden chats rely on run snapshot state rather than live SSE until reopened.
5. The debug drawer is intentionally operator-focused rather than a polished end-user surface.
6. The current UI is usable, but still product-iteration code rather than a finished design system.

## Safe Change Guidelines

If you modify the frontend, preserve these invariants:

1. Frontend talks only to FastAPI.
2. Backend remains the source of truth for persisted messages.
3. Human status labels remain frontend-owned.
4. The debug drawer should reuse the durable run APIs rather than invent a parallel debug channel.
5. Do not reimplement backend routing or memory logic in React.

## Files To Read First

1. [src/App.tsx](src/App.tsx)
2. [src/api.ts](src/api.ts)
3. [src/types.ts](src/types.ts)
4. [src/streamStatusText.ts](src/streamStatusText.ts)
5. [src/renderMessage.tsx](src/renderMessage.tsx)
6. [src/styles.css](src/styles.css)
7. [src/debug/DebugPanel.tsx](src/debug/DebugPanel.tsx)
