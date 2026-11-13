"""Five ways to split one combination across its Alphas, compared out of sample.

Every method takes the training-window PnL of the selected Alphas and returns long-only weights
that sum to 1, then clipped to the per-Alpha cap and floor (:func:`points.bound_weights`).

- ``equal_weight``: 1/N. The baseline. It uses no estimates at all.
- ``inverse_volatility``: weight each Alpha by 1/sigma. Steadier Alphas get more.
- ``min_variance``: the mix with the smallest combined variance, on a shrunk covariance.
- ``hrp``: hierarchical risk parity (López de Prado, 2016). Clusters Alphas by correlation and
  splits risk down the tree. It uses no expected returns, so it cannot be fooled by a noisy one.
- ``max_sharpe``: the mix with the highest Sharpe, with means shrunk toward the group mean.
- ``equal_risk_contribution``: each Alpha contributes the same share of the combination's
  variance (Maillard, Roncalli & Teiletche, 2010). Unlike inverse volatility it sees
  correlation: two near-copies of one Alpha share one slot of risk, not two.

Why equal weight is the baseline to beat. Expected returns are estimated from a few years of
daily PnL, and that estimate is very noisy. An Alpha with true annual Sharpe 1.0, observed over
two years, has an estimated Sharpe of about 1.0 ± 0.87. An optimiser that maximises on such
estimates mostly fits the noise, and loses when the noise is not there out of sample (DeMiguel,
Garlappi & Uppal, 2009). Walk-forward validation (:mod:`walkforward`) is what tests whether any
method earns its complexity here.

Covariances are estimated on the training window only. Every method is scale-free, so the
series are divided by one common scale first; weights do not change.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.optimize import minimize
from scipy.spatial.distance import squareform

from .points import bound_weights

if TYPE_CHECKING:
    from .settings import Settings

Method = Literal[
    "equal_weight",
    "inverse_volatility",
    "min_variance",
    "hrp",
    "max_sharpe",
    "equal_risk_contribution",
    # forge: the robust methods live in robust.py (docs/forge/OSMOSIS_ROBUST_METHODS.md)
    "resampled_max_sharpe",
    "nco",
    "max_diversification",
    "bayesian_blend",
    "ensemble",
]
#: Equal weight first: it is the baseline the others must beat.
METHODS: tuple[Method, ...] = (
    "equal_weight",
    "inverse_volatility",
    "min_variance",
    "hrp",
    "max_sharpe",
    "equal_risk_contribution",
    "resampled_max_sharpe",
    "nco",
    "max_diversification",
    "bayesian_blend",
    "ensemble",
)
#: Computed in :mod:`robust`. Imported lazily there, because robust reads this module's solvers.
ROBUST_METHODS: frozenset[str] = frozenset(
    {"resampled_max_sharpe", "nco", "max_diversification", "bayesian_blend", "ensemble"}
)

#: How close each Alpha's risk share must be to 1/N for the solver's answer to count.
ERC_TOLERANCE = 1e-4


@dataclass(frozen=True)
class Allocation:
    """A method's bounded weights, and whether it had to fall back to equal weight."""

    weights: pd.Series
    #: True when the optimiser failed or had no sensible answer, so equal weight was used.
    fallback: bool


