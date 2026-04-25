# Heuristics Inventory

This document tracks hard-coded policy, scoring, fallback, and threshold logic that intentionally shapes backend behavior.

Heuristics are allowed when they make the system safer, more observable, or more useful than an unconstrained model or raw similarity search. They should not become invisible product policy.

## Maintenance Rule

When adding or changing a heuristic:

- add or update an entry in this file in the same commit
- name the owner and file/function
- state why the heuristic exists
- state what could go wrong
- state whether the value should eventually become configurable, evaluated, learned, or removed

If the heuristic materially changes router workflow, retrieval ranking, memory policy, security behavior, or user-visible output, it should also emit enough debug data for operators to inspect the decision.

## Review Labels

Use these labels in entries:

- `Accepted` — useful and intentionally hard-coded for now
- `Watch` — probably useful, but likely to need tuning or replacement
- `Debt` — tolerated because it unblocks behavior, but should be redesigned
- `Config candidate` — should likely move to settings once stable
- `Eval candidate` — needs regression/eval coverage before further tuning

## Retrieval

### Query Scope Signal Extraction

- Status: `Watch`, `Eval candidate`
- Owner: [retrieval/scope.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/retrieval/scope.py)
- Code: `_PACKAGE_MANAGER_HINTS`, `_INIT_SYSTEM_HINTS`, `_SUBSYSTEM_HINTS`, `extract_scope_signals_from_query()`

What it does:

- maps literal query tokens to controlled vocabulary hints, such as `apt-get -> apt`, `systemctl -> systemd`, `zfs -> filesystems`, and `docker -> containers`
- uses conservative regex word-boundary matching
- merges these hints with router-provided `scope_hints`

Why it exists:

- lets retrieval scope itself even when the LLM omits explicit tool hints
- reduces cross-family contamination before chunk-level hybrid search

Risks:

- incomplete vocabulary coverage
- ambiguous terms can over-narrow retrieval
- hidden taxonomy assumptions can bias document selection

Preferred future:

- keep the dictionary small and audited
- add retrieval eval cases for each new mapping
- consider replacing or augmenting with structured query classification once there is enough eval data

### Document Trust And Freshness Weights

- Status: `Watch`, `Eval candidate`
- Owner: [retrieval/scope.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/retrieval/scope.py)
- Code: `_TRUST_WEIGHTS`, `_FRESHNESS_WEIGHTS`, `score_doc()`

What it does:

- ranks scoped document candidates with fixed weights:
  - trust: `canonical=4.0`, `official=3.0`, `community=2.0`, `unofficial=1.0`, `unknown=0.5`
  - freshness: `current=4.0`, `supported=3.0`, `legacy=2.0`, `deprecated=1.0`, `archived=0.5`, `unknown=1.0`

Why it exists:

- makes canonical/current documentation win by default when multiple documents match the same topic
- gives the system an explicit preference for authoritative sources without relying on model judgment

Risks:

- can suppress a more relevant community document below a less relevant official one
- fixed values may not fit every source family

Preferred future:

- keep values visible and covered by retrieval ranking tests
- calibrate with corpus-level retrieval evals after the expanded corpus is ingested

### Document Field Match Ordering

- Status: `Watch`
- Owner: [retrieval/scope.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/retrieval/scope.py)
- Code: `_SCORING_FIELDS`, `_weighted_field_score()`, `widen_hint()`

What it does:

- treats document fields as increasingly broad in this order:
  - `package_managers`
  - `init_systems`
  - `major_subsystems`
  - `os_family`
  - `source_family`
- uses that order both for scoring strength and widening

Why it exists:

- gives specific operational signals more ranking influence than broad family labels
- provides deterministic widening when the first scope is too narrow

Risks:

- some queries are better scoped by source family than package manager
- the same ordering is doing two jobs: score weighting and fallback widening

Preferred future:

- evaluate whether scoring order and widening order should split into separate policies

### Scope Widening Thresholds

- Status: `Config candidate`, `Eval candidate`
- Owner: [retrieval/config.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/retrieval/config.py), [retrieval/search_pipeline.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/retrieval/search_pipeline.py)
- Code: `scope_min_hit_count`, `scope_min_top_score`, `scope_max_widenings`, `should_widen()`

What it does:

- widens a selected document scope when candidate count or top score is below configured floors
- stops widening after a configured ceiling

Why it exists:

- prevents over-narrow retrieval from returning no useful chunks
- keeps weak hints from blocking all evidence

Risks:

- low thresholds can keep bad narrow scopes
- high thresholds can over-widen and reintroduce cross-family contamination

Preferred future:

- tune through retrieval evals
- keep the values runtime-configurable once the right operating range is known

### Source Profile Boosting

- Status: `Debt`, `Eval candidate`
- Owner: [retrieval/search_pipeline.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/retrieval/search_pipeline.py)
- Code: `_build_source_profiles()`, `_source_boost()`, `_rank_candidates()`

What it does:

