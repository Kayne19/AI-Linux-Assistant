"""Per-run shared evidence pool.

Responsibilities:
- Track query records, evidence records, coverage, and exhaustion state.
- Issue gating decisions for retrieval (allow, prefer_net_new, block exhausted scope).
- Classify retrieval outcomes after the pipeline returns.
- Build short prompt summaries for MAGI roles.
- Maintain an exact-fingerprint cache (replaces the old ledger cache).

Non-responsibilities:
- Retrieval execution (search, rerank, overlap filtering, bundle assembly, formatting).
- Generating region_keys from raw row data (that belongs to formatter/pipeline).
"""
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Region-key helpers
# ---------------------------------------------------------------------------

def build_region_key(source: str, page_start=None, page_end=None, row_key=None) -> str:
    """Canonical region key for a covered evidence region.

    Paged:     region:{source}:{page_start}-{page_end}
    Page-less: region:{source}:singleton:{row_key}
    """
    if page_start is not None and page_end is not None:
        return f"region:{source}:{int(page_start)}-{int(page_end)}"
    if row_key is not None:
        return f"region:{source}:singleton:{row_key}"
    return f"region:{source}:singleton:unknown"


def region_keys_from_retrieval_metadata(retrieval_metadata: dict) -> list:
    """Derive region keys from the metadata dict returned by the search pipeline."""
    keys = []
    for window in retrieval_metadata.get("delivered_page_windows") or []:
        source = window.get("source")
        ps = window.get("page_start")
        pe = window.get("page_end")
        if source and ps is not None and pe is not None:
            keys.append(build_region_key(source, ps, pe))
    for block_key in retrieval_metadata.get("delivered_block_keys") or []:
        if block_key and ":singleton:" in block_key:
            # block:{source}:singleton:{row_key} → region:{source}:singleton:{row_key}
            parts = block_key.split(":", 2)
            if len(parts) == 3:
                rest = parts[2]  # "singleton:{row_key}" (source is in parts[1])
                # but block_key format is "block:source:singleton:row_key"
                # re-split properly
                all_parts = block_key.split(":", 3)
                if len(all_parts) == 4:
                    source = all_parts[1]
                    row_key = all_parts[3]
                    keys.append(build_region_key(source, row_key=row_key))
    return keys


# ---------------------------------------------------------------------------
# Fingerprint helpers
# ---------------------------------------------------------------------------

def _sha1_hex(data: str) -> str:
    return hashlib.sha1(data.encode("utf-8")).hexdigest()[:16]


def build_query_fingerprint(
    normalized_query: str,
    searchable_labels: list,
    requested_evidence_goal: str = "",
) -> str:
    payload = {
        "query": normalized_query or "",
        "labels": sorted(searchable_labels or []),
        "goal": requested_evidence_goal or "",
    }
    return _sha1_hex(json.dumps(payload, sort_keys=True))


def build_result_set_fingerprint(bundle_keys: list, block_keys: list) -> str:
    payload = {
        "bundle_keys": sorted(k for k in (bundle_keys or []) if k),
        "block_keys": sorted(k for k in (block_keys or []) if k),
    }
    return _sha1_hex(json.dumps(payload, sort_keys=True))


def build_evidence_fingerprint(region_keys: list, block_keys: list) -> str:
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
    searchable_labels: list = field(default_factory=list)
    caller_role: str = ""
    caller_phase: str = ""
    caller_round: int = 0
    unresolved_issue: str = ""
    requested_evidence_goal: str = ""
    # Filled in after retrieval
    outcome: str = ""
    linked_evidence_fingerprint: str = ""
    linked_result_set_fingerprint: str = ""


@dataclass
class EvidenceRecord:
    evidence_fingerprint: str
    result_set_fingerprint: str
    delivered_bundle_keys: list = field(default_factory=list)
    delivered_block_keys: list = field(default_factory=list)
    delivered_region_keys: list = field(default_factory=list)
    selected_sources: list = field(default_factory=list)
    net_new: bool = False


