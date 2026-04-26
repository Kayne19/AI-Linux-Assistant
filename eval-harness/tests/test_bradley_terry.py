from __future__ import annotations

import math
import random
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eval_harness.orchestration.bradley_terry import bootstrap_bradley_terry, fit_bradley_terry


def test_deterministic_ranking_a_beats_b_beats_c() -> None:
    # A always beats B, B always beats C → expected A > B > C.
    pairs = [
        ("A", "B", 1.0),
        ("A", "B", 1.0),
        ("A", "B", 1.0),
        ("B", "C", 1.0),
        ("B", "C", 1.0),
        ("B", "C", 1.0),
        ("A", "C", 1.0),
        ("A", "C", 1.0),
    ]
    ratings = fit_bradley_terry(pairs)
    assert ratings["A"] > ratings["B"] > ratings["C"]


def test_all_ties_ratings_near_zero() -> None:
    # Equal fractional wins for everyone → all ratings ≈ 0.
    pairs = [
        ("A", "B", 0.5),
        ("A", "B", 0.5),
        ("B", "C", 0.5),
        ("B", "C", 0.5),
        ("A", "C", 0.5),
        ("A", "C", 0.5),
    ]
    ratings = fit_bradley_terry(pairs)
    for r in ratings.values():
        assert abs(r) < 0.5, f"Expected near-zero rating, got {r}"


def test_fractional_weights_handled() -> None:
    # A gets 0.7 fractional win over B across several matches.
    pairs = [("A", "B", 0.7)] * 10
    ratings = fit_bradley_terry(pairs)
    assert ratings["A"] > ratings["B"]


def test_empty_pairs_returns_empty() -> None:
    assert fit_bradley_terry([]) == {}


def test_single_player_pair() -> None:
    # Only two players: A beats B consistently.
    ratings = fit_bradley_terry([("A", "B", 1.0), ("A", "B", 1.0)])
    assert "A" in ratings and "B" in ratings
    assert ratings["A"] > ratings["B"]


def test_bootstrap_ci_bounds_sane() -> None:
    rng = random.Random(7)
    groups = [
        [("A", "B", 1.0), ("B", "C", 1.0)],
        [("A", "B", 1.0), ("B", "C", 1.0)],
        [("A", "C", 1.0)],
    ]
    result = bootstrap_bradley_terry(groups, bootstrap_samples=100, confidence=0.95, rng=rng)
    for subj, stats in result.items():
        assert "rating" in stats
        assert "ci_low" in stats
        assert "ci_high" in stats
        assert stats["ci_low"] <= stats["rating"] <= stats["ci_high"], (
            f"{subj}: ci_low={stats['ci_low']} rating={stats['rating']} ci_high={stats['ci_high']}"
        )


def test_bootstrap_empty_groups() -> None:
    result = bootstrap_bradley_terry([])
    assert result == {}


def test_geomean_normalised_to_zero() -> None:
    pairs = [("A", "B", 1.0), ("B", "C", 1.0), ("A", "C", 1.0)]
    ratings = fit_bradley_terry(pairs)
    log_geomean = sum(ratings.values()) / len(ratings)
    assert abs(log_geomean) < 1e-6