- samples rows by source
- builds a lightweight token profile with TF-IDF-like scoring
- adds `0.2 * source_boost` to rerank scores

Why it exists:

- nudges anchors toward sources that share query-specific terms

Risks:

- overlaps with the new document-scope selector
- source-level profiles are coarse and can reward incidental token overlap
- sampling behavior can shift as corpus size changes

Preferred future:

- evaluate whether metadata-aware document scoping makes this unnecessary
- remove if retrieval evals show no benefit after the new ingestion corpus is active

### Requested Evidence Goal Boost

- Status: `Watch`
- Owner: [retrieval/search_pipeline.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/retrieval/search_pipeline.py)
- Code: `_goal_alignment_boost()`

What it does:

- tokenizes `requested_evidence_goal`
- adds a bounded boost based on overlap with chunk text/source text
- caps the boost at `0.45`

Why it exists:

- gives the router's evidence goal limited influence over anchor choice without replacing reranking

Risks:

- token overlap is shallow
- can reward repeated generic terms rather than true evidence fit

Preferred future:

- keep bounded
- evaluate alongside EvidencePool usefulness scoring

### Bundle And Expansion Limits

- Status: `Accepted`, `Config candidate`
- Owner: [retrieval/search_pipeline.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/retrieval/search_pipeline.py), [retrieval/config.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/retrieval/config.py)
- Code: `initial_fetch`, `final_top_k`, `neighbor_pages`, `max_expanded`, `source_profile_sample`

What it does:

- bounds initial candidates, selected anchors, neighbor page expansion, final expanded row count, and source-profile sampling

Why it exists:

- keeps retrieval prompt size and runtime cost bounded
- prevents one retrieval from flooding the responder with too much context

Risks:

- can omit useful context when documents are dense or page boundaries are noisy
- values are corpus-dependent

Preferred future:

- keep tunable through app settings
- track retrieval quality and latency before changing defaults

## Router And Evidence Pool

### Evidence Goal Derivation

- Status: `Watch`
- Owner: [orchestration/model_router.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration/model_router.py)
- Code: `_derive_requested_evidence_goal()`

What it does:

- maps query/gap text tokens to generic evidence goals such as `identify_prerequisites`, `create_target`, `configure_access`, `install_component`, `verify_state`, and `troubleshoot_failure`

Why it exists:

- gives repeated retrieval a stable purpose when the model did not provide one
- improves EvidencePool scope keys and retrieval anchor selection

Risks:

- token rules can misclassify user intent
- derived goals can affect caching, gating, and retrieval boosts

Preferred future:

- move toward model-emitted structured goals with deterministic validation
- keep the fallback small and test-covered

### Environment-Fact Follow-Up Preference

- Status: `Watch`
- Owner: [orchestration/model_router.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration/model_router.py)
- Code: `_decision_prefers_follow_up_questions()`

What it does:

- prefers asking follow-up questions when the unresolved gap appears environment-specific
- uses `gap_type` plus tokens such as `dhcp`, `static ip`, `bridge`, `dns`, `hostname`, and `target host`

Why it exists:

- prevents documentation retrieval from pretending to know local environment facts

Risks:

- can ask unnecessary follow-ups when docs would be enough
- can miss environment-specific gaps that do not use the token list

Preferred future:

- keep as safety-oriented behavior
- evaluate against conversation fixtures where asking a question is vs. is not appropriate

### Evidence Usefulness Scoring

- Status: `Watch`, `Eval candidate`
- Owner: [orchestration/evidence_pool.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration/evidence_pool.py)
- Code: `_score_usefulness()`

What it does:

- scores retrieval output with token overlap, new region counts, new source counts, repeat reason, and evidence presence
- maps score bands to `high`, `medium`, `low`, or `zero`

Why it exists:

- lets the router reason about whether retrieval is helping instead of counting tool calls only
- supports gating and web fallback decisions

Risks:

- token overlap is not semantic understanding
- scoring bands are manually chosen
- can mark useful but lexically different evidence as low-value

Preferred future:

- add regression cases for usefulness classifications
- consider replacing parts of the scoring with structured retrieval outcome evaluation

### Scope Exhaustion And Gating

- Status: `Watch`
- Owner: [orchestration/evidence_pool.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration/evidence_pool.py)
- Code: `_mark_scope_exhaustion()`, `check_gate()`

What it does:

- marks repeated low/zero usefulness as soft or hard exhausted
- blocks or requires `repeat_reason` for repeated same-scope retrieval
- allows `allow_net_new_only` when appropriate

Why it exists:

- prevents expensive loops over the same low-value evidence
- forces the model to explain why another retrieval is justified

Risks:

- can gate retrieval too aggressively after a bad query rewrite
- can require repeat reasons when a human would simply search again with better wording

Preferred future:

- keep observable through `evidence_pool_update`
- tune with router runtime tests and real debug traces

### Web Fallback Allowance

- Status: `Accepted`, `Watch`
- Owner: [orchestration/evidence_pool.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration/evidence_pool.py)
- Code: `historian_web_fallback_allowed()`