@dataclass
class CoverageRecord:
    """Per-source coverage state."""
    # source -> list of (page_start, page_end) integer tuples
    covered_intervals: dict = field(default_factory=dict)
    # Full region key strings for page-less singleton evidence
    covered_singleton_keys: set = field(default_factory=set)


@dataclass
class ScopeState:
    # scope_key -> consecutive no-new-evidence count
    no_new_evidence_counts: dict = field(default_factory=dict)
    exhausted_scope_keys: set = field(default_factory=set)
    last_allowed_reason: str = ""
    last_blocked_reason: str = ""


# ---------------------------------------------------------------------------
# Outcome constants
# ---------------------------------------------------------------------------

OUTCOME_CACHE_HIT = "cache_hit"
OUTCOME_REUSED_KNOWN = "reused_known_evidence"
OUTCOME_NEW_EVIDENCE = "delivered_new_evidence"
OUTCOME_NO_NEW = "no_new_evidence"
OUTCOME_EXHAUSTED = "search_exhausted_for_scope"

# How many consecutive no-new-evidence results exhaust a scope
NO_NEW_EVIDENCE_THRESHOLD = 3

# Reasons that allow retrieval even on an exhausted scope
ALLOWED_REPEAT_REASONS = frozenset({
    "contradiction_check",
    "alternate_source_confirmation",
    "gap_expansion",
    "explicit_gap",
})


# ---------------------------------------------------------------------------
# Gate decision
# ---------------------------------------------------------------------------

@dataclass
class GateDecision:
    allow_search: bool = True
    prefer_net_new_only: bool = False
    allow_overlap_for_reason: str = ""
    scope_exhausted: bool = False
    blocked_reason: str = ""


# ---------------------------------------------------------------------------
# Main EvidencePool
# ---------------------------------------------------------------------------

