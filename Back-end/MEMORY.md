# Memory Subsystem

This document explains how memory works in the backend, what it is allowed to do, and where the relevant code lives.

The goal is to make the subsystem legible to another coding agent without requiring it to reconstruct the design from the router, stores, and prompts.

## Purpose

The memory subsystem exists to preserve durable, project-scoped operating context across chats in the same project.

It is for things like:

- environment facts
- active issues
- previously attempted fixes
- important constraints
- important preferences

It is not meant to be a generic personal diary or broad conversational memory layer.

The current design is intentionally biased toward technical troubleshooting and project environment continuity.

## Core Rules

1. Memory is project-scoped.
   - The boundary is `chat_session.project_id`.
   - Every chat in the same project reads and writes the same memory.
   - Different projects must not contaminate each other.

2. The backend owns memory scope.
   - The model does not choose where memory lives.
   - Session bootstrap and router wiring determine the active project and memory store.

3. Memory is explicit in the router.
   - The memory pipeline is not hidden in providers or storage helpers.
   - It runs through visible router states.

4. Storage does not decide policy.
   - Stores query and persist memory.
   - Extraction and commit policy live in separate components.

5. Remembered environment context should constrain answers.
   - Project memory is not just passive recap material.
   - The responder should treat remembered environment facts as the default operating context for the current chat unless the user clearly changes target scope.

## High-Level Flow

Per user turn, the memory path is:

1. Load relevant project memory.
2. Use that memory while answering.
3. Extract candidate memory from the turn.
4. Resolve candidate memory against existing committed memory.
5. Commit accepted updates back to the project store.

In router terms, that is:

- `LOAD_MEMORY`
- `GENERATE_RESPONSE`
- `EXTRACT_MEMORY`
- `RESOLVE_MEMORY`
- `COMMIT_MEMORY`

These states are orchestrated in [model_router.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration/model_router.py).

## Main Components

### Router

[model_router.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration/model_router.py)

Owns:

- when memory is loaded
- what memory snapshot is attached to the turn
- when extraction runs
- when resolution runs
- when commit runs
- memory-related emitted events

The router is the control plane. It should remain the place where another engineer can answer:

- what memory was loaded for this turn
- whether extraction happened
- whether anything was committed
- why memory was skipped or failed

### Memory Extractor

[memory_extractor.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents/memory_extractor.py)

Owns:

- turning the latest turn into structured candidate memory
- normalizing model output into backend-safe shapes
- rejecting malformed or low-signal extraction outputs structurally

Inputs:

- `user_question`
- `assistant_response`
- `recent_history`

Important note:

The extractor intentionally receives a short recent conversation window so it can resolve references like:

- "that"
- "do that"
- "I don't want to do that"

Without recent history, the extractor can store contextless memory that is technically valid but semantically incomplete.

### Memory Resolver

[memory_resolver.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents/memory_resolver.py)

Owns commit policy.

It decides:

- what can be committed directly
- what should remain a candidate
- what conflicts with existing memory
- when a user-provided mutable fact supersedes an older value

This is where confidence thresholds and user-source requirements live.

Examples of current policy:

- many facts require `source_type == "user"`
- preferences and constraints generally require user source
- mutable environment facts can replace older committed values when user-sourced and confident enough
- conflicting facts are surfaced as conflicts instead of silently overwriting

### Memory Stores

[postgres_memory_store.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/persistence/postgres_memory_store.py)

[in_memory_memory_store.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/persistence/in_memory_memory_store.py)

Own:

- querying committed memory
- formatting relevant memory snapshots for prompts
- persisting committed facts/issues/attempts/constraints/preferences
- storing memory candidates and project state

They should not own extraction policy or commit policy.

### Shared Memory Formatting / Helpers

[memory_common.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/persistence/memory_common.py)

Owns:

- `MemorySnapshot`
- prompt text rendering for snapshots
- fact label formatting
- shared normalization and relevance helpers

