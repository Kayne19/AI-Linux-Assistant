"""Document-level scope selection for pre-narrowed hybrid search (T12).

Retrieval now has a structured ``documents`` table (populated by T11). This
module picks candidate documents BEFORE the chunk-level hybrid search runs so
mixed-family corpora (Debian guide, Arch wiki, Proxmox admin) do not
cross-contaminate when chunks look semantically similar.

Shape:

1. Build a :class:`ScopeHint` from router hints + query-entity extraction +
   memory-derived scope bits.
2. Call :func:`select_candidate_docs` with the hint and the ``documents``
   table rows. The returned list is ranked by trust tier, freshness, and
   match strength against the requested OS family / package manager /
   subsystem / init system.
3. The caller runs its hybrid search with a ``canonical_source_id IN
   (...)`` predicate. If the scoped result is weak, call
   :func:`widen_hint` once to relax one constraint and retry.

No mutation of the documents table happens here — this is a pure reader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Tier weightings
# ---------------------------------------------------------------------------

_TRUST_WEIGHTS: dict[str, float] = {
    "canonical": 4.0,
    "official": 3.0,
    "community": 2.0,
    "unofficial": 1.0,
    "unknown": 0.5,
}

_FRESHNESS_WEIGHTS: dict[str, float] = {
    "current": 4.0,
    "supported": 3.0,
    "legacy": 2.0,
    "deprecated": 1.0,
    "archived": 0.5,
    "unknown": 1.0,
}

# Fields considered when tier-ranking a candidate. Order matters: the first
# field whose hint-value is None is also the first field widen_hint drops.
_SCORING_FIELDS: tuple[str, ...] = (
    "package_managers",   # most specific
    "init_systems",
    "major_subsystems",
    "os_family",
    "source_family",      # least specific
)


# ---------------------------------------------------------------------------
# Public data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeHint:
    """What the caller knows (or suspects) about the query's scope.

    All fields are optional. None means "no preference on this axis".
    Empty list == an explicit "no preference" too. ``explicit_doc_ids``
    short-circuits selection entirely and is used when the router names a
    specific document.
    """

    os_family: str | None = None
    source_family: str | None = None
    package_managers: tuple[str, ...] = ()
    init_systems: tuple[str, ...] = ()
    major_subsystems: tuple[str, ...] = ()
    explicit_doc_ids: tuple[str, ...] = ()


@dataclass
class ScopedCandidate:
    """One candidate document with its computed ranking score."""

    canonical_source_id: str
    canonical_title: str
    score: float
    matched_fields: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Widening ladder
# ---------------------------------------------------------------------------


def widen_hint(hint: ScopeHint, step: int = 1) -> ScopeHint:
    """Drop the most-specific constraints to broaden the candidate set.

    Step 1 drops package_managers, step 2 also drops init_systems, step 3
    also drops major_subsystems, step 4 drops os_family, step 5 drops
    source_family. Beyond that the hint is fully widened to an empty one.
    """
    if step <= 0:
        return hint

    # Use an ordered list matching _SCORING_FIELDS.
    updates: dict = {}
    if step >= 1:
        updates["package_managers"] = ()
    if step >= 2:
        updates["init_systems"] = ()
    if step >= 3:
        updates["major_subsystems"] = ()
    if step >= 4:
        updates["os_family"] = None
    if step >= 5:
        updates["source_family"] = None
    return ScopeHint(
        os_family=updates.get("os_family", hint.os_family),
        source_family=updates.get("source_family", hint.source_family),
        package_managers=updates.get("package_managers", hint.package_managers),
        init_systems=updates.get("init_systems", hint.init_systems),
        major_subsystems=updates.get("major_subsystems", hint.major_subsystems),
        explicit_doc_ids=hint.explicit_doc_ids,
    )


# ---------------------------------------------------------------------------
# Query-entity extraction
# ---------------------------------------------------------------------------

_PACKAGE_MANAGER_HINTS = {
    "apt": "apt", "apt-get": "apt", "dpkg": "dpkg", "rpm": "rpm",
    "dnf": "dnf", "yum": "dnf", "pacman": "pacman", "portage": "portage",
    "emerge": "portage", "apk": "apk", "nix": "nix", "nix-env": "nix",
    "brew": "brew",
}

_INIT_SYSTEM_HINTS = {
    "systemd": "systemd", "systemctl": "systemd", "journalctl": "systemd",
    "openrc": "openrc", "sysv": "sysv", "init.d": "sysv", "runit": "runit",
    "launchd": "launchd", "launchctl": "launchd",
}

_SUBSYSTEM_HINTS = {
    "docker": "containers", "podman": "containers", "kubectl": "containers",
    "zfs": "filesystems", "btrfs": "filesystems", "ext4": "filesystems",
    "lvm": "storage", "raid": "storage",
    "iptables": "networking", "nftables": "networking", "firewalld": "networking",
    "selinux": "security", "apparmor": "security",
    "grub": "boot", "efibootmgr": "boot",
    "qemu": "virtualization", "libvirt": "virtualization", "kvm": "virtualization",
}


def extract_scope_signals_from_query(query: str) -> dict[str, list[str]]:
    """Pull package_managers / init_systems / major_subsystems hints from *query*.

    Case-insensitive word-boundary matching against a small curated dictionary.
    Deliberately conservative — misses are better than confidently-wrong hints.
    """
    lowered = query.lower()
    found_pm: list[str] = []
    found_init: list[str] = []
    found_subs: list[str] = []

    def _scan(table: dict[str, str], bucket: list[str]) -> None:
        for needle, canonical in table.items():
            pattern = r"\b" + re.escape(needle) + r"\b"
            if re.search(pattern, lowered):
                if canonical not in bucket:
                    bucket.append(canonical)

    _scan(_PACKAGE_MANAGER_HINTS, found_pm)
    _scan(_INIT_SYSTEM_HINTS, found_init)
    _scan(_SUBSYSTEM_HINTS, found_subs)

    return {
        "package_managers": found_pm,
        "init_systems": found_init,
        "major_subsystems": found_subs,
    }


def build_hint(
    *,
    query: str | None = None,
    router_hint: dict | None = None,
    explicit_doc_ids: tuple[str, ...] = (),
) -> ScopeHint:
    """Merge router-provided hints with query-entity extraction.

    Router hints win over query extraction: if the router names an OS family,
    the extractor's OS guesses are ignored on that axis.
    """
    router = router_hint or {}
    derived = extract_scope_signals_from_query(query or "")

    def _first_nonempty(*candidates):
        for c in candidates:
            if c:
                return c
        return None

    def _merged(router_key: str, query_key: str) -> tuple[str, ...]:
        router_value = router.get(router_key) or []
        query_value = derived.get(query_key) or []
        merged: list[str] = []
        for v in list(router_value) + list(query_value):
            if v and v not in merged:
                merged.append(v)
        return tuple(merged)

    return ScopeHint(
        os_family=_first_nonempty(router.get("os_family")),
        source_family=_first_nonempty(router.get("source_family")),
        package_managers=_merged("package_managers", "package_managers"),
        init_systems=_merged("init_systems", "init_systems"),
        major_subsystems=_merged("major_subsystems", "major_subsystems"),
        explicit_doc_ids=tuple(explicit_doc_ids),
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def _field_match_strength(doc_value, hint_value) -> float:
    """Return a [0.0, 1.0] match score for a single field."""
    if hint_value in (None, "", (), []):
        return 0.0
    hint_list = list(hint_value) if isinstance(hint_value, (list, tuple)) else [hint_value]
    hint_set = {str(v).lower() for v in hint_list if v not in (None, "")}
    if not hint_set:
        return 0.0
    doc_values = {str(v).lower() for v in _as_list(doc_value) if v not in (None, "")}
    if not doc_values:
        return 0.0
    overlap = hint_set & doc_values
    if not overlap:
        return 0.0
    return len(overlap) / len(hint_set)


def _weighted_field_score(doc: dict, hint: ScopeHint, field: str) -> tuple[float, bool]:
    """Return (score_contribution, matched?) for *field*.

    Matched means the hint is non-empty for that field AND overlaps the doc.
    Scoring fields contribute with descending weight — the first field in
    ``_SCORING_FIELDS`` gets the largest coefficient.
    """
    weight = len(_SCORING_FIELDS) - _SCORING_FIELDS.index(field)
    strength = _field_match_strength(
        doc.get(field),
        {
            "package_managers": hint.package_managers,
            "init_systems": hint.init_systems,
            "major_subsystems": hint.major_subsystems,
            "os_family": hint.os_family,
            "source_family": hint.source_family,
        }[field],
    )
    return weight * strength, strength > 0


def _trust_score(doc: dict) -> float:
    return _TRUST_WEIGHTS.get((doc.get("trust_tier") or "unknown"), _TRUST_WEIGHTS["unknown"])


def _freshness_score(doc: dict) -> float:
    return _FRESHNESS_WEIGHTS.get(
        (doc.get("freshness_status") or "unknown"), _FRESHNESS_WEIGHTS["unknown"]
    )


def score_doc(doc: dict, hint: ScopeHint) -> ScopedCandidate:
    """Compute the tier-ranking score for *doc* under *hint*."""
    field_total = 0.0
    matched: list[str] = []
    for field in _SCORING_FIELDS:
        contribution, did_match = _weighted_field_score(doc, hint, field)
        field_total += contribution
        if did_match:
            matched.append(field)
    total = field_total + _trust_score(doc) + _freshness_score(doc)
    return ScopedCandidate(
        canonical_source_id=doc.get("canonical_source_id", ""),
        canonical_title=doc.get("canonical_title", "") or doc.get("canonical_source_id", ""),
        score=total,
        matched_fields=matched,
    )


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------


def select_candidate_docs(
    hint: ScopeHint,
    documents: list[dict],
    *,
    limit: int | None = None,
    min_match_strength: float = 0.0,
) -> list[ScopedCandidate]:
    """Pick + rank candidate documents for *hint*.

    Rules:
    - If ``explicit_doc_ids`` is non-empty, return only those docs (in that
      order), scored against the hint.
    - If *hint* has no scope constraints at all, return every doc sorted by
      trust+freshness (useful for a global fallback path).
    - Otherwise filter to docs whose total ``matched_fields`` count is
      greater than ``min_match_strength``, then sort by descending score
      (ties broken by trust > freshness > canonical_source_id).
    """
    if hint.explicit_doc_ids:
        index = {doc.get("canonical_source_id"): doc for doc in documents}
        out: list[ScopedCandidate] = []
        for doc_id in hint.explicit_doc_ids:
            doc = index.get(doc_id)
            if doc is None:
                continue
            out.append(score_doc(doc, hint))
        return out

    constraints_present = any(
        [
            hint.os_family, hint.source_family,
            hint.package_managers, hint.init_systems, hint.major_subsystems,
        ]
    )

    candidates = [score_doc(doc, hint) for doc in documents]

    if constraints_present:
        candidates = [c for c in candidates if len(c.matched_fields) > min_match_strength]

    def _sort_key(c: ScopedCandidate):
        return (-c.score, -len(c.matched_fields), c.canonical_source_id)

    candidates.sort(key=_sort_key)
    if limit is not None:
        candidates = candidates[:limit]
    return candidates


def should_widen(
    candidates: list[ScopedCandidate],
    *,
    min_hit_count: int,
    min_top_score: float,
) -> bool:
    """Decide whether to widen the hint and retry.

    Widen when either the candidate count is too low or the best score falls
    below the configured floor.
    """
    if len(candidates) < min_hit_count:
        return True
    if not candidates:
        return True
    return candidates[0].score < min_top_score