class EvidencePool:
    """Per-run, in-memory coordination layer for retrieval state.

    The router owns this object; one pool is created per turn (or per
    standalone retrieval call).  It is discarded when the run ends.
    """

    def __init__(self):
        self.query_records: list = []           # list[QueryRecord]
        self.evidence_records: dict = {}        # evidence_fingerprint -> EvidenceRecord
        self.coverage = CoverageRecord()
        self.scope_state = ScopeState()
        self._known_query_fingerprints: set = set()
        self._known_result_set_fingerprints: set = set()
        # Exact-fingerprint cache (replaces old ledger["cache"])
        self._cache: dict = {}

    # ------------------------------------------------------------------
    # Coverage helpers
    # ------------------------------------------------------------------

    def _add_covered_paged_region(self, source: str, ps: int, pe: int) -> bool:
        """Register a paged region as covered. Returns True if net-new."""
        intervals = self.coverage.covered_intervals.get(source, [])
        for existing_ps, existing_pe in intervals:
            if existing_ps <= ps and pe <= existing_pe:
                return False  # fully contained — not net-new
        intervals = list(intervals)
        intervals.append((ps, pe))
        self.coverage.covered_intervals[source] = intervals
        return True

    def _add_covered_region(self, region_key: str) -> bool:
        """Register a region key as covered. Returns True if net-new."""
        if ":singleton:" in region_key:
            if region_key in self.coverage.covered_singleton_keys:
                return False
            self.coverage.covered_singleton_keys.add(region_key)
            return True
        # paged: region:{source}:{ps}-{pe}
        try:
            _, source, page_range = region_key.split(":", 2)
            ps, pe = (int(x) for x in page_range.split("-", 1))
        except (ValueError, AttributeError):
            return False
        return self._add_covered_paged_region(source, ps, pe)

    def _is_region_covered(self, region_key: str) -> bool:
        if ":singleton:" in region_key:
            return region_key in self.coverage.covered_singleton_keys
        try:
            _, source, page_range = region_key.split(":", 2)
            ps, pe = (int(x) for x in page_range.split("-", 1))
        except (ValueError, AttributeError):
            return False
        for existing_ps, existing_pe in self.coverage.covered_intervals.get(source, []):
            if existing_ps <= ps and pe <= existing_pe:
                return True
        return False

    def known_covered_region_keys(self) -> list:
        """Return all currently covered region keys."""
        keys = list(self.coverage.covered_singleton_keys)
        for source, intervals in self.coverage.covered_intervals.items():
            for ps, pe in intervals:
                keys.append(f"region:{source}:{ps}-{pe}")
        return keys

    def coverage_as_excluded_page_windows(self) -> list:
        """Convert paged coverage to the excluded_page_windows format expected by the pipeline."""
        windows = []
        for source, intervals in self.coverage.covered_intervals.items():
            for ps, pe in intervals:
                windows.append({
                    "key": f"window:{source}:{ps}-{pe}",
                    "source": source,
                    "page_start": ps,
                    "page_end": pe,
                })
        return windows

    def coverage_as_excluded_block_keys(self) -> list:
        """Convert singleton coverage to excluded_block_keys format (sorted)."""
        block_keys = []
        for region_key in self.coverage.covered_singleton_keys:
            # region:{source}:singleton:{row_key} → block:{source}:singleton:{row_key}
            if region_key.startswith("region:"):
                block_key = "block:" + region_key[len("region:"):]
                block_keys.append(block_key)
        return sorted(block_keys)

    # ------------------------------------------------------------------
    # Scope-key helper
    # ------------------------------------------------------------------

    def _scope_key(self, caller_role: str, searchable_labels: list) -> str:
        role_part = caller_role or "any"
        labels_part = ",".join(sorted(searchable_labels or [])) or "all"
        return f"{role_part}:{labels_part}"

    # ------------------------------------------------------------------
    # Query and evidence recording
    # ------------------------------------------------------------------

    def record_query(
        self,
        raw_query: str,
        searchable_labels: list,
        caller_role: str = "",
        caller_phase: str = "",
        caller_round: int = 0,
        unresolved_issue: str = "",
        requested_evidence_goal: str = "",
    ) -> "QueryRecord":
        """Create and register a QueryRecord before retrieval executes."""
        normalized = (raw_query or "").strip().lower()
        qfp = build_query_fingerprint(normalized, searchable_labels, requested_evidence_goal)
        record = QueryRecord(
            query_fingerprint=qfp,
            raw_query=raw_query or "",
            normalized_query=normalized,
            searchable_labels=list(searchable_labels or []),
            caller_role=caller_role,
            caller_phase=caller_phase,
            caller_round=caller_round,
            unresolved_issue=unresolved_issue,
            requested_evidence_goal=requested_evidence_goal,
        )
        self._known_query_fingerprints.add(qfp)
        self.query_records.append(record)
        return record

    def record_evidence_from_result(
        self,
        retrieval_result: dict,
        query_record: "QueryRecord",
        *,
        is_cache_hit: bool = False,
    ) -> "EvidenceRecord | None":
        """Classify outcome and register coverage from the pipeline result.

        Mutates *query_record* to fill in outcome and linked fingerprints.
        Returns the EvidenceRecord (or None for cache hits where the record
        already exists).
        """
        metadata = retrieval_result.get("retrieval_metadata") or {}
        bundle_keys = list(metadata.get("delivered_bundle_keys") or [])
        block_keys = list(metadata.get("delivered_block_keys") or [])
        region_keys = region_keys_from_retrieval_metadata(metadata)
        selected_sources = list(retrieval_result.get("selected_sources") or [])

        rsfp = build_result_set_fingerprint(bundle_keys, block_keys)
        efp = build_evidence_fingerprint(region_keys, block_keys)

        query_record.linked_result_set_fingerprint = rsfp
        query_record.linked_evidence_fingerprint = efp

        if is_cache_hit:
            query_record.outcome = OUTCOME_CACHE_HIT
            return self.evidence_records.get(efp)

        # Only check reused-known when there is actual evidence in the result.
        # Empty results (no bundles, no blocks) always go through the no-new-evidence
        # counter so they contribute to scope exhaustion tracking.
        has_evidence = bool(bundle_keys or block_keys or region_keys)
        if has_evidence and rsfp in self._known_result_set_fingerprints:
            query_record.outcome = OUTCOME_REUSED_KNOWN
            return self.evidence_records.get(efp)

        # Measure net-new coverage
        net_new_keys = [rk for rk in region_keys if self._add_covered_region(rk)]

        if not region_keys or not net_new_keys:
            scope_key = self._scope_key(query_record.caller_role, query_record.searchable_labels)
            count = self.scope_state.no_new_evidence_counts.get(scope_key, 0) + 1
            self.scope_state.no_new_evidence_counts[scope_key] = count
            if count >= NO_NEW_EVIDENCE_THRESHOLD:
                self.scope_state.exhausted_scope_keys.add(scope_key)
                query_record.outcome = OUTCOME_EXHAUSTED
            else:
                query_record.outcome = OUTCOME_NO_NEW
        else:
            query_record.outcome = OUTCOME_NEW_EVIDENCE

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
        searchable_labels: list,
        caller_role: str = "",
        repeat_reason: str = "",
        requested_evidence_goal: str = "",
    ) -> "GateDecision":
        """Decide whether this retrieval should proceed.

        Called *before* the query record is created and before retrieval runs.
        Only blocks when the caller is a MAGI role (caller_role is set) and
        the scope is exhausted without a legitimate repeat reason.
        """
        normalized = (raw_query or "").strip().lower()
        qfp = build_query_fingerprint(normalized, searchable_labels, requested_evidence_goal)
        scope_key = self._scope_key(caller_role, searchable_labels)

        # Legitimate repeat reasons always get through
        if repeat_reason in ALLOWED_REPEAT_REASONS:
            self.scope_state.last_allowed_reason = repeat_reason
            return GateDecision(
                allow_search=True,
                allow_overlap_for_reason=repeat_reason,
            )

        # Block exhausted scopes only when a MAGI role is identified
        if caller_role and scope_key in self.scope_state.exhausted_scope_keys:
            reason = f"scope exhausted for {scope_key}"
            self.scope_state.last_blocked_reason = reason
            return GateDecision(
                allow_search=False,
                scope_exhausted=True,
                blocked_reason=reason,
            )

        # Prefer net-new on repeated query fingerprints
        prefer_net_new = qfp in self._known_query_fingerprints

        return GateDecision(
            allow_search=True,
            prefer_net_new_only=prefer_net_new,
        )

    # ------------------------------------------------------------------
    # Prompt summary
    # ------------------------------------------------------------------

    def build_prompt_summary(self, max_regions: int = 5) -> str:
        """Return a short EVIDENCE POOL SUMMARY block for MAGI prompts."""
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
            if last.unresolved_issue:
                lines.append(f"  Unresolved evidence gap: {last.unresolved_issue}")

        if self.scope_state.exhausted_scope_keys:
            exhausted = sorted(self.scope_state.exhausted_scope_keys)
            lines.append(f"  Exhausted scopes: {', '.join(exhausted)}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def summary_event_payload(self) -> dict:
        """Dict suitable for emission as an observability event."""
        return {
            "query_count": len(self.query_records),
            "evidence_count": len(self.evidence_records),
            "covered_region_count": len(self.known_covered_region_keys()),
            "exhausted_scope_keys": sorted(self.scope_state.exhausted_scope_keys),
            "last_outcome": self.query_records[-1].outcome if self.query_records else "",
        }

    def last_query_outcome(self) -> str:
        if self.query_records:
            return self.query_records[-1].outcome
        return ""
