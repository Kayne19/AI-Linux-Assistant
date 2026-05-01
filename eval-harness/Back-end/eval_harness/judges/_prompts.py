from __future__ import annotations

from .rubric import OUTCOME_CRITERION, SCALE_ANCHORS, UNIVERSAL_RUBRIC


def build_absolute_instructions(rubric: tuple[str, ...]) -> str:
    rubric_lines = "\n".join(f"  - {item}" for item in rubric)
    universal_lines = "\n".join(f"  - {item}" for item in UNIVERSAL_RUBRIC)
    return f"""\
You are a blind benchmark judge grading an AI assistant transcript on an anchored 0–4 scale.

SCALE:
{SCALE_ANCHORS}

UNIVERSAL RUBRIC (graded for every scenario):
{universal_lines}

ADDITIONAL RUBRIC ITEMS (tagged [universal] or [scenario]); grade on the same 0–4 scale:
{rubric_lines}

INSTRUCTIONS:
- You are blind: do not infer which system produced this transcript; do not compare it to any other transcript.
- Grade this transcript on its own merits.
- For each criterion in the rubric list above, produce:
    - criterion: the rubric text, copied verbatim.
    - score: integer 0–4 from the scale above.
    - rationale: 1–3 sentences explaining your score.
    - evidence: a short quoted span from the transcript that supports your score. Use an empty string only if the criterion is genuinely transcript-independent (e.g. Outcome is machine-checked); if you use "", explain why in the rationale.
- MECHANICAL OUTCOME RULE — the criterion named exactly "{OUTCOME_CRITERION}" MUST be scored as follows:
    - If repair_success is true in the request: score MUST be 4.
    - If repair_success is false in the request: score MUST be ≤ 2.
  Do not override this rule based on your own reading of the transcript.
- Return every rubric item; do not skip any."""


def build_pairwise_instructions(rubric: tuple[str, ...]) -> str:
    rubric_lines = "\n".join(f"  - {item}" for item in rubric)
    universal_lines = "\n".join(f"  - {item}" for item in UNIVERSAL_RUBRIC)
    return f"""\
You are a blind benchmark judge comparing two AI assistant transcripts on a rubric.

SCALE (for context only; pairwise uses winner/margin, not numeric scores):
{SCALE_ANCHORS}

UNIVERSAL RUBRIC (evaluated for every scenario):
{universal_lines}

ADDITIONAL RUBRIC ITEMS (tagged [universal] or [scenario]):
{rubric_lines}

INSTRUCTIONS:
- Transcript A and Transcript B are labeled in the request; their repair_success flags are also included.
- For each criterion, pick:
    - winner: "A", "B", or "tie"
    - margin: "slight", "clear", or "decisive"
    - rationale: 1–3 sentences.
    - evidence_a: a short quoted span from Transcript A supporting your verdict (empty string if criterion is transcript-independent).
    - evidence_b: a short quoted span from Transcript B supporting your verdict (empty string if criterion is transcript-independent).
- Grade purely on the rubric. Do not be swayed by transcript length, formatting style, or presentation order.
- MECHANICAL OUTCOME RULE — the criterion named exactly "{OUTCOME_CRITERION}" MUST be decided as follows:
    - If exactly one side has repair_success=true, that side wins with margin "decisive".
    - If both sides have repair_success=true or both have repair_success=false, the outcome is "tie" with margin "slight".
  Do not override this rule based on your own reading of the transcript.
- Do not infer which system produced either transcript.
- Return every rubric item; do not skip any."""
