from __future__ import annotations

import math
import random as _random
from collections.abc import Sequence
from typing import Any


def fit_bradley_terry(
    pairs: Sequence[tuple[str, str, float]],
    *,
    iterations: int = 200,
    tol: float = 1e-6,
) -> dict[str, float]:
    """MM iteration (Hunter 2004). Returns log-ratings normalised so geomean=0."""
    players: set[str] = set()
    for winner, loser, _ in pairs:
        players.add(winner)
        players.add(loser)
    if not players:
        return {}

    player_list = sorted(players)
    pi: dict[str, float] = {p: 1.0 for p in player_list}

    wins: dict[str, float] = {p: 0.0 for p in player_list}
    n_contest: dict[tuple[str, str], float] = {}

    for winner, loser, weight in pairs:
        wins[winner] += weight
        wins[loser] += 1.0 - weight
        key_ab = (winner, loser)
        key_ba = (loser, winner)
        n_contest[key_ab] = n_contest.get(key_ab, 0.0) + 1.0
        n_contest[key_ba] = n_contest.get(key_ba, 0.0) + 1.0

    for _ in range(iterations):
        pi_new: dict[str, float] = {}
        max_delta = 0.0
        for p in player_list:
            denom = 0.0
            for q in player_list:
                if q == p:
                    continue
                n_pq = n_contest.get((p, q), 0.0)
                if n_pq > 0.0:
                    denom += n_pq / (pi[p] + pi[q])
            if denom == 0.0 or wins[p] == 0.0:
                pi_new[p] = pi[p]
            else:
                pi_new[p] = wins[p] / denom
            max_delta = max(max_delta, abs(pi_new[p] - pi[p]))
        pi = pi_new
        if max_delta < tol:
            break

    log_geo_mean = sum(math.log(max(v, 1e-12)) for v in pi.values()) / len(pi)
    return {p: math.log(max(pi[p], 1e-12)) - log_geo_mean for p in player_list}


def bootstrap_bradley_terry(
    grouped_pairs: Sequence[Sequence[tuple[str, str, float]]],
    *,
    bootstrap_samples: int = 200,
    confidence: float = 0.95,
    rng: _random.Random | None = None,
) -> dict[str, dict[str, Any]]:
    """Bootstrap BT ratings by resampling whole scenario groups with replacement.

    Returns {subject: {rating, ci_low, ci_high}}.
    """
    if rng is None:
        rng = _random.Random(42)

    all_pairs = [pair for group in grouped_pairs for pair in group]
    base_ratings = fit_bradley_terry(all_pairs)

    groups = list(grouped_pairs)
    n_groups = len(groups)
    if n_groups == 0:
        return {p: {"rating": r, "ci_low": r, "ci_high": r} for p, r in base_ratings.items()}

    sample_ratings: dict[str, list[float]] = {p: [] for p in base_ratings}

    for _ in range(bootstrap_samples):
        resampled = [pair for _ in range(n_groups) for pair in rng.choice(groups)]
        ratings = fit_bradley_terry(resampled)
        for p in base_ratings:
            sample_ratings[p].append(ratings.get(p, 0.0))

    alpha = 1.0 - confidence
    lo_idx = max(0, int(math.floor(alpha / 2 * bootstrap_samples)))
    hi_idx = min(bootstrap_samples - 1, int(math.ceil((1.0 - alpha / 2) * bootstrap_samples)) - 1)

    result: dict[str, dict[str, Any]] = {}
    for p, base_r in base_ratings.items():
        sorted_samples = sorted(sample_ratings[p])
        result[p] = {
            "rating": base_r,
            "ci_low": sorted_samples[lo_idx],
            "ci_high": sorted_samples[hi_idx],
        }
    return result
