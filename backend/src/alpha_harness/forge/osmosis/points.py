"""Turning weights into whole points that sum to exactly 100,000.

Two steps, in this order:

1. :func:`bound_weights`: clip each weight to ``[floor, cap]`` and hand the excess to the Alphas
   that still have room, until the weights sum to 1. The cap keeps one Alpha from carrying the
   scope. The floor keeps every selected Alpha in play.
2. :func:`to_points`: round to whole points with the largest-remainder method. Take each
   Alpha's floored points, then give the leftover points one at a time to the Alphas whose
   fractional part was largest. The total is exact by construction, unlike rounding each
   Alpha on its own, which can drift by a point or two.

Infeasible bounds raise instead of silently breaking the cap. With ``N`` Alphas, the cap
needs ``N * cap >= 1`` and the floor needs ``N * floor <= 1``.
"""

import numpy as np
import pandas as pd

#: Tolerance for "sums to 1" after the bounding loop.
_TOL = 1e-12


def bound_weights(weights: np.ndarray, floor: float, cap: float) -> np.ndarray:
    """Weights clipped to ``[floor, cap]`` that still sum to 1.

    The excess (or shortfall) left by clipping is shared out in proportion to the raw weights
    among the Alphas not yet at the bound it pushes against. Repeated until nothing moves.
    """
    w = np.asarray(weights, dtype=float)
    n = w.size
    if n == 0:
        raise ValueError("no weights to bound")
    if not np.all(np.isfinite(w)) or np.any(w < 0) or w.sum() <= 0:
        raise ValueError("weights must be finite, non-negative and not all zero")
    w = w / w.sum()
    if n * floor > 1 + _TOL or n * cap < 1 - _TOL:
        raise ValueError(
            f"cannot bound {n} Alphas to floor {floor} and cap {cap}: need "
            f"{1 / cap:.2f} <= N <= {1 / floor:.2f}"
        )
    x = np.clip(w, floor, cap)
    for _ in range(n + 2):
        diff = 1.0 - float(x.sum())
        if abs(diff) < _TOL:
            return x
        room = x < cap - _TOL if diff > 0 else x > floor + _TOL
        if not room.any():
            break
        share = w * room
        if share.sum() <= 0:
            share = room.astype(float)
        x = x + diff * share / share.sum()
        x = np.clip(x, floor, cap)
    if abs(1.0 - float(x.sum())) > 1e-9:
        raise RuntimeError("bounded weights did not reach 1; check floor and cap")
    return x


def to_points(weights: pd.Series, total: int, floor: float, cap: float) -> pd.Series:
    """Whole points per Alpha, summing to exactly ``total``.

    Largest-remainder rounding, then a check that every Alpha still lies within the cap and the
    floor in points. Ties on the remainder go to the earlier Alpha, so output is deterministic.
    """
    bounded = bound_weights(weights.to_numpy(), floor, cap)
    raw = bounded * total
    points = np.floor(raw).astype(np.int64)
    leftover = int(total - points.sum())
    n = points.size
    if not 0 <= leftover <= n:
        raise RuntimeError(f"rounding left {leftover} points for {n} Alphas")
    fractions = raw - points
    order = sorted(range(n), key=lambda i: (-fractions[i], i))
    for i in order[:leftover]:
        points[i] += 1
    if int(points.sum()) != total:
        raise RuntimeError("points do not sum to the total")
    lo, hi = floor * total, cap * total
    if np.any(points < lo - 1e-6) or np.any(points > hi + 1e-6):
        raise RuntimeError("a rounded allocation broke the floor or cap")
    return pd.Series(points, index=weights.index, dtype=np.int64)