def ledoit_wolf_cov(returns: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf shrinkage covariance with a constant-correlation target (2004).

    A sample covariance from a short history is noisy, and its inverse is worse. The estimator
    pulls it toward a simpler target: every pair of Alphas has the same average correlation.
    The pull strength (``delta``, between 0 and 1) is estimated from the data itself. With few
    days or many Alphas, it leans harder toward the target.

    ``returns`` is days by Alphas. The result is Alphas by Alphas and always positive
    semi-definite.
    """
    x = np.asarray(returns, dtype=float)
    n, p = x.shape
    x = x - x.mean(axis=0)
    s = x.T @ x / n  # divisor n, as in Ledoit & Wolf's derivation
    var = np.diag(s).copy()
    if p == 1:
        return s
    sd = np.sqrt(var)
    off = ~np.eye(p, dtype=bool)
    r_bar = float((s / np.outer(sd, sd))[off].mean())
    target = r_bar * np.outer(sd, sd)
    np.fill_diagonal(target, var)

    # pi: how noisy each sample covariance entry is.
    z = x[:, :, None] * x[:, None, :] - s[None, :, :]
    pi_mat = (z**2).mean(axis=0)
    pi = float(pi_mat.sum())

    # rho: how the noise in the entries lines up with the target's.
    a = x**2 - var[None, :]
    theta_i = np.einsum("ti,tij->ij", a, z) / n
    theta_j = np.einsum("tj,tij->ij", a, z) / n
    ratio = np.sqrt(var[None, :] / var[:, None])  # sqrt(s_jj / s_ii) at [i, j]
    rho_off = (r_bar / 2.0) * (ratio * theta_i + (1.0 / ratio) * theta_j)
    rho = float(np.trace(pi_mat) + rho_off[off].sum())

    gamma = float(((target - s) ** 2).sum())
    delta = 1.0 if gamma <= 0 else min(1.0, max(0.0, (pi - rho) / gamma / n))
    return delta * target + (1.0 - delta) * s


def _cluster_variance(cov: np.ndarray, idx: np.ndarray) -> float:
    """Variance of a cluster, weighting its members by inverse variance (HRP's inner step)."""
    sub = cov[np.ix_(idx, idx)]
    inv = 1.0 / np.diag(sub)
    inv = inv / inv.sum()
    return float(inv @ sub @ inv)


def _hrp(cov: np.ndarray) -> np.ndarray:
    """Hierarchical risk parity weights (López de Prado, 2016).

    Three steps. Cluster the Alphas on correlation distance ``sqrt(0.5 (1 - rho))``. Reorder
    them so similar Alphas sit next to each other. Then split the whole budget down the tree:
    at each node, the left and right halves share it in inverse proportion to their variance.
    """
    sd = np.sqrt(np.diag(cov))
    corr = cov / np.outer(sd, sd)
    dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, 1.0))
    np.fill_diagonal(dist, 0.0)
    order = leaves_list(linkage(squareform(dist, checks=False), method="single"))
    weights = np.ones(cov.shape[0])
    stack: list[np.ndarray] = [order]
    while stack:
        cluster = stack.pop()
        if cluster.size <= 1:
            continue
        half = cluster.size // 2
        left, right = cluster[:half], cluster[half:]
        v_left = _cluster_variance(cov, left)
        v_right = _cluster_variance(cov, right)
        alpha = 1.0 - v_left / (v_left + v_right)
        weights[left] *= alpha
        weights[right] *= 1.0 - alpha
        stack.extend([left, right])
    return weights / weights.sum()


def min_variance_solution(cov: np.ndarray) -> np.ndarray | None:
    """The long-only minimum-variance mix, or ``None`` if the solver does not converge."""
    p = cov.shape[0]
    res = minimize(
        lambda w: float(w @ cov @ w),
        np.full(p, 1.0 / p),
        jac=lambda w: 2.0 * cov @ w,
        bounds=[(0.0, 1.0)] * p,
        constraints=({"type": "eq", "fun": lambda w: float(w.sum()) - 1.0},),
        method="SLSQP",
    )
    return res.x if res.success else None


