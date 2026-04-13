"""Per-run shared evidence pool.

Responsibilities:
- Track query records, evidence records, coverage, and exhaustion state.
- Issue gating decisions for retrieval across the normal responder and MAGI.
- Classify retrieval outcomes after the pipeline returns.
- Score usefulness of returned evidence, not only novelty.
- Build short prompt summaries for MAGI roles.
- Maintain an exact-fingerprint cache.

Non-responsibilities:
- Retrieval execution (search, rerank, overlap filtering, bundle assembly, formatting).
- Generating region keys from raw row data (that belongs to formatter/pipeline).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _tokenize(value: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(_normalize_text(value)) if len(token) >= 3}


# ---------------------------------------------------------------------------
# Region-key helpers
# ---------------------------------------------------------------------------

def build_region_key(source: str, page_start=None, page_end=None, row_key=None) -> str:
    """Canonical region key for a covered evidence region."""
    if page_start is not None and page_end is not None:
        return f"region:{source}:{int(page_start)}-{int(page_end)}"
    if row_key is not None:
        return f"region:{source}:singleton:{row_key}"
    return f"region:{source}:singleton:unknown"


def region_keys_from_retrieval_metadata(retrieval_metadata: dict) -> list[str]:
    """Derive region keys from the metadata dict returned by the search pipeline."""
    keys: list[str] = []
    for window in retrieval_metadata.get("delivered_page_windows") or []:
        source = window.get("source")
        ps = window.get("page_start")
        pe = window.get("page_end")
        if source and ps is not None and pe is not None:
            keys.append(build_region_key(source, ps, pe))
    for block_key in retrieval_metadata.get("delivered_block_keys") or []:
        if block_key and ":singleton:" in block_key:
            parts = block_key.split(":", 3)
            if len(parts) == 4:
                keys.append(build_region_key(parts[1], row_key=parts[3]))
    return keys


# ---------------------------------------------------------------------------
# Fingerprint / scope helpers
# ---------------------------------------------------------------------------

def _sha1_hex(data: str) -> str:
    return hashlib.sha1(data.encode("utf-8")).hexdigest()[:16]


GENERIC_EVIDENCE_GOALS = frozenset(
    {
        "identify_prerequisites",
        "create_target",
        "configure_access",
        "install_component",
        "verify_state",
        "troubleshoot_failure",
        "confirm_contradiction",
        "gather_alternate_source",
        "expand_covered_region",
        "fill_unresolved_gap",
    }
)


def normalize_evidence_goal(goal: str) -> str:
    return _normalize_text(goal).replace(" ", "_")


REPEAT_REASON_EXPAND = "expand_beyond_covered_region"
REPEAT_REASON_FILL_GAP = "fill_named_unresolved_gap"

_REPEAT_REASON_ALIASES = {
    "contradiction_check": "contradiction_check",
    "alternate_source_confirmation": "alternate_source_confirmation",
    "gap_expansion": REPEAT_REASON_EXPAND,
    "expand_beyond_covered_region": REPEAT_REASON_EXPAND,
    "explicit_gap": REPEAT_REASON_FILL_GAP,
    "fill_named_unresolved_gap": REPEAT_REASON_FILL_GAP,
}

ALLOWED_REPEAT_REASONS = frozenset(_REPEAT_REASON_ALIASES.values())


def normalize_repeat_reason(reason: str) -> str:
    return _REPEAT_REASON_ALIASES.get(_normalize_text(reason).replace(" ", "_"), "")


@dataclass(frozen=True)
class EvidenceScope:
    scope_key: str
    searchable_labels: tuple[str, ...]
    requested_evidence_goal: str = ""
    unresolved_issue: str = ""
    scope_anchor: str = ""


def build_evidence_scope(
    searchable_labels: list[str],
    *,
    requested_evidence_goal: str = "",
    unresolved_issue: str = "",
    normalized_query: str = "",
) -> EvidenceScope:
    labels = tuple(sorted(searchable_labels or []))
    goal = normalize_evidence_goal(requested_evidence_goal)
    issue = _normalize_text(unresolved_issue).replace(" ", "_")
    anchor = goal or issue or _normalize_text(normalized_query).replace(" ", "_") or "general"
    labels_part = ",".join(labels) or "all"
    return EvidenceScope(
        scope_key=f"{labels_part}::{anchor}",
        searchable_labels=labels,
        requested_evidence_goal=goal,
        unresolved_issue=issue,
        scope_anchor=anchor,
    )


def build_query_fingerprint(
    normalized_query: str,
    searchable_labels: list[str],
    requested_evidence_goal: str = "",
    unresolved_issue: str = "",
) -> str:
    payload = {
        "query": normalized_query or "",
        "labels": sorted(searchable_labels or []),
        "goal": normalize_evidence_goal(requested_evidence_goal),
        "unresolved_issue": _normalize_text(unresolved_issue),
    }
    return _sha1_hex(json.dumps(payload, sort_keys=True))


def build_result_set_fingerprint(bundle_keys: list[str], block_keys: list[str]) -> str:
    payload = {
        "bundle_keys": sorted(k for k in (bundle_keys or []) if k),
        "block_keys": sorted(k for k in (block_keys or []) if k),
    }
    return _sha1_hex(json.dumps(payload, sort_keys=True))


def build_evidence_fingerprint(region_keys: list[str], block_keys: list[str]) -> str:
    payload = {
        "region_keys": sorted(k for k in (region_keys or []) if k),
        "block_keys": sorted(k for k in (block_keys or []) if k),
    }
    return _sha1_hex(json.dumps(payload, sort_keys=True))


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass
class QueryRecord:
    query_fingerprint: str
    raw_query: str
    normalized_query: str
    searchable_labels: list[str] = field(default_factory=list)
    scope_key: str = ""
    caller_role: str = ""
    caller_phase: str = ""
    caller_round: int = 0
    unresolved_issue: str = ""
    requested_evidence_goal: str = ""
    repeat_reason: str = ""
    usefulness: str = ""
    usefulness_reason: str = ""
    outcome: str = ""
    linked_evidence_fingerprint: str = ""
    linked_result_set_fingerprint: str = ""


@dataclass
class EvidenceRecord:
    evidence_fingerprint: str
    result_set_fingerprint: str
    delivered_bundle_keys: list[str] = field(default_factory=list)
    delivered_block_keys: list[str] = field(default_factory=list)
    delivered_region_keys: list[str] = field(default_factory=list)
    selected_sources: list[str] = field(default_factory=list)
    net_new: bool = False


@dataclass
class CoverageRecord:
    covered_intervals: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    covered_singleton_keys: set[str] = field(default_factory=set)


@dataclass
class ScopeState:
    no_new_evidence_counts: dict[str, int] = field(default_factory=dict)
    low_value_streaks: dict[str, int] = field(default_factory=dict)
    zero_value_streaks: dict[str, int] = field(default_factory=dict)
    scope_query_counts: dict[str, int] = field(default_factory=dict)
    scope_seen_sources: dict[str, set[str]] = field(default_factory=dict)
    soft_exhausted_scope_keys: set[str] = field(default_factory=set)
    hard_exhausted_scope_keys: set[str] = field(default_factory=set)
    exhausted_scope_keys: set[str] = field(default_factory=set)
    last_allowed_reason: str = ""
    last_blocked_reason: str = ""
    last_require_reason: str = ""
    last_gate_action: str = ""


# ---------------------------------------------------------------------------
# Outcome / usefulness / gating constants
# ---------------------------------------------------------------------------

OUTCOME_CACHE_HIT = "cache_hit"
OUTCOME_REUSED_KNOWN = "reused_known_evidence"
OUTCOME_NEW_EVIDENCE = "delivered_new_evidence"
OUTCOME_NO_NEW = "no_new_evidence"
OUTCOME_EXHAUSTED = "search_exhausted_for_scope"

USEFULNESS_HIGH = "high"
USEFULNESS_MEDIUM = "medium"
USEFULNESS_LOW = "low"
USEFULNESS_ZERO = "zero"

GATE_ALLOW = "allow"
GATE_ALLOW_NET_NEW_ONLY = "allow_net_new_only"
GATE_REQUIRE_REASON = "require_reason"
GATE_BLOCK = "block"

SOFT_EXHAUSTION_LOW_VALUE_THRESHOLD = 2
HARD_EXHAUSTION_ZERO_THRESHOLD = 3
HARD_EXHAUSTION_LOW_VALUE_THRESHOLD = 4
NO_NEW_EVIDENCE_THRESHOLD = HARD_EXHAUSTION_ZERO_THRESHOLD


@dataclass
class GateDecision:
    action: str = GATE_ALLOW
    allow_search: bool = True
    prefer_net_new_only: bool = False
    allow_overlap_for_reason: str = ""
    requires_reason: bool = False
    scope_exhausted: bool = False
    blocked_reason: str = ""
    exhaustion_level: str = ""
    scope_key: str = ""


class EvidencePool:
    """Per-run, in-memory coordination layer for retrieval state."""

    def __init__(self):
        self.query_records: list[QueryRecord] = []
        self.evidence_records: dict[str, EvidenceRecord] = {}
        self.coverage = CoverageRecord()
        self.scope_state = ScopeState()
        self._known_query_fingerprints: set[str] = set()
        self._known_result_set_fingerprints: set[str] = set()
        self._cache: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Coverage helpers
    # ------------------------------------------------------------------

    def _add_covered_paged_region(self, source: str, ps: int, pe: int) -> bool:
        intervals = self.coverage.covered_intervals.get(source, [])
        for existing_ps, existing_pe in intervals:
            if existing_ps <= ps and pe <= existing_pe:
                return False
        updated = list(intervals)
        updated.append((ps, pe))
        self.coverage.covered_intervals[source] = updated
        return True

    def _add_covered_region(self, region_key: str) -> bool:
        if ":singleton:" in region_key:
            if region_key in self.coverage.covered_singleton_keys:
                return False
            self.coverage.covered_singleton_keys.add(region_key)
            return True
        try:
            _, source, page_range = region_key.split(":", 2)
            ps, pe = (int(x) for x in page_range.split("-", 1))
        except (ValueError, AttributeError):
            return False
        return self._add_covered_paged_region(source, ps, pe)

    def known_covered_region_keys(self) -> list[str]:
        keys = list(self.coverage.covered_singleton_keys)
        for source, intervals in self.coverage.covered_intervals.items():
            for ps, pe in intervals:
                keys.append(f"region:{source}:{ps}-{pe}")
        return keys

    def coverage_as_excluded_page_windows(self) -> list[dict]:
        windows = []
        for source, intervals in self.coverage.covered_intervals.items():
            for ps, pe in intervals:
                windows.append(
                    {
                        "key": f"window:{source}:{ps}-{pe}",
                        "source": source,
                        "page_start": ps,
                        "page_end": pe,
                    }
                )
        return windows

    def coverage_as_excluded_block_keys(self) -> list[str]:
        block_keys = []
        for region_key in self.coverage.covered_singleton_keys:
            if region_key.startswith("region:"):
                block_keys.append("block:" + region_key[len("region:"):])
        return sorted(block_keys)

    # ------------------------------------------------------------------
    # Scope helpers
    # ------------------------------------------------------------------

    def _scope(self, searchable_labels, *, requested_evidence_goal="", unresolved_issue="", normalized_query="") -> EvidenceScope:
        return build_evidence_scope(
            list(searchable_labels or []),
            requested_evidence_goal=requested_evidence_goal,
            unresolved_issue=unresolved_issue,
            normalized_query=normalized_query,
        )

    def _scope_key(self, caller_role: str, searchable_labels: list[str], *, requested_evidence_goal="", unresolved_issue="", normalized_query="") -> str:
        del caller_role
        return self._scope(
            searchable_labels,
            requested_evidence_goal=requested_evidence_goal,
            unresolved_issue=unresolved_issue,
            normalized_query=normalized_query,
        ).scope_key

    def _mark_scope_exhaustion(self, scope_key: str, usefulness: str) -> str:
        low_value_streak = self.scope_state.low_value_streaks.get(scope_key, 0)
        zero_value_streak = self.scope_state.zero_value_streaks.get(scope_key, 0)

        if usefulness in {USEFULNESS_LOW, USEFULNESS_ZERO}:
            low_value_streak += 1
        else:
            low_value_streak = 0

        if usefulness == USEFULNESS_ZERO:
            zero_value_streak += 1
        else:
            zero_value_streak = 0

        self.scope_state.low_value_streaks[scope_key] = low_value_streak
        self.scope_state.zero_value_streaks[scope_key] = zero_value_streak
        self.scope_state.no_new_evidence_counts[scope_key] = low_value_streak

        if zero_value_streak >= HARD_EXHAUSTION_ZERO_THRESHOLD or low_value_streak >= HARD_EXHAUSTION_LOW_VALUE_THRESHOLD:
            self.scope_state.hard_exhausted_scope_keys.add(scope_key)
            self.scope_state.soft_exhausted_scope_keys.discard(scope_key)
            self.scope_state.exhausted_scope_keys.add(scope_key)
            return "hard"

        if low_value_streak >= SOFT_EXHAUSTION_LOW_VALUE_THRESHOLD:
            self.scope_state.soft_exhausted_scope_keys.add(scope_key)
            self.scope_state.exhausted_scope_keys.add(scope_key)
            return "soft"

        self.scope_state.soft_exhausted_scope_keys.discard(scope_key)
        self.scope_state.hard_exhausted_scope_keys.discard(scope_key)
        self.scope_state.exhausted_scope_keys.discard(scope_key)
        return ""

    def _score_usefulness(
        self,
        retrieval_result: dict,
        query_record: QueryRecord,
        *,
        net_new_keys: list[str],
        newly_selected_sources: list[str],
        is_cache_hit: bool,
        is_reused_known: bool,
    ) -> tuple[str, str]:
        metadata = retrieval_result.get("retrieval_metadata") or {}
        context_text = str(retrieval_result.get("context_text") or "")
        selected_sources = list(retrieval_result.get("selected_sources") or [])
        excluded_seen_count = int(metadata.get("excluded_seen_count") or 0)
        has_evidence = bool(metadata.get("delivered_bundle_keys") or metadata.get("delivered_block_keys") or region_keys_from_retrieval_metadata(metadata))

        if not has_evidence:
            reason = "retrieval returned no prompt-facing evidence"
            if excluded_seen_count:
                reason = "retrieval only revisited already-covered regions"
            return USEFULNESS_ZERO, reason

        if is_cache_hit:
            return USEFULNESS_LOW, "retrieval reused a cached result for the same scope"

        goal_terms = _tokenize(query_record.requested_evidence_goal)
        gap_terms = _tokenize(query_record.unresolved_issue)
        evidence_terms = _tokenize(context_text + " " + " ".join(selected_sources))

        score = 0
        reasons: list[str] = []
        if net_new_keys:
            score += 1
            reasons.append("expanded covered evidence")
            if len(net_new_keys) >= 2:
                score += 1
        if newly_selected_sources:
            score += 1
            reasons.append("added source diversity")

        goal_overlap = len(goal_terms & evidence_terms)
        if goal_overlap >= 2:
            score += 2
            reasons.append("aligned with requested evidence goal")
        elif goal_overlap == 1:
            score += 1
            reasons.append("partially aligned with requested evidence goal")

        gap_overlap = len(gap_terms & evidence_terms)
        if gap_overlap >= 1:
            score += 1
            reasons.append("helped fill the unresolved gap")

        repeat_reason = normalize_repeat_reason(query_record.repeat_reason)
        if repeat_reason == "alternate_source_confirmation" and newly_selected_sources:
            score += 1
            reasons.append("confirmed the scope from an alternate source")
        if repeat_reason == "contradiction_check" and has_evidence:
            score += 1
            reasons.append("supported a contradiction check")

        if is_reused_known:
            score = min(score, 1)
            reasons.append("mostly repeated a known evidence set")
        if excluded_seen_count and not net_new_keys:
            score -= 1
            reasons.append("primarily revisited already-covered regions")

        if score >= 4:
            usefulness = USEFULNESS_HIGH
        elif score >= 2:
            usefulness = USEFULNESS_MEDIUM
        elif score >= 1:
            usefulness = USEFULNESS_LOW
        else:
            usefulness = USEFULNESS_ZERO

        if usefulness == USEFULNESS_ZERO and not reasons:
            reasons.append("did not materially advance the active scope")
        return usefulness, "; ".join(dict.fromkeys(reasons))

    # ------------------------------------------------------------------
    # Query and evidence recording
    # ------------------------------------------------------------------

    def record_query(
        self,
        raw_query: str,
        searchable_labels: list[str],
        caller_role: str = "",
        caller_phase: str = "",
        caller_round: int = 0,
        unresolved_issue: str = "",
        requested_evidence_goal: str = "",
        repeat_reason: str = "",
    ) -> QueryRecord:
        normalized = _normalize_text(raw_query)
        scope = self._scope(
            searchable_labels,
            requested_evidence_goal=requested_evidence_goal,
            unresolved_issue=unresolved_issue,
            normalized_query=normalized,
        )
        qfp = build_query_fingerprint(
            normalized,
            searchable_labels,
            requested_evidence_goal=requested_evidence_goal,
            unresolved_issue=unresolved_issue,
        )
        record = QueryRecord(
            query_fingerprint=qfp,
            raw_query=raw_query or "",
            normalized_query=normalized,
            searchable_labels=list(searchable_labels or []),
            scope_key=scope.scope_key,
            caller_role=caller_role,
            caller_phase=caller_phase,
            caller_round=caller_round,
            unresolved_issue=_normalize_text(unresolved_issue),
            requested_evidence_goal=scope.requested_evidence_goal,
            repeat_reason=normalize_repeat_reason(repeat_reason),
        )
        self._known_query_fingerprints.add(qfp)
        self.query_records.append(record)
        self.scope_state.scope_query_counts[scope.scope_key] = self.scope_state.scope_query_counts.get(scope.scope_key, 0) + 1
        return record

    def record_evidence_from_result(
        self,
        retrieval_result: dict,
        query_record: QueryRecord,
        *,
        is_cache_hit: bool = False,
    ) -> EvidenceRecord | None:
        metadata = retrieval_result.get("retrieval_metadata") or {}
        bundle_keys = list(metadata.get("delivered_bundle_keys") or [])
        block_keys = list(metadata.get("delivered_block_keys") or [])
        region_keys = list(metadata.get("delivered_region_keys") or []) or region_keys_from_retrieval_metadata(metadata)
        selected_sources = list(retrieval_result.get("selected_sources") or [])

        rsfp = build_result_set_fingerprint(bundle_keys, block_keys)
        efp = build_evidence_fingerprint(region_keys, block_keys)
        query_record.linked_result_set_fingerprint = rsfp
        query_record.linked_evidence_fingerprint = efp

        seen_sources = self.scope_state.scope_seen_sources.setdefault(query_record.scope_key, set())
        newly_selected_sources = [source for source in selected_sources if source and source not in seen_sources]
        seen_sources.update(source for source in selected_sources if source)

        is_reused_known = bool(bundle_keys or block_keys or region_keys) and rsfp in self._known_result_set_fingerprints
        net_new_keys = [region_key for region_key in region_keys if self._add_covered_region(region_key)] if not is_cache_hit and not is_reused_known else []

        usefulness, usefulness_reason = self._score_usefulness(
            retrieval_result,
            query_record,
            net_new_keys=net_new_keys,
            newly_selected_sources=newly_selected_sources,
            is_cache_hit=is_cache_hit,
            is_reused_known=is_reused_known,
        )
        query_record.usefulness = usefulness
        query_record.usefulness_reason = usefulness_reason

        exhaustion_level = self._mark_scope_exhaustion(query_record.scope_key, usefulness)

        if is_cache_hit:
            query_record.outcome = OUTCOME_CACHE_HIT
            return self.evidence_records.get(efp)

        if is_reused_known:
            query_record.outcome = OUTCOME_REUSED_KNOWN
            return self.evidence_records.get(efp)

        if net_new_keys:
            query_record.outcome = OUTCOME_NEW_EVIDENCE
        elif exhaustion_level == "hard":
            query_record.outcome = OUTCOME_EXHAUSTED
        else:
            query_record.outcome = OUTCOME_NO_NEW

        self._known_result_set_fingerprints.add(rsfp)
        evidence_record = EvidenceRecord(
            evidence_fingerprint=efp,
            result_set_fingerprint=rsfp,
            delivered_bundle_keys=bundle_keys,
            delivered_block_keys=block_keys,
            delivered_region_keys=region_keys,
            selected_sources=selected_sources,
            net_new=bool(net_new_keys),
        )
        self.evidence_records[efp] = evidence_record
        return evidence_record

    # ------------------------------------------------------------------
    # Gating
    # ------------------------------------------------------------------

    def check_gate(
        self,
        raw_query: str,
        searchable_labels: list[str],
        caller_role: str = "",
        repeat_reason: str = "",
        requested_evidence_goal: str = "",
        unresolved_issue: str = "",
    ) -> GateDecision:
        normalized = _normalize_text(raw_query)
        scope = self._scope(
            searchable_labels,
            requested_evidence_goal=requested_evidence_goal,
            unresolved_issue=unresolved_issue,
            normalized_query=normalized,
        )
        qfp = build_query_fingerprint(
            normalized,
            searchable_labels,
            requested_evidence_goal=requested_evidence_goal,
            unresolved_issue=unresolved_issue,
        )
        repeated_scope = self.scope_state.scope_query_counts.get(scope.scope_key, 0) > 0
        exact_query_seen = qfp in self._known_query_fingerprints
        canonical_repeat_reason = normalize_repeat_reason(repeat_reason)
        caller_is_magi = bool(caller_role)
        hard_exhausted = scope.scope_key in self.scope_state.hard_exhausted_scope_keys
        soft_exhausted = scope.scope_key in self.scope_state.soft_exhausted_scope_keys

        if canonical_repeat_reason in ALLOWED_REPEAT_REASONS:
            self.scope_state.last_allowed_reason = canonical_repeat_reason
            self.scope_state.last_gate_action = GATE_ALLOW
            return GateDecision(
                action=GATE_ALLOW,
                allow_search=True,
                prefer_net_new_only=bool(repeated_scope),
                allow_overlap_for_reason=canonical_repeat_reason,
                scope_exhausted=soft_exhausted or hard_exhausted,
                exhaustion_level="hard" if hard_exhausted else ("soft" if soft_exhausted else ""),
                scope_key=scope.scope_key,
            )

        if hard_exhausted:
            reason = f"scope hard exhausted for {scope.scope_key}"
            self.scope_state.last_blocked_reason = reason
            self.scope_state.last_gate_action = GATE_BLOCK
            return GateDecision(
                action=GATE_BLOCK,
                allow_search=False,
                scope_exhausted=True,
                blocked_reason=reason,
                exhaustion_level="hard",
                scope_key=scope.scope_key,
            )

        if caller_is_magi and soft_exhausted:
            reason = f"scope soft exhausted for {scope.scope_key}; provide repeat_reason or refine requested_evidence_goal"
            self.scope_state.last_require_reason = reason
            self.scope_state.last_gate_action = GATE_REQUIRE_REASON
            return GateDecision(
                action=GATE_REQUIRE_REASON,
                allow_search=False,
                requires_reason=True,
                scope_exhausted=True,
                blocked_reason=reason,
                exhaustion_level="soft",
                scope_key=scope.scope_key,
            )

        if not caller_is_magi and soft_exhausted and repeated_scope and exact_query_seen:
            reason = f"scope is repeating low-value evidence for {scope.scope_key}; refine requested_evidence_goal or provide repeat_reason"
            self.scope_state.last_require_reason = reason
            self.scope_state.last_gate_action = GATE_REQUIRE_REASON
            return GateDecision(
                action=GATE_REQUIRE_REASON,
                allow_search=False,
                requires_reason=True,
                scope_exhausted=True,
                blocked_reason=reason,
                exhaustion_level="soft",
                scope_key=scope.scope_key,
            )

        if repeated_scope or exact_query_seen:
            self.scope_state.last_gate_action = GATE_ALLOW_NET_NEW_ONLY
            return GateDecision(
                action=GATE_ALLOW_NET_NEW_ONLY,
                allow_search=True,
                prefer_net_new_only=True,
                scope_exhausted=soft_exhausted,
                exhaustion_level="soft" if soft_exhausted else "",
                scope_key=scope.scope_key,
            )

        self.scope_state.last_gate_action = GATE_ALLOW
        return GateDecision(
            action=GATE_ALLOW,
            allow_search=True,
            scope_key=scope.scope_key,
        )

    # ------------------------------------------------------------------
    # Prompt summary / observability
    # ------------------------------------------------------------------

    def build_prompt_summary(self, max_regions: int = 5) -> str:
        lines = ["EVIDENCE POOL SUMMARY:"]

        covered = self.known_covered_region_keys()
        if covered:
            lines.append(f"  Covered regions ({len(covered)}):")
            for key in sorted(covered)[:max_regions]:
                lines.append(f"    - {key}")
            if len(covered) > max_regions:
                lines.append(f"    - ... and {len(covered) - max_regions} more")
        else:
            lines.append("  No evidence regions covered yet.")

        if self.query_records:
            last = self.query_records[-1]
            if last.outcome:
                lines.append(f"  Latest retrieval outcome: {last.outcome}")
            if last.usefulness:
                lines.append(f"  Latest usefulness: {last.usefulness}")
            if last.usefulness_reason:
                lines.append(f"  Usefulness note: {last.usefulness_reason}")
            if last.requested_evidence_goal:
                lines.append(f"  Active evidence goal: {last.requested_evidence_goal}")
            if last.unresolved_issue:
                lines.append(f"  Unresolved evidence gap: {last.unresolved_issue}")

        if self.scope_state.soft_exhausted_scope_keys:
            lines.append(f"  Soft exhausted scopes: {', '.join(sorted(self.scope_state.soft_exhausted_scope_keys))}")
        if self.scope_state.hard_exhausted_scope_keys:
            lines.append(f"  Hard exhausted scopes: {', '.join(sorted(self.scope_state.hard_exhausted_scope_keys))}")

        return "\n".join(lines)

    def summary_event_payload(self) -> dict:
        last = self.query_records[-1] if self.query_records else None
        return {
            "query_count": len(self.query_records),
            "evidence_count": len(self.evidence_records),
            "covered_region_count": len(self.known_covered_region_keys()),
            "soft_exhausted_scope_keys": sorted(self.scope_state.soft_exhausted_scope_keys),
            "hard_exhausted_scope_keys": sorted(self.scope_state.hard_exhausted_scope_keys),
            "exhausted_scope_keys": sorted(self.scope_state.exhausted_scope_keys),
            "last_outcome": last.outcome if last else "",
            "last_usefulness": last.usefulness if last else "",
            "last_usefulness_reason": last.usefulness_reason if last else "",
            "last_scope_key": last.scope_key if last else "",
            "last_requested_evidence_goal": last.requested_evidence_goal if last else "",
        }

    def last_query_outcome(self) -> str:
        return self.query_records[-1].outcome if self.query_records else ""

    def last_query_usefulness(self) -> str:
        return self.query_records[-1].usefulness if self.query_records else ""

    def last_query_scope_key(self) -> str:
        return self.query_records[-1].scope_key if self.query_records else ""

    def historian_web_fallback_allowed(self) -> bool:
        if self.scope_state.hard_exhausted_scope_keys or self.scope_state.soft_exhausted_scope_keys:
            return True
        return self.last_query_usefulness() in {USEFULNESS_LOW, USEFULNESS_ZERO}
