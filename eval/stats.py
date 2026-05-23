"""Stats helpers for the empirical eval layer.

Stdlib only — no numpy / scipy dependency. The helpers here are deliberately
narrow:

  bootstrap_ci(values)              — percentile bootstrap on a scalar metric
  mean_std(values)                  — sample mean + sample standard deviation
  pearson(xs, ys)                   — Pearson correlation
  cohen_quadratic_kappa(a, b, k)    — quadratic-weighted Cohen's kappa
                                       (used for cross-judge agreement on the
                                       1–5 LLM-judge scale)

Why bootstrap, not a closed-form CI: most of our metrics are computed across
heterogeneous tickets (mixed categories, debatable labels) — the underlying
distribution is not Bernoulli with a fixed p. The bootstrap makes minimal
assumptions and lets a senior reviewer see the uncertainty directly.

N=27 (bitext27) and N=20 (the held-out test split in Commit 4) will produce
wide intervals. That is honest. The methodology doc leads with this fact.
"""

from __future__ import annotations

import math
import random
import statistics


def bootstrap_ci(
    values: list[float],
    *,
    n_resamples: int = 1000,
    ci: float = 0.95,
    seed: int = 13,
) -> tuple[float, float, float]:
    """Percentile-method bootstrap confidence interval on `mean(values)`.

    Args:
        values: per-ticket scalar outcomes (e.g. 0/1 for accuracy, 1–5 for
            response_quality). MUST be float (or int — coerced).
        n_resamples: number of bootstrap resamples. 1000 is fast + stable
            at the precision we report (2 decimals).
        ci: confidence interval width, e.g. 0.95 → 2.5th and 97.5th percentile.
        seed: deterministic — same input always returns same CI. Critical for
            CI-friendly eval output (no flaky test comparisons).

    Returns:
        (point_estimate, ci_lower, ci_upper). All three are floats in the
        same range as the input. If `values` is empty, returns (0.0, 0.0, 0.0).

    Example:
        >>> point, lo, hi = bootstrap_ci([1, 1, 1, 1, 0, 1, 0, 1, 1, 1])
        >>> round(point, 2)
        0.8
    """
    if not values:
        return 0.0, 0.0, 0.0
    vals = [float(v) for v in values]
    point = sum(vals) / len(vals)

    rng = random.Random(seed)
    n = len(vals)
    resamples: list[float] = []
    for _ in range(n_resamples):
        sample = [vals[rng.randrange(n)] for _ in range(n)]
        resamples.append(sum(sample) / n)
    resamples.sort()

    alpha = 1 - ci
    lo_idx = max(0, int(math.floor((alpha / 2) * n_resamples)))
    hi_idx = min(n_resamples - 1, int(math.ceil((1 - alpha / 2) * n_resamples)) - 1)
    return point, resamples[lo_idx], resamples[hi_idx]


def mean_std(values: list[float]) -> tuple[float, float]:
    """Sample mean + sample standard deviation. Returns (0.0, 0.0) if empty."""
    if not values:
        return 0.0, 0.0
    vals = [float(v) for v in values]
    if len(vals) == 1:
        return vals[0], 0.0
    return statistics.fmean(vals), statistics.stdev(vals)


def pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation coefficient. Returns 0.0 if inputs are degenerate
    (different lengths, < 2 points, or zero variance)."""
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    denom_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    denom_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if denom_x == 0 or denom_y == 0:
        return 0.0
    return num / (denom_x * denom_y)


def cohen_quadratic_kappa(
    scores_a: list[int], scores_b: list[int], k_max: int
) -> float:
    """Quadratic-weighted Cohen's kappa for ordinal ratings 1..k_max.

    Used in cross-judge bias estimation (gpt-4o-mini vs gpt-4o on the 1–5
    response_quality scale). Quadratic weighting penalises disagreement
    proportionally to its magnitude — so two judges that score (4, 5) on
    the same draft are barely penalised, while (1, 5) is penalised fully.

    Returns 0.0 on degenerate input (mismatched lengths, < 2 points,
    out-of-range scores, or zero observed disagreement structure).
    """
    if len(scores_a) != len(scores_b) or len(scores_a) < 2:
        return 0.0
    if not all(1 <= s <= k_max for s in scores_a + scores_b):
        return 0.0

    n = len(scores_a)
    # Observed confusion matrix
    obs: list[list[int]] = [[0] * k_max for _ in range(k_max)]
    for a, b in zip(scores_a, scores_b, strict=True):
        obs[a - 1][b - 1] += 1

    # Marginals → expected matrix under chance
    row_marg = [sum(row) for row in obs]
    col_marg = [sum(obs[r][c] for r in range(k_max)) for c in range(k_max)]
    exp: list[list[float]] = [
        [row_marg[r] * col_marg[c] / n for c in range(k_max)] for r in range(k_max)
    ]

    # Weight matrix (quadratic)
    weight: list[list[float]] = [
        [((r - c) ** 2) / ((k_max - 1) ** 2) for c in range(k_max)]
        for r in range(k_max)
    ]

    num = sum(
        weight[r][c] * obs[r][c] for r in range(k_max) for c in range(k_max)
    )
    den = sum(
        weight[r][c] * exp[r][c] for r in range(k_max) for c in range(k_max)
    )
    if den == 0:
        return 0.0
    return 1 - num / den


__all__ = [
    "bootstrap_ci",
    "cohen_quadratic_kappa",
    "mean_std",
    "pearson",
]
