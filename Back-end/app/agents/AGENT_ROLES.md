# Agent Roles and Reasoning Units

This document defines the roles, responsibilities, and deliberation protocols for the agents in the `Back-end/app/agents` package.

## Core Principle: Agents Are Task-Shaped

Agents represent distinct backend jobs, not specific model providers. The identity of an agent is defined by its task and reasoning policy, while the underlying model is an injected implementation detail.

- **Good:** `Classifier(worker=...)`, `Contextualizer(worker=...)`
- **Bad:** `OpenAIClassifier`, `AnthropicContextualizer`

## Specialized Agents

The system employs several specialized agents to handle specific phases of the router's lifecycle:

- **Classifier**: Determines the user's intent, identifies relevant domains, and suggests retrieval strategies (e.g., `no_rag`).
- **Contextualizer**: Prepares the historical context and retrieved documents for the response phase.
- **Responder**: The primary agent responsible for generating the final answer in non-Magi turns.
- **Memory Extractor**: Identifies new project-scoped facts or environment details from the conversation.
- **Memory Resolver**: Merges extracted candidates with existing project memory, resolving conflicts and updating stale information.
- **Summarizers**: Create concise representations of conversation history or retrieved documents to maintain context efficiency.

## Magi System (Multi-Agent Deliberation)

The Magi system is an alternative response mode that uses a council of models to deliberate on complex issues. It is toggled via the `magi` flag in the API.

### Magi Roles

| Role | Responsibility |
|---|---|
| **Eager** | Hypothesis generator — proposes the most likely explanation and immediate next steps. |
| **Skeptic** | Validator — challenges assumptions, identifies contradictions, and highlights missing evidence. |
| **Historian** | Ground truth — retrieves and reports on project memory, prior actions, and documentation. |
| **Arbiter** | Synthesizer — reads the full deliberation transcript and produces the final user-facing response. |

### Deliberation Protocol

1. **Opening Arguments**: Eager, Skeptic, and Historian each produce a role-shaped structured position.
2. **Discussion Gate**: The system evaluates if discussion is required based on alignment and grounding strength. Weak or conflicted grounding forces a discussion.
3. **Discussion**: Bounded rounds where roles contribute delta-value updates against an `unresolved_issue`.
4. **Closing Arguments**: Roles provide concise stance updates after the discussion.
5. **Arbiter Synthesis**: The Arbiter produces the final response along with internal synthesis metadata (e.g., `winning_branch`, `uncertainty_level`).

## Evidence Pool Integration (MAGI Turns)

On MAGI turns (`magi="full"` or `magi="lite"`), the router's per-turn `EvidencePool` injects a short `EVIDENCE POOL SUMMARY` block into each role's opening-argument prompt and each discussion-round prompt.

The summary includes:

- covered region keys (what evidence areas have already been retrieved this turn)
- the latest retrieval outcome classification (`delivered_new_evidence`, `cache_hit`, `reused_known_evidence`, `no_new_evidence`, `search_exhausted_for_scope`)
- any unresolved evidence gap from the prior query
- exhausted scope keys (scopes where the pool has blocked further retrieval)

An accompanying `MAGI_NET_NEW_INSTRUCTION` is also injected to guide tool use:

> When using tools: prefer net-new evidence regions not yet covered this run. Revisit covered regions only for contradiction checks, alternate-source confirmation, or explicit gap expansion.

Roles that attempt retrieval on an exhausted scope receive a `retrieval_gated` event and an empty result, rather than a real DB call. Roles can bypass gating by supplying a recognized `repeat_reason` (`contradiction_check`, `alternate_source_confirmation`, `gap_expansion`, `explicit_gap`).

## Traceability

All agent actions, especially Magi deliberation phases, must emit explicit events and traces. The goal is to make the "why" behind every answer auditable and transparent.
