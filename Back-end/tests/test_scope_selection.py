"""Tests for the T12 retrieval scope selector.

Covers:
- build_hint merges router hints with query-entity extraction
- extract_scope_signals_from_query finds pkg managers, init systems, subsystems
- score_doc weights matching fields over trust and freshness correctly
- select_candidate_docs filters + orders by score
- explicit_doc_ids short-circuits selection
- widen_hint drops constraints in the documented order
- should_widen correctly gates on top score + hit count
"""

from retrieval.scope import (
    ScopeHint,
    ScopedCandidate,
    build_hint,
    extract_scope_signals_from_query,
    score_doc,
    select_candidate_docs,
    should_widen,
    widen_hint,
)


def _doc(**kwargs) -> dict:
    base = {
        "canonical_source_id": kwargs.pop("id", "id-x"),
        "canonical_title": kwargs.pop("title", "Doc"),
        "source_family": "linux_generic",
        "vendor_or_project": "unknown",
        "doc_kind": "other",
        "trust_tier": "unknown",
        "freshness_status": "unknown",
        "os_family": "unknown",
        "init_systems": ["unknown"],
        "package_managers": ["unknown"],
        "major_subsystems": [],
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# extract_scope_signals_from_query
# ---------------------------------------------------------------------------


def test_extract_scope_signals_finds_package_managers_case_insensitive():
    signals = extract_scope_signals_from_query("How do I use APT to install nginx?")
    assert "apt" in signals["package_managers"]


def test_extract_scope_signals_maps_apt_get_and_yum_aliases():
    signals = extract_scope_signals_from_query("apt-get remove x and yum install y")
    assert "apt" in signals["package_managers"]
    assert "dnf" in signals["package_managers"]


def test_extract_scope_signals_detects_init_and_subsystem():
    signals = extract_scope_signals_from_query("systemctl restart docker on boot")
    assert "systemd" in signals["init_systems"]
    assert "containers" in signals["major_subsystems"]
    assert "boot" not in signals["major_subsystems"]  # only via grub/efibootmgr


def test_extract_scope_signals_empty_query():
    signals = extract_scope_signals_from_query("")
    assert signals == {"package_managers": [], "init_systems": [], "major_subsystems": []}


def test_extract_scope_signals_word_boundary_rejects_substrings():
    signals = extract_scope_signals_from_query("captain apprehensive")
    assert "apt" not in signals["package_managers"]


# ---------------------------------------------------------------------------
# build_hint
# ---------------------------------------------------------------------------


def test_build_hint_merges_router_and_query():
    hint = build_hint(
        query="systemctl on arch using pacman",
        router_hint={"os_family": "linux", "source_family": "arch"},
    )
    assert hint.os_family == "linux"
    assert hint.source_family == "arch"
    assert "pacman" in hint.package_managers
    assert "systemd" in hint.init_systems


def test_build_hint_router_hint_wins_on_scalar_fields():
    # Even if query implies a different OS, router hint is authoritative.
    hint = build_hint(query="launchctl on Mac", router_hint={"os_family": "linux"})
    assert hint.os_family == "linux"


def test_build_hint_preserves_explicit_doc_ids():
    hint = build_hint(explicit_doc_ids=("a", "b"))
    assert hint.explicit_doc_ids == ("a", "b")


def test_build_hint_scalar_string_in_router_list_field_is_treated_as_one_value():
    # Regression: list("apt") splits into ['a','p','t'] — we must wrap scalar
    # strings before iterating.
    hint = build_hint(router_hint={"package_managers": "apt"})
    assert hint.package_managers == ("apt",)


# ---------------------------------------------------------------------------
# score_doc
# ---------------------------------------------------------------------------


def test_score_doc_higher_for_more_matches():
    hint = ScopeHint(
        os_family="linux", package_managers=("apt",), init_systems=("systemd",)
    )
    low = _doc(os_family="linux", package_managers=["apt"], init_systems=["sysv"])
    high = _doc(os_family="linux", package_managers=["apt"], init_systems=["systemd"])
    assert score_doc(high, hint).score > score_doc(low, hint).score


def test_score_doc_counts_trust_and_freshness():
    hint = ScopeHint()
    current_canonical = _doc(trust_tier="canonical", freshness_status="current")
    archived_unofficial = _doc(trust_tier="unofficial", freshness_status="archived")
    assert score_doc(current_canonical, hint).score > score_doc(archived_unofficial, hint).score


def test_score_doc_reports_matched_fields():
    hint = ScopeHint(os_family="linux", package_managers=("apt",))
    doc = _doc(os_family="linux", package_managers=["apt"])
    cand = score_doc(doc, hint)
    assert set(cand.matched_fields) == {"os_family", "package_managers"}


# ---------------------------------------------------------------------------
# select_candidate_docs
# ---------------------------------------------------------------------------


def test_select_candidate_docs_filters_by_match_when_constraints_present():
    hint = ScopeHint(package_managers=("apt",))
    docs = [
        _doc(id="deb", package_managers=["apt"]),
        _doc(id="arch", package_managers=["pacman"]),
    ]
    out = select_candidate_docs(hint, docs)
    assert [c.canonical_source_id for c in out] == ["deb"]


def test_select_candidate_docs_no_constraints_returns_all_sorted_by_tier():
    hint = ScopeHint()
    docs = [
        _doc(id="weak", trust_tier="unofficial", freshness_status="archived"),
        _doc(id="strong", trust_tier="canonical", freshness_status="current"),
    ]
    out = select_candidate_docs(hint, docs)
    assert [c.canonical_source_id for c in out] == ["strong", "weak"]


def test_select_candidate_docs_explicit_doc_ids_short_circuits():
    hint = ScopeHint(
        explicit_doc_ids=("doc_b",),
        package_managers=("apt",),  # would normally filter
    )
    docs = [
        _doc(id="doc_a", package_managers=["apt"]),
        _doc(id="doc_b", package_managers=["pacman"]),
    ]
    out = select_candidate_docs(hint, docs)
    assert [c.canonical_source_id for c in out] == ["doc_b"]


def test_select_candidate_docs_limit_caps_results():
    hint = ScopeHint()
    docs = [_doc(id=f"d{i}", trust_tier="canonical") for i in range(5)]
    out = select_candidate_docs(hint, docs, limit=2)
    assert len(out) == 2


def test_select_candidate_docs_tiebreak_is_stable_by_id():
    hint = ScopeHint()
    docs = [
        _doc(id="zed", trust_tier="canonical", freshness_status="current"),
        _doc(id="alpha", trust_tier="canonical", freshness_status="current"),
    ]
    out = select_candidate_docs(hint, docs)
    assert [c.canonical_source_id for c in out] == ["alpha", "zed"]


# ---------------------------------------------------------------------------
# widen_hint
# ---------------------------------------------------------------------------


def test_widen_hint_step_1_drops_package_managers():
    hint = ScopeHint(
        os_family="linux", package_managers=("apt",), init_systems=("systemd",),
        major_subsystems=("containers",), source_family="debian",
    )
    widened = widen_hint(hint, step=1)
    assert widened.package_managers == ()
    assert widened.init_systems == ("systemd",)
    assert widened.os_family == "linux"


def test_widen_hint_step_3_drops_subsystems_too():
    hint = ScopeHint(
        os_family="linux", package_managers=("apt",), init_systems=("systemd",),
        major_subsystems=("containers",), source_family="debian",
    )
    widened = widen_hint(hint, step=3)
    assert widened.package_managers == ()
    assert widened.init_systems == ()
    assert widened.major_subsystems == ()
    assert widened.os_family == "linux"


def test_widen_hint_step_5_empties_almost_everything():
    hint = ScopeHint(
        os_family="linux", package_managers=("apt",), init_systems=("systemd",),
        major_subsystems=("containers",), source_family="debian",
        explicit_doc_ids=("pinned",),
    )
    widened = widen_hint(hint, step=5)
    assert widened.os_family is None
    assert widened.source_family is None
    assert widened.explicit_doc_ids == ("pinned",)  # not widened-away


def test_widen_hint_step_2_drops_init_systems():
    hint = ScopeHint(
        os_family="linux", package_managers=("apt",), init_systems=("systemd",),
        major_subsystems=("containers",), source_family="debian",
    )
    widened = widen_hint(hint, step=2)
    assert widened.package_managers == ()
    assert widened.init_systems == ()
    assert widened.major_subsystems == ("containers",)
    assert widened.os_family == "linux"
    assert widened.source_family == "debian"


def test_widen_hint_step_4_drops_os_family():
    hint = ScopeHint(
        os_family="linux", package_managers=("apt",), init_systems=("systemd",),
        major_subsystems=("containers",), source_family="debian",
    )
    widened = widen_hint(hint, step=4)
    assert widened.os_family is None
    assert widened.source_family == "debian"
    assert widened.major_subsystems == ()


def test_widen_hint_step_zero_is_identity():
    hint = ScopeHint(os_family="linux")
    assert widen_hint(hint, step=0) is hint


# ---------------------------------------------------------------------------
# should_widen
# ---------------------------------------------------------------------------


def test_should_widen_triggers_on_too_few_candidates():
    candidates = [ScopedCandidate(canonical_source_id="a", canonical_title="A", score=10.0)]
    assert should_widen(candidates, min_hit_count=3, min_top_score=1.0) is True


def test_should_widen_triggers_on_weak_top_score():
    candidates = [
        ScopedCandidate(canonical_source_id="a", canonical_title="A", score=0.1),
        ScopedCandidate(canonical_source_id="b", canonical_title="B", score=0.1),
        ScopedCandidate(canonical_source_id="c", canonical_title="C", score=0.1),
    ]
    assert should_widen(candidates, min_hit_count=3, min_top_score=2.0) is True


def test_should_widen_stays_when_scope_is_good():
    candidates = [
        ScopedCandidate(canonical_source_id="a", canonical_title="A", score=5.0),
        ScopedCandidate(canonical_source_id="b", canonical_title="B", score=4.0),
        ScopedCandidate(canonical_source_id="c", canonical_title="C", score=3.0),
    ]
    assert should_widen(candidates, min_hit_count=3, min_top_score=2.0) is False


def test_should_widen_on_empty_candidates():
    assert should_widen([], min_hit_count=3, min_top_score=2.0) is True
