"""Tests for H5 atomic registry writes in routing_registry.py."""

import json
import threading
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_registry(tmp_path):
    """Create a fresh empty registry file for a test, returning its path."""
    registry_path = tmp_path / "routing_domains.json"

    default_mini = {
        "domains": [
            {
                "label": "debian",
                "aliases": ["debian", "apt"],
                "description": "Debian stuff.",
                "skip_rag": False,
                "builtin": True,
            },
        ]
    }
    from orchestration import routing_registry as mod

    with mock.patch.object(mod, "REGISTRY_PATH", registry_path):
        with mock.patch.object(mod, "DEFAULT_REGISTRY", default_mini):
            mod.save_registry(default_mini)
            yield mod


def _merge_worker(
    mod, label: str, alias: str, results: list, barrier: threading.Barrier
):
    """Thread target: wait at barrier, then merge, then record result."""
    barrier.wait()
    changed, msg = mod.merge_domain_suggestion({"label": label, "aliases": [alias]})
    results.append((label, alias, changed, msg))


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def test_save_registry_is_atomic(isolated_registry):
    """save_registry writes via temp file + os.replace, no temp debris."""
    mod = isolated_registry
    registry = {
        "domains": [
            {
                "label": "test",
                "aliases": ["test"],
                "description": "Atomic test.",
                "skip_rag": False,
                "builtin": False,
            }
        ]
    }
    mod.save_registry(registry)

    assert mod.REGISTRY_PATH.exists()
    content = json.loads(mod.REGISTRY_PATH.read_text())
    assert content["domains"][0]["label"] == "test"
    # No temp files left behind
    temps = list(mod.REGISTRY_PATH.parent.glob(".tmp-routing_domains-*"))
    assert len(temps) == 0


# ---------------------------------------------------------------------------
# Basic merge operations
# ---------------------------------------------------------------------------


def test_merge_domain_suggestion_adds_new_domain(isolated_registry):
    mod = isolated_registry
    changed, msg = mod.merge_domain_suggestion(
        {"label": "arch", "aliases": ["arch", "pacman"], "description": "Arch Linux."}
    )
    assert changed is True
    assert "added" in msg

    domains = mod.get_domains()
    labels = {d["label"] for d in domains}
    assert "arch" in labels


def test_merge_domain_suggestion_updates_existing_domain(isolated_registry):
    mod = isolated_registry
    changed, msg = mod.merge_domain_suggestion(
        {"label": "debian", "aliases": ["apt-get"], "description": "New desc."}
    )
    assert changed is True
    assert "updated" in msg

    domain = mod.get_domain_map()["debian"]
    assert "apt-get" in domain["aliases"]


# ---------------------------------------------------------------------------
# Concurrent writes — no last-write-wins loss
# ---------------------------------------------------------------------------


def test_concurrent_merges_both_land_no_last_write_wins(isolated_registry):
    """Two parallel merge_domain_suggestion calls — both updates must survive."""
    mod = isolated_registry
    results: list = []
    barrier = threading.Barrier(2, timeout=10)

    t1 = threading.Thread(
        target=_merge_worker,
        args=(mod, "debian", "aptitude", results, barrier),
    )
    t2 = threading.Thread(
        target=_merge_worker,
        args=(mod, "debian", "synaptic", results, barrier),
    )

    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(results) == 2, f"Expected 2 results, got {len(results)}"
    for r in results:
        assert r[2], f"Merge failed: {r}"

    # Both aliases should be present — no last-write-wins loss
    domain = mod.get_domain_map()["debian"]
    assert "aptitude" in domain["aliases"], f"aptitude missing from {domain['aliases']}"
    assert "synaptic" in domain["aliases"], f"synaptic missing from {domain['aliases']}"


def test_concurrent_new_domains_both_land_no_last_write_wins(isolated_registry):
    """Two parallel merges adding different new domains — both must be present."""
    mod = isolated_registry
    results: list = []
    barrier = threading.Barrier(2, timeout=10)

    t1 = threading.Thread(
        target=_merge_worker,
        args=(mod, "fedora", "dnf", results, barrier),
    )
    t2 = threading.Thread(
        target=_merge_worker,
        args=(mod, "opensuse", "zypper", results, barrier),
    )

    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(results) == 2, f"Expected 2 results, got {len(results)}"
    labels = {d["label"] for d in mod.get_domains()}
    assert "fedora" in labels
    assert "opensuse" in labels
