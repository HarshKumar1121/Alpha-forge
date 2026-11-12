"""How many independent bets a combination really holds, and how many Alphas to stop at.

Ten Alphas that all trade the same signal are one bet with ten tickets. Adding an eleventh adds
almost nothing to the risk you can diversify. The effective number of bets measures this in
one figure.

It is the participation ratio of the correlation matrix's eigenvalues (Meucci, 2009):

    ENB = (sum of eigenvalues)^2 / (sum of squared eigenvalues)

The eigenvalues of a correlation matrix sum to N. When the Alphas are uncorrelated, every
eigenvalue is 1 and ENB = N. When one shared factor drives them all, one eigenvalue takes
nearly everything and ENB is close to 1. So ENB is N in bets rather than in Alphas.

The recommended N is the smallest combination after which one more Alpha raises ENB by less
than ``gain`` (5% by default). The floor stops the rule from recommending a tiny combination,
since too few Alphas make a noisy plan however independent they look.

Everything here is computed on the selection window (training data) only. Nothing reads BRAIN.
"""

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import pandas as pd


def clean_correlation(frame: pd.DataFrame) -> np.ndarray:
    """The correlation matrix of ``frame``'s columns, with missing or flat entries made neutral.

    An Alpha with no day-to-day variation has no correlation, so it is treated as uncorrelated
    with the rest (zero off the diagonal). Without this, one flat Alpha would poison the
    eigenvalues with NaN.
    """
    if frame.shape[1] == 0:
        return np.zeros((0, 0))
    corr = frame.corr().to_numpy(dtype=float)
    corr = np.where(np.isfinite(corr), corr, 0.0)
    np.fill_diagonal(corr, 1.0)
    return corr


def effective_bets(corr: np.ndarray) -> float:
    """The participation ratio of a correlation matrix's eigenvalues: between 1 and its size."""
    size = corr.shape[0]
    if size == 0:
        return 0.0
    values = np.clip(np.linalg.eigvalsh(corr), 0.0, None)
    total_sq = float((values**2).sum())
    if total_sq <= 0.0:
        return 1.0
    return float(values.sum() ** 2 / total_sq)


def bets_curve(ranked: pd.DataFrame) -> list[float]:
    """ENB of the top-k Alphas for every k, where ``ranked`` columns are in greedy order.

    The correlation matrix is computed once for the whole ranking; each prefix is then a
    sub-matrix. Entry ``k - 1`` is the figure for the first ``k`` Alphas.
    """
    full = clean_correlation(ranked)
    return [effective_bets(full[:k, :k]) for k in range(1, full.shape[0] + 1)]


def recommended_size(curve: list[float], floor: int, gain: float) -> int:
    """The smallest N, never below ``floor``, after which one more Alpha adds under ``gain``.

    ``curve[k - 1]`` is the ENB of the first ``k`` Alphas. The step from ``k`` to ``k + 1`` adds
    ``curve[k] / curve[k - 1] - 1``. If no step ever flattens, the whole ranking is recommended.
    """
    total = len(curve)
    if total == 0:
        return 0
    start = min(max(floor, 1), total)
    for k in range(start, total):
        before, after = curve[k - 1], curve[k]
        if before <= 0.0:
            continue
        if after / before - 1.0 < gain:
            return k
    return total