What it does:

- allows Historian web fallback only when retrieval is exhausted or latest usefulness is `low` / `zero`

Why it exists:

- keeps local RAG primary and makes web search fallback behavior explicit

Risks:

- can delay web search when local docs are stale but not obviously exhausted

Preferred future:

- keep conservative
- revisit after freshness metadata is fully used by retrieval and answer synthesis

### Repeat Reason And Gap Type Vocabularies

- Status: `Accepted`
- Owner: [orchestration/evidence_pool.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration/evidence_pool.py), [orchestration/model_router.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration/model_router.py)
- Code: `ALLOWED_REPEAT_REASONS`, `ALLOWED_GAP_TYPES`, `normalize_repeat_reason()`, `normalize_gap_type()`

What it does:

- constrains repeated retrieval justifications and unresolved-gap categories to fixed vocabularies

Why it exists:

- keeps router protocol observable and prevents vague tool-loop behavior

Risks:

- fixed categories may be too narrow for future workflows

Preferred future:

- expand deliberately with tests and docs

## Magi

### Discussion Gate

- Status: `Watch`
- Owner: [agents/magi/system.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents/magi/system.py)
- Code: `_discussion_gate()`

What it does:

- decides whether Magi discussion is forced based on opening-position divergence and Historian grounding quality

Why it exists:

- avoids unnecessary council discussion when roles align and grounding is strong
- forces at least one discussion round when disagreement or weak grounding would make a direct synthesis fragile

Risks:

- role-output wording can affect whether openings are considered divergent
- grounding labels are model-produced structured values

Preferred future:

- keep `magi_discussion_gate` events visible
- add fixtures for aligned, divergent, weak-grounding, and conflicted-grounding cases

### Discussion And Tool-Round Caps

- Status: `Accepted`, `Config candidate`
- Owner: [agents/magi/system.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents/magi/system.py), [agents/magi/roles.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents/magi/roles.py), [agents/magi/arbiter.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents/magi/arbiter.py)
- Code: `max_discussion_rounds`, `max_tool_rounds`

What it does:

- caps deliberation and tool-call loops

Why it exists:

- bounds latency, cost, and runaway deliberation

Risks:

- hard caps can stop useful investigation early

Preferred future:

- keep configurable through settings
- tune using Magi runtime traces

## Ingestion

### Identity Normalization And Vocabulary Coercion

- Status: `Accepted`, `Watch`
- Owner: [ingestion/identity](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/ingestion/identity)
- Code: resolver/schema/vocabulary coercion and sidecar merge rules

What it does:

- normalizes document identity fields into controlled vocabularies
- coerces invalid or missing values to known defaults where appropriate

Why it exists:

- retrieval scope depends on consistent metadata
- LanceDB rows need stable schema and types

Risks:

- normalization mistakes propagate into document ranking and scope selection
- default values can hide missing metadata if not audited

Preferred future:

- keep identity audit traces and tests current
- prefer operator-visible quarantine over silent coercion for high-impact fields

## Memory

### Memory Extraction And Resolution Policy

- Status: `Watch`
- Owner: [persistence/MEMORY.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/persistence/MEMORY.md), [agents/memory_extractor.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents/memory_extractor.py), [agents/memory_resolver.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/agents/memory_resolver.py)

What it does:

- uses structured model output plus resolver rules to decide what becomes committed memory, unresolved memory, or conflict

Why it exists:

- memory needs policy beyond simple persistence
- local environment facts should constrain future answers only when they are reliable enough

Risks:

- model extraction can miss or overstate facts
- resolver policy can reject useful but incomplete facts or preserve stale ones

Preferred future:

- keep committed vs. unresolved outcomes tested
- document any new resolver thresholds or fixed vocabularies here as they are added

## Frontend And UX

### Debug Event Summaries

- Status: `Accepted`
- Owner: [Front-end/src/debug/debugUtils.ts](/home/kayne19/projects/AI-Linux-Assistant/Front-end/src/debug/debugUtils.ts)

What it does:

- compresses structured event payloads into short operator-facing labels

Why it exists:

- makes the debug drawer scannable

Risks:

- summary text can hide important fields if the detail panel is not checked

Preferred future:

- keep summaries short, but ensure important new backend decisions have a visible detail card

## Provider And Runtime Configuration

### Default Providers, Models, And Runtime Limits

- Status: `Config candidate`
- Owner: [config/settings.py](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/config/settings.py), [providers](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/providers)

What it does:

- chooses default role/provider/model settings, retrieval provider defaults, worker counts, and runtime caps

Why it exists:

- gives local development and production a coherent baseline without requiring full configuration for every role

Risks:

- defaults can silently become product policy
- model changes can shift behavior without code changes

Preferred future:

- keep defaults centralized
- prefer explicit setting names over hidden constants
- document behavior-changing defaults in the relevant subsystem docs and here when they are heuristic policy

