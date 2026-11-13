"""Five more ways to weight a combination, built to hold up when the estimates are noisy.

The original five (:mod:`weights`) either ignore expected returns or trust them as they are. These
five are a response to the same noise problem: a few years of daily PnL estimates each Alpha's
return and risk badly, and an optimiser that trusts those estimates fits noise.

- ``resampled_max_sharpe``: refit max-Sharpe on many bootstrap resamples of the history, then
  average the weights. Averaging smooths out the lumpy answers one fit gives on noisy inputs.
- ``nco``: nested clustered optimisation (López de Prado, 2019). Group correlated Alphas, solve a
  small problem inside each group, then solve across the groups. Fewer, steadier estimates.
- ``max_diversification``: the mix that diversifies the most volatility away. It maximises the
  diversification ratio (Choueifaty & Coignard, 2008).
- ``bayesian_blend``: Black-Litterman (1992). Start from a prior (the risk each Alpha should carry
  if nothing is known), then move it by views (each Alpha's shrunk Sharpe). Views on an
  inconsistent Alpha count for less; views count for more with a longer history.
- ``ensemble``: the average of the weights from every method above that gave a real answer, so no
  single fit decides the mix. Equal weight is left out: it is the baseline.

Every method returns long-only weights that sum to 1. Each one that cannot give a sensible
answer falls back to equal weight, and says so, as the others do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.optimize import minimize
from scipy.spatial.distance import squareform

from .inputs import consistency_scores
from .points import bound_weights
from .walkforward import stationary_bootstrap_indices
from .weights import (
    Allocation,
    equal_risk_contribution_solution,
    ledoit_wolf_cov,
    max_sharpe_solution,
    min_variance_solution,
    negative_ratio_gradient,
    solve_raw,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .settings import Settings
    from .weights import Method

#: The methods this module computes. ``ensemble`` is last because it reads the others.
ROBUST_METHODS: tuple[Method, ...] = (
    "resampled_max_sharpe",
    "nco",
    "max_diversification",
    "bayesian_blend",
    "ensemble",
)

#: What the ensemble averages: every method except equal weight (the baseline) and the ensemble.
ENSEMBLE_INPUTS: tuple[Method, ...] = (
    "inverse_volatility",
    "min_variance",
    "hrp",
    "max_sharpe",
    "equal_risk_contribution",
    "resampled_max_sharpe",
    "nco",
    "max_diversification",
    "bayesian_blend",
)


def _corr_distance(cov: np.ndarray) -> np.ndarray:
    """Correlation distance ``sqrt(0.5 (1 - rho))``: 0 for identical Alphas, 1 for uncorrelated."""
    sd = np.sqrt(np.diag(cov))
    corr = cov / np.outer(sd, sd)
    dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, 1.0))
    np.fill_diagonal(dist, 0.0)
    return dist


def resampled_weights(x: np.ndarray, settings: Settings) -> np.ndarray | None:
    """Average of shrunk max-Sharpe weights over stationary-bootstrap resamples of the history.

    One max-Sharpe fit on a noisy history is itself noisy: a few extra days can move its weights a
    lot. Refitting on many resamples (blocks of ``bootstrap_block`` days, so the day-to-day
    structure survives) and averaging gives a steadier answer. The draws are seeded, so the same
    data gives the same weights. Returns ``None`` when fewer than half the draws have a solution.
    """
    rng = np.random.default_rng(settings.seed)
    n = x.shape[0]
    draws: list[np.ndarray] = []
    for _ in range(settings.resample_count):
        sample = x[stationary_bootstrap_indices(n, settings.bootstrap_block, rng)]
        solved = max_sharpe_solution(ledoit_wolf_cov(sample), sample, settings)
        if solved is not None:
            clipped = np.clip(solved, 0.0, None)
            draws.append(clipped / clipped.sum())
    if len(draws) < 0.5 * settings.resample_count:
        return None
    return np.mean(draws, axis=0)


def _silhouette(dist: np.ndarray, labels: np.ndarray) -> float:
    """Mean silhouette score of a clustering on a distance matrix: how well each Alpha fits its
    own group (near 1) compared with the nearest other group (near -1). Singletons score 0."""
    if np.unique(labels).size < 2:
        return -1.0  # one group says nothing about structure, so it scores worst
    scores: list[float] = []
    for i in range(labels.size):
        same = labels == labels[i]
        same[i] = False
        if not same.any():
            scores.append(0.0)
            continue
        a = float(dist[i, same].mean())
        b = min(float(dist[i, labels == c].mean()) for c in np.unique(labels) if c != labels[i])
        scores.append((b - a) / max(a, b) if max(a, b) > 0 else 0.0)
    return float(np.mean(scores))


def nco_weights(x: np.ndarray) -> np.ndarray | None:
    """Nested clustered optimisation (López de Prado, 2019).

    1. Cluster the Alphas on correlation distance. The number of clusters ``k`` is the one with
       the highest silhouette score among 2 .. p-1. Alphas that move together share a cluster.
    2. Inside each cluster, solve minimum variance over its members. The cluster becomes one
       return series: its members' PnL, weighted by that solution.
    3. Across clusters, solve minimum variance over the cluster series.
    4. Expand: an Alpha's weight is its cluster's weight times its share inside the cluster.

    Each solve is on a smaller matrix than the full problem, so each estimate has fewer
    parameters to get wrong. Returns ``None`` if there are fewer than three Alphas (no k to pick)
    or any solve fails.
    """
    p = x.shape[1]
    if p < 3:
        return None
    cov = ledoit_wolf_cov(x)
    dist = _corr_distance(cov)
    tree = linkage(squareform(dist, checks=False), method="average")
    best: tuple[float, np.ndarray] | None = None
    for k in range(2, p):
        labels = fcluster(tree, t=k, criterion="maxclust")
        if np.unique(labels).size != k:
            continue
        score = _silhouette(dist, labels)
        if best is None or score > best[0]:
            best = (score, labels)
    if best is None:
        return None
    labels = best[1]
    groups = [np.flatnonzero(labels == c) for c in np.unique(labels)]

    inside = np.zeros((p, len(groups)))
    for j, members in enumerate(groups):
        if members.size == 1:
            inside[members[0], j] = 1.0
            continue
        solved = min_variance_solution(cov[np.ix_(members, members)])
        if solved is None:
            return None
        clipped = np.clip(solved, 0.0, None)
        inside[members, j] = clipped / clipped.sum()

    group_returns = x @ inside
    top = min_variance_solution(ledoit_wolf_cov(group_returns))
    if top is None:
        return None
    top = np.clip(top, 0.0, None)
    return inside @ (top / top.sum())


def max_diversification_weights(x: np.ndarray) -> np.ndarray | None:
    """The long-only mix with the highest diversification ratio, or ``None``.

    The ratio is the weighted average of the Alphas' volatilities divided by the combination's
    volatility. It is 1 when the Alphas are perfectly correlated and rises as they diversify each
    other. Maximised on the shrunk covariance; the start point is equal weight.
    """
    cov = ledoit_wolf_cov(x)
    sigma = np.sqrt(np.diag(cov))
    p = cov.shape[0]

    def negative_ratio(w: np.ndarray) -> float:
        variance = max(float(w @ cov @ w), 1e-18)
        return -float(w @ sigma) / np.sqrt(variance)

    res = minimize(
        negative_ratio,
        np.full(p, 1.0 / p),
        jac=lambda w: negative_ratio_gradient(w, cov, sigma),
        bounds=[(0.0, 1.0)] * p,
        constraints=({"type": "eq", "fun": lambda w: float(w.sum()) - 1.0},),
        method="SLSQP",
    )
    if not res.success or not np.all(np.isfinite(res.x)):
        return None
    clipped = np.clip(res.x, 0.0, None)
    if clipped.sum() <= 0:
        return None
    return clipped / clipped.sum()


def black_litterman_mean(
    prior: np.ndarray, views: np.ndarray, cov: np.ndarray, omega: np.ndarray, tau: float
) -> np.ndarray:
    """The posterior mean of Black-Litterman, with one view per Alpha (``P = I``).

    The prior ``pi`` is what each Alpha should earn if nothing is known. The views ``Q`` are what
    the data says. ``omega`` is how much each view is trusted (its variance). The posterior is the
    precision-weighted average of the two::

        mu_BL = [(tau Sigma)^-1 + Omega^-1]^-1 [(tau Sigma)^-1 pi + Omega^-1 Q]

    With ``omega`` huge, the views carry no weight and ``mu_BL`` is the prior. With ``omega``
    tiny (a long history), the views dominate and ``mu_BL`` is ``Q``.
    """
    prior_precision = np.linalg.inv(tau * cov)
    view_precision = np.diag(1.0 / omega)
    lhs = prior_precision + view_precision
    rhs = prior_precision @ prior + view_precision @ views
    return np.linalg.solve(lhs, rhs)


def bayesian_weights(x: np.ndarray, settings: Settings) -> np.ndarray | None:
    """Black-Litterman weights: the posterior mean, turned into a mix, or ``None``.

    - Prior: ``pi = lambda * Sigma * w_erc``, the returns that the equal-risk-contribution mix
      (reverse-optimised) implies. Every Alpha carries an equal share of risk in the prior.
    - Views: ``P = I``, one per Alpha. ``Q_i = shrunk Sharpe_i * sigma_i / sqrt(252)``, the daily
      return the Alpha's Sharpe implies at its own volatility. Sharpes are shrunk toward the group.
    - Confidence: ``Omega_ii = tau * sigma_i^2 * (1 + kappa * s_i^2) / T_i``. ``T_i`` is the days of
      history; ``s_i`` is the consistency score (:mod:`inputs`), so an erratic Alpha's view is
      trusted less. A neutral score (0) adds no extra uncertainty.
    - Weights: proportional to ``(lambda Sigma)^-1 mu_BL``. Long-only. If any weight is not
      positive, the blend has no long-only answer and the function returns ``None``.
    """
    cov = ledoit_wolf_cov(x)
    sigma = np.sqrt(np.diag(cov))
    w_erc = equal_risk_contribution_solution(cov)
    if w_erc is None:
        return None
    lam, tau, kappa = settings.bl_risk_aversion, settings.bl_tau, settings.bl_kappa
    days, ppy = x.shape[0], settings.periods_per_year

    prior = lam * cov @ w_erc
    mean = x.mean(axis=0)
    sharpe = mean / sigma * np.sqrt(ppy)
    shrunk = (1.0 - settings.mean_shrink) * sharpe + settings.mean_shrink * sharpe.mean()
    views = shrunk * sigma / np.sqrt(ppy)

    scores = consistency_scores(x, settings)
    omega = tau * sigma**2 * (1.0 + kappa * scores**2) / days

    mu = black_litterman_mean(prior, views, cov, omega, tau)
    raw = np.linalg.solve(lam * cov, mu)
    if not np.all(np.isfinite(raw)) or not np.all(raw > 0):
        return None
    return raw / raw.sum()


def ensemble_weights(x: np.ndarray, settings: Settings) -> np.ndarray | None:
    """The mean of the bounded weights of every input that gave a real answer, or ``None``.

    Each input is bounded to the floor and cap first, so the mean respects them too (an average of
    bounded mixes is bounded). Inputs that fell back to equal weight are left out, and so is equal
    weight itself: it is the baseline the ensemble must beat, not one of its votes.
    """
    answers: list[np.ndarray] = []
    for method in ENSEMBLE_INPUTS:
        raw, fallback = solve_raw(method, x, settings)
        if not fallback:
            answers.append(bound_weights(raw, settings.floor, settings.cap))
    if not answers:
        return None
    return np.mean(answers, axis=0)


def solve_robust(method: Method, x: np.ndarray, settings: Settings) -> tuple[np.ndarray, bool]:
    """Raw weights for one robust method on ``x`` (days by Alphas, scaled), and whether it fell
    back to equal weight. The caller bounds them to the floor and cap."""
    p = x.shape[1]
    solvers = {
        "resampled_max_sharpe": lambda: resampled_weights(x, settings),
        "nco": lambda: nco_weights(x),
        "max_diversification": lambda: max_diversification_weights(x),
        "bayesian_blend": lambda: bayesian_weights(x, settings),
        "ensemble": lambda: ensemble_weights(x, settings),
    }
    if method not in solvers:
        raise ValueError(f"not a robust method: {method}")
    raw = solvers[method]()
    if raw is None:
        return np.full(p, 1.0 / p), True
    return raw / raw.sum(), False


def ensemble_of(allocations: Mapping[str, Allocation], cols: list[str]) -> Allocation:
    """The ensemble built from allocations already computed on the same window.

    The same rule as :func:`ensemble_weights`: the mean of the bounded weights of every input that
    did not fall back, with equal weight left out. It reuses the answers rather than refitting
    them, which is what the walk-forward needs, since it fits every method once per fold.
    """
    answers = [
        allocations[m].weights.to_numpy()
        for m in ENSEMBLE_INPUTS
        if m in allocations and not allocations[m].fallback
    ]
    if not answers:
        return Allocation(pd.Series(np.full(len(cols), 1.0 / len(cols)), index=cols), fallback=True)
    return Allocation(pd.Series(np.mean(answers, axis=0), index=cols), fallback=False)
