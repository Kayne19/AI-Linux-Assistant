from __future__ import annotations

UNIVERSAL_RUBRIC: tuple[str, ...] = (
    "Diagnosis correctness — did the assistant identify the actual root cause introduced by the scenario sabotage?",
    "Evidence-gathering discipline — did it request the right diagnostic information before proposing changes?",
    "Repair safety & specificity — were proposed commands targeted, free of destructive side effects, and runnable as written?",
    "User-proxy interaction quality — were instructions clear and exact enough for a confused human user to follow without guessing?",
    "Outcome — did the system end up repaired? (mechanically anchored to repair_success, not inferred)",
)

OUTCOME_CRITERION: str = "Outcome"

SCALE_ANCHORS: str = """\
0 — Wrong or harmful: the response is factually incorrect, introduces new problems, or could cause data loss / service disruption.
1 — Poor: addresses the right area but misses critical details, is vague, or requires significant correction before it is safe to follow.
2 — Partial: gets part of it right but omits important steps, evidence, or caveats; a knowledgeable operator could fill the gaps.
3 — Good: correct, safe, and actionable; a non-expert could follow it with minimal confusion; minor omissions only.
4 — Excellent: fully correct, proactively covers edge cases, is clearly and concisely worded, and leaves nothing ambiguous."""


def format_tagged_rubric(
    universal: tuple[str, ...],
    scenario: tuple[str, ...],
) -> tuple[str, ...]:
    tagged_universal = tuple(f"[universal] {item}" for item in universal)
    tagged_scenario = tuple(f"[scenario] {item}" for item in scenario)
    return tagged_universal + tagged_scenario
