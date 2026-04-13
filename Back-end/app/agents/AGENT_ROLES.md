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

## Regular Responder Mini-Protocol

The normal responder now borrows MAGI's search discipline without becoming MAGI.

- the router owns a lightweight `DECIDE_NEXT_STEP -> SEARCH -> EVALUATE_TOOL_RESULT -> FINALIZE_RESPONSE` mini-protocol for non-MAGI turns
- before any regular-responder retrieval, the model must choose `answer_now`, `ask_focused_follow_up_questions`, or `search`
- search decisions must include explicit justification for the router, including `requested_evidence_goal`, `unresolved_gap`, and why the current evidence is insufficient
- `gap_type` may optionally identify a `procedural_doc_gap`, `environment_fact_gap`, or `confirmation_gap`
- after each responder search, the model must evaluate what new evidence was added, what gap was reduced if any, and whether another search is still justified
- the router and evidence pool remain the authority on whether another search is allowed; the evaluation step is advisory for reasoning clarity
- when the unresolved gap is mainly about the user's real environment, the responder should prefer 1 to 3 tightly related follow-up questions instead of speculative extra retrieval
- repeated same-scope responder retrieval requires a named `repeat_reason`

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
- the active `requested_evidence_goal`, when one is in play
- any unresolved evidence gap from the prior query
- soft exhausted scope keys (scopes where repeat retrieval now requires an explicit reason)
- hard exhausted scope keys (scopes where repeat retrieval is blocked unless a recognized reason is supplied)

An accompanying `MAGI_NET_NEW_INSTRUCTION` is also injected to guide tool use:

> When using tools: prefer net-new evidence regions not yet covered this run. Revisit covered regions only for contradiction checks, alternate-source confirmation, or explicit gap expansion.

Roles that attempt retrieval on an exhausted scope receive a router-owned `retrieval_gated` decision rather than an unconditional DB call. Recognized `repeat_reason` values are:

- `contradiction_check`
- `alternate_source_confirmation`
- `expand_beyond_covered_region`
- `fill_named_unresolved_gap`

Current MAGI rule:

- soft exhaustion triggers `require_reason`
- hard exhaustion triggers `block`
- the normal responder uses the same evidence-pool machinery, but through its own router-owned mini-protocol and without changing MAGI's role contracts or prompts

Current Historian fallback rule:

- Historian keeps local corpus retrieval first
- provider-native web search may be enabled only for Historian opening/discussion rounds
- that fallback is router-controlled and only appears after local low-usefulness or exhausted retrieval state

## Traceability

All agent actions, especially Magi deliberation phases, must emit explicit events and traces. The goal is to make the "why" behind every answer auditable and transparent.
