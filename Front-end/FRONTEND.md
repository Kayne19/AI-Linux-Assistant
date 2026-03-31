# Frontend

This document explains the current frontend structure and its ownership boundaries.

## Purpose

The frontend is a React + TypeScript client for:

- login
- project selection/creation/edit/delete
- chat selection/creation/edit/delete
- message history display
- streaming chat UI
- live backend status rendering

It is intentionally thin relative to the backend.

The frontend should render backend truth, not recreate backend policy.

## Main Files

### `src/App.tsx`

This is the main application surface.

It currently owns:

- login state
- project list state
- selected project/chat state
- chat list caching by project
- message list rendering
- optimistic streaming message behavior
- sidebar state and edit dialogs

This file is the main stateful UI container.

### `src/api.ts`

Owns API access:

- blocking JSON requests
- streaming chat request handling
- SSE parsing

The frontend does not call the router directly. It only talks to FastAPI.

### `src/renderMessage.tsx`

Owns message-content rendering, including markdown-like assistant output formatting.

### `src/streamStatusText.ts`

Owns mapping backend state/event codes into human-facing labels and aliases.

This is where product voice for streaming statuses belongs.

### `src/styles.css`

Owns the current app layout and visual treatment.

## Application Model

The frontend mirrors the backend product model:

- user
- project
- chat session
- messages

Important:

- project is the scope container
- chat is the thread inside the project
- project memory is backend-owned and implicit

The frontend should not attempt to manage project memory directly.

## Streaming Behavior

When a message is sent:

1. The frontend creates optimistic temporary user and assistant messages.
2. It opens the streaming API request.
3. Backend states/events update the visible live status.
4. Text deltas append into the optimistic assistant message.
5. When `done` arrives, the optimistic pair is replaced by the final persisted backend messages.

This avoids the earlier end-of-stream flash and keeps the backend as the source of truth.

## Ownership Rules

### Frontend owns

- human-readable status labels
- temporary optimistic rendering
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
3. The current UI is usable, but still product-iteration code rather than a finished design system.

## Safe Change Guidelines

If you modify the frontend, preserve these invariants:

1. Frontend talks only to FastAPI.
2. Backend remains the source of truth for persisted messages.
3. Human status labels remain frontend-owned.
4. Do not reimplement backend routing or memory logic in React.

## Files To Read First

1. [src/App.tsx](src/App.tsx)
2. [src/api.ts](src/api.ts)
3. [src/types.ts](src/types.ts)
4. [src/streamStatusText.ts](src/streamStatusText.ts)
5. [src/renderMessage.tsx](src/renderMessage.tsx)
6. [src/styles.css](src/styles.css)