def risk_shares(weights: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """Each Alpha's share of the combination's variance, summing to 1.

    Alpha ``i`` contributes ``w_i (Sigma w)_i`` to the variance ``w' Sigma w``. These shares are
    what "risk contribution" means: a 50% share is half the combination's risk, whatever the
    Alpha's own volatility.
    """
    contributions = weights * (cov @ weights)
    return contributions / contributions.sum()


def equal_risk_contribution_solution(cov: np.ndarray) -> np.ndarray | None:
    """The long-only mix where every Alpha carries the same share of risk, or ``None``.

    Solved as a least-squares problem: find weights that make every pair of risk shares equal.
    The start point is inverse volatility, which is exact when the Alphas are uncorrelated and a
    good guess otherwise. A solver that stops early, or a solution whose shares miss 1/N by more
    than :data:`ERC_TOLERANCE`, returns ``None`` so the caller falls back to equal weight.
    """
    p = cov.shape[0]
    start = 1.0 / np.sqrt(np.diag(cov))
    start = start / start.sum()

    def pairwise_gap(w: np.ndarray) -> float:
        shares = risk_shares(w, cov)
        return float(((shares[:, None] - shares[None, :]) ** 2).sum())

    res = minimize(
        pairwise_gap,
        start,
        bounds=[(1e-9, 1.0)] * p,
        constraints=({"type": "eq", "fun": lambda w: float(w.sum()) - 1.0},),
        method="SLSQP",
        options={"maxiter": 500, "ftol": 1e-14},
    )
    if not res.success:
        return None
    solved = np.clip(res.x, 0.0, None)
    solved = solved / solved.sum()
    if np.max(np.abs(risk_shares(solved, cov) - 1.0 / p)) > ERC_TOLERANCE:
        return None
    return solved


def negative_ratio_gradient(w: np.ndarray, cov: np.ndarray, vector: np.ndarray) -> np.ndarray:
    """Gradient of ``-(vector . w) / sqrt(w' cov w)`` with respect to ``w``.

    Analytic, so the optimiser does not estimate it by finite differences: that took most of the
    walk-forward's time. The max-Sharpe objective is this with the shrunk means as ``vector``, and
    the diversification ratio is this with the volatilities.
    """
    variance = max(float(w @ cov @ w), 1e-18)
    s = np.sqrt(variance)
    return -vector / s + float(w @ vector) * (cov @ w) / s**3


def max_sharpe_solution(
    cov: np.ndarray, train: np.ndarray, settings: Settings
) -> np.ndarray | None:
    """The long-only mix with the highest Sharpe, using shrunk means.

    Each Alpha's mean is pulled toward the group mean by ``mean_shrink``. Raw means are the
    noisiest input in the whole allocator; shrinking them is the cheapest protection against
    betting heavily on one lucky stretch. Returns ``None`` when no Alpha has a positive shrunk
    mean, because then no mix has a positive Sharpe.
    """
    mu = train.mean(axis=0)
    mu_shrunk = (1.0 - settings.mean_shrink) * mu + settings.mean_shrink * mu.mean()
    if not (mu_shrunk > 0).any():
        return None
    p = cov.shape[0]

    def negative_sharpe(w: np.ndarray) -> float:
        variance = max(float(w @ cov @ w), 1e-18)
        return -float(w @ mu_shrunk) / np.sqrt(variance)

    res = minimize(
        negative_sharpe,
        np.full(p, 1.0 / p),
        jac=lambda w: negative_ratio_gradient(w, cov, mu_shrunk),
        bounds=[(0.0, 1.0)] * p,
        constraints=({"type": "eq", "fun": lambda w: float(w.sum()) - 1.0},),
        method="SLSQP",
    )
    if not res.success or float(res.x @ mu_shrunk) <= 0:
        return None
    return res.x


def allocate(method: Method, train: pd.DataFrame, settings: Settings) -> Allocation:
    """Weights for ``train``'s columns under ``method``, bounded by the cap and floor.

    The result always sums to 1 and respects the bounds. A method that cannot produce a sensible
    answer returns equal weight with ``fallback=True``, so the screen can say so.
    """
    if method not in METHODS:
        raise ValueError(f"unknown weighting method: {method}")
    cols = list(train.columns)
    p = len(cols)
    if p == 1:
        return Allocation(pd.Series([1.0], index=cols), fallback=False)

    values = train.to_numpy(dtype=float)
    scale = float(np.std(values, ddof=1))
    if not np.isfinite(scale) or scale == 0.0:
        raise ValueError("training PnL has no variation; cannot allocate")
    x = values / scale
    raw, fallback = solve_raw(method, x, settings)

    bounded = bound_weights(raw, settings.floor, settings.cap)
    return Allocation(pd.Series(bounded, index=cols), fallback=fallback)


def solve_raw(method: Method, x: np.ndarray, settings: Settings) -> tuple[np.ndarray, bool]:
    """Raw, unbounded weights for ``method`` on scaled days-by-Alphas ``x``, and the fallback flag.

    Shared by :func:`allocate` and by the ensemble, so an ensemble's inputs are computed exactly as
    the standalone methods are.
    """
    p = x.shape[1]
    equal = np.full(p, 1.0 / p)

    if method == "equal_weight":
        return equal, False
    if method in ROBUST_METHODS:
        # forge: local import, because robust.py imports this module's solvers.
        from .robust import solve_robust

        return solve_robust(method, x, settings)
    if method == "inverse_volatility":
        sd = x.std(axis=0, ddof=1)
        if not np.all(sd > 0):
            raise ValueError("an Alpha has zero training volatility; cannot invert it")
        return (1.0 / sd) / (1.0 / sd).sum(), False

    cov = ledoit_wolf_cov(x)
    if method == "hrp":
        return _hrp(cov), False
    if method == "min_variance":
        solved = min_variance_solution(cov)
    elif method == "equal_risk_contribution":
        solved = equal_risk_contribution_solution(cov)
    else:
        solved = max_sharpe_solution(cov, x, settings)
    if solved is None:
        return equal, True
    clipped = np.clip(solved, 0.0, None)
    return clipped / clipped.sum(), False
