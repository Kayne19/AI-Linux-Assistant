# Benchmark First-Turn Generation Design

## Summary

The benchmark should stop using `observable_problem_statement` as the literal first user message shown to benchmark subjects.

Instead, each scenario revision should get one generated, persisted `initial_user_message` that sounds like a realistic human opening turn. That message may be generated once using hidden scenario details, but those hidden details must not be exposed to the proxy or the subject during the benchmark itself.

The generated first turn should then be self-reviewed by the proxy model. The approved final message is stored on the scenario revision and reused for every benchmark subject and every evaluation run for that revision. Review notes are stored in scenario metadata for audit and debugging.

## Goals

- Make turn 1 sound like a real human instead of planner-authored scenario text.
- Prevent first-turn leakage of diagnosis, exact fix location, or remediation hints.
- Keep benchmark inputs comparable across subjects by reusing one canonical first turn per scenario revision.
- Ensure the proxy sees the first turn in transcript history on later turns, even though it did not literally author it.
- Preserve auditability by storing the draft and review outcome in scenario metadata.

## Non-Goals

- Per-run first-turn variation.
- Exposing hidden scenario details to the proxy after initial message generation.
- Replacing `observable_problem_statement` as a planner-facing scenario field.
- Turning first-turn quality into a large rule engine.

## Why This Change

Today the benchmark sends `scenario.observable_problem_statement` as the first user message. If that field is overly specific, the benchmark leaks too much information immediately. A realistic benchmark opening should communicate symptoms and what the user saw, not the answer.

The existing proxy already sees the first turn because the benchmark appends that turn to the transcript before later proxy turns are generated. The problem is not transcript visibility; the problem is the source and quality of the first-turn text.

## Proposed Design

### Persisted Fields

Add a new scenario-revision field:

- `initial_user_message: str`

Persist review details in scenario revision metadata:

- `initial_user_message_generation`
  - `draft`
  - `review_outcome`
  - `review_notes`
  - `final_message`
  - `used_fallback`

If a dedicated structured metadata key already exists for planner outputs, store this under that structure rather than creating a parallel top-level metadata blob.

### Generation Flow

During scenario generation or scenario repair:

1. Build a hidden-context prompt for the first-turn generator.
2. Allow the generator to see hidden scenario details such as:
   - sabotage context
   - verification intent
   - user-visible symptoms
   - likely commands or errors the user realistically would have seen
3. Generate a draft `initial_user_message` in the proxy persona.
4. Run a second pass using the proxy reviewer:
   - approve as-is, or
   - rewrite into a safer, more realistic first turn
5. Store the final approved message on the scenario revision.
6. Store the draft and review notes in metadata.

The review step is the primary guardrail. This keeps the design aligned with the proxy persona rather than relying on a large brittle rule set.

### Benchmark Runtime Behavior

At benchmark start:

- use `initial_user_message` as turn 1 if present
- fall back to `observable_problem_statement` only if generation/review failed or the field is absent
- append the opening message to transcript history exactly as if the proxy had sent it

After that:

- later proxy turns continue to use only the normal transcript and the latest assistant reply
- hidden scenario details used during first-turn generation are not passed to normal proxy turns

This means the proxy can be told, implicitly or explicitly, that it sent the opening message, even if that message was pre-generated and stored earlier.

## Review Prompt Behavior

The self-review step should answer a small structured question:

- is the draft realistic as a user opening turn
- does it leak diagnosis or fix hints
- should it be approved or rewritten

If rewritten, the reviewer returns the replacement message plus short notes explaining what was wrong with the draft.

The review notes are for internal audit only and must not be exposed during benchmark execution.

## Fallback Behavior

If first-turn generation or self-review fails:

- store fallback metadata showing that generation failed
- use `observable_problem_statement` as the runtime opening message

This preserves benchmark execution reliability while making degraded scenario quality visible for follow-up cleanup.

## Implementation Shape

### Scenario Pipeline

Update scenario generation/repair flow to produce and persist `initial_user_message` before a scenario revision is treated as ready for benchmark use.

Likely touchpoints:

- scenario planner transport/schema
- scenario validation/persistence
- setup/revision lifecycle code

### Benchmark Orchestration

Update benchmark startup to prefer `initial_user_message` over `observable_problem_statement`.

Keep transcript handling unchanged except for the opening-message source. The proxy should continue receiving transcript history that includes the first turn.

### Validation Strategy

Do not build a large deterministic filter system.

Primary safety mechanism:

- generator produces draft
- proxy reviewer approves or rewrites
- final approved result is persisted

Minimal hard fallback is acceptable only for catastrophic cases such as empty output or repeated review failure.

## Testing Plan

Add coverage for:

- scenario generation persists `initial_user_message`
- review metadata persists draft, outcome, notes, and final message
- benchmark uses stored `initial_user_message` as turn 1
- all subjects in the same benchmark revision receive the same opening message
- later proxy turns see the first turn in transcript history
- hidden scenario details are not supplied to normal proxy turns after startup
- fallback path uses `observable_problem_statement` and marks metadata when generation or review fails

## Risks

### Review Drift

If the reviewer becomes too permissive, first-turn leakage can still happen. Audit metadata makes this visible and debuggable.

### Over-Rewriting

If the reviewer rewrites too aggressively, first turns may become generic and lose useful symptom detail. Prompting should preserve realistic user-visible evidence while removing explicit diagnosis.

### Partial Adoption

If benchmark runtime is updated before scenario generation stores the new field consistently, old revisions must continue working through fallback behavior.

## Recommendation

Implement this as a scenario-revision feature, not as a per-run proxy feature.

That keeps the benchmark deterministic across subjects, improves realism, and localizes the new complexity to scenario preparation rather than the hot path of benchmark execution.
