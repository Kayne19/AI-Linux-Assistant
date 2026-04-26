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

## Regular Responder Tool Surface

The normal responder runs a single provider tool loop — same shape every Magi role uses.

- tools available every turn: `search_rag_database`, `search_conversation_history`, native `web_search`, and the optional memory-store tools when a memory store is wired up
- the model picks: RAG for project-local questions, `web_search` for anything outside the corpus, answer when it has enough
- `search_rag_database` accepts an optional `progress_assessment` field on 2nd-or-later calls in the same turn — the model uses it to evaluate the prior search; the router forwards it to `EvidencePool.apply_qualitative_evaluation` before running the new retrieval
- `evidence_gap`, `gap_type`, and `repeat_reason` remain on the schema as optional structured hints used for evidence-pool memo and prompt-side scope tracking, not as gates
- when the unresolved gap is mainly about the user's environment, the responder should prefer 1 to 3 tightly focused follow-up questions instead of speculative extra retrieval

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
- the latest usefulness classification (`high`, `medium`, `low`, `zero`)
- the active `evidence_gap`, when one is in play
- any unresolved evidence gap from the prior query
- soft exhausted scope keys (scopes where repeat retrieval should explain or refine the evidence path)
- hard exhausted scope keys (scopes where local RAG is strongly signaling that web fallback or follow-up may be better)

An accompanying `MAGI_NET_NEW_INSTRUCTION` is also injected to guide tool use:

> When using tools: prefer net-new evidence regions not yet covered this run. Revisit covered regions only for contradiction checks, alternate-source confirmation, or explicit gap expansion.

Roles that attempt retrieval on an exhausted scope receive a router-owned `retrieval_signal` event, but the DB call still runs within the shared tool budget. Recognized `repeat_reason` values are:

- `contradiction_check`
- `alternate_source_confirmation`
- `expand_beyond_covered_region`
- `fill_named_unresolved_gap`

Current rule:

- the evidence pool is a memo, not a gate — it records queries, scopes, usefulness, covered regions, and exclusions; it never blocks or rejects a tool call
- both the regular responder and every Magi role except Arbiter share the same provider tool loop and the same `_handle_responder_tool_call`; soft/hard exhaustion now manifests only through the `EVIDENCE POOL SUMMARY` block injected into the next prompt
- on a 2nd-or-later `search_rag_database` call in the same turn, the model passes `progress_assessment` describing the prior search; the handler forwards it to `EvidencePool.apply_qualitative_evaluation` before running the new retrieval

Current Historian web rule:

- Historian (and Eager / Skeptic) gets native `web_search` on every opening and discussion round; closing stays toolless
- prompts steer RAG-first for project-local material; the model picks `web_search` for anything outside the corpus

## Traceability

All agent actions, especially Magi deliberation phases, must emit explicit events and traces. The goal is to make the "why" behind every answer auditable and transparent.