This is where project memory becomes prompt-facing text such as:

- `KNOWN SYSTEM PROFILE`
- `KNOWN ISSUES`
- `PREVIOUS ATTEMPTS`
- `KNOWN CONSTRAINTS`
- `KNOWN PREFERENCES`

## What Gets Stored

The structured memory schema currently includes:

- facts
- issues
- attempts
- constraints
- preferences
- session summary

### Facts

Durable environment or system facts, for example:

- OS / distro
- virtualization platform
- container runtime
- hardware facts
- package manager

These are the most important category for project context.

### Issues

Ongoing or resolved problems associated with the project.

### Attempts

Actions already tried, commands already run, and outcomes.

This is important for avoiding repeated dead-end suggestions.

### Constraints

Things the assistant should avoid or respect, for example:

- "I do not want to reinstall"
- "This is a Proxmox host"
- "I cannot reboot right now"

### Preferences

Stable user or project preferences that meaningfully shape future advice.

## Prompting

Memory-aware responder behavior is defined in [prompts.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/prompting/prompts.py).

Important current behavior:

- `KNOWN_SYSTEM_MEMORY` is injected into the responder request
- the responder is told to use memory/history for environment and prior-attempt context
- remembered project environment should be treated as the default operating context for the chat unless the user clearly changes scope
- generic advice that conflicts with remembered project context should be avoided

This matters because technically correct advice can still be wrong for the active project.

Example:

- suggesting ordinary Debian host steps when the remembered project context says the machine is actually a Proxmox host

## Retrieval vs Memory

These are different responsibilities.

Memory is for:

- project state
- environment continuity
- previously attempted steps
- constraints and preferences

Retrieval is for:

- exact commands
- exact syntax
- doc-backed procedures
- source-grounded technical detail

The responder should use memory to understand the situation, and retrieval/tools to ground exact technical steps.

## Tool Visibility

Memory use should stay observable.

The router already emits memory-related events such as:

- memory loaded
- memory extracted
- memory resolved
- memory committed
- memory error

Do not bury memory changes inside silent helper logic.

## Data Ownership

App/store ownership is:

- app store: users, projects, chat sessions, messages
- memory store: project memory and memory candidates

That separation should remain intact.

The app store should not absorb extraction or memory policy.
The memory store should not absorb responder or router policy.

## Known Limitations

Current limitations worth knowing before changing the subsystem:

1. The system is still optimized for technical/project memory, not broad personal memory.
2. Project-scoped memory is powerful, but it makes prompt policy important; if the responder ignores remembered environment context, answers can be technically correct but operationally wrong.
3. Candidate/conflict handling exists, but the operator UX around reviewing candidates is still minimal.
4. The system does not yet have a separate "general conversation memory mode" versus "technical project memory mode".

## Safe Change Guidelines

If you modify this subsystem, preserve these invariants:

1. Memory scope remains `project_id`-bound.
2. Router states remain explicit.
3. Stores stay persistence/query-only.
4. Resolver stays the owner of commit/conflict policy.
5. Responder prompts continue to treat remembered project environment as meaningful context, not decorative text.
6. Memory extraction should keep enough recent history to resolve short referential replies safely.
7. Memory extraction runs on every turn when a memory store is present — do not add label-based or content-based skip conditions. A turn labeled `no_rag` (e.g., greetings, small talk) may still contain extractable facts such as OS, hardware, or constraints. The extractor decides what is worth keeping; the router does not pre-filter.

## Files To Read First

If you are a new agent touching memory, start here:

1. [model_router.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration/model_router.py)
2. [memory_extractor.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents/memory_extractor.py)
3. [memory_resolver.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents/memory_resolver.py)
4. [postgres_memory_store.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/persistence/postgres_memory_store.py)
5. [memory_common.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/persistence/memory_common.py)
6. [prompts.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/prompting/prompts.py)

That is the minimum set needed to make safe architectural changes.
