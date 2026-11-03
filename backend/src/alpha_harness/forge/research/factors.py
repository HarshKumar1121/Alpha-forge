"""Factor regression: how much of an alpha's PnL is a known risk factor, not new alpha.

Runs ``config/forge/factors.yaml``'s five expressions once per scope (``region/delay/universe``)
through the same :class:`~.planner.Researcher` the planner and the Court use, so a factor
expression already simulated for one alpha is never paid for again when the next alpha in the
same scope is regressed (CLAUDE.md rule 3). Regression itself (betas, R-squared, residual
Sharpe) is a pure function over PnL series, independent of how those series were obtained.

Ported pattern from ``research/court.py``'s ``_known_factor`` (one factor at a time, correlation
only); this module runs all factors together as a multi-factor OLS.
"""

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from .. import analytics as an
from .. import config
from ..store import InMemoryResearchStore
from .planner import Cand, Researcher

if TYPE_CHECKING:
    from ..alpha_types import AlphaType
    from ..simulator import Simulator

#: Rows of overlap a factor needs against the target before it's used in the regression — the
#: same floor ``families.py``/``court.py`` use for PnL correlation.
MIN_OVERLAP = 120

#: One store per scope, so factor sims for "usa/1/top3000" are never re-paid once simulated,
#: no matter which alpha is being regressed. There is no durable ResearchStore yet (see
#: ``forge/store.py``), so this is process-lifetime caching — the same durability every other
#: Gym/BRAIN research flow has today.
_stores: dict[str, InMemoryResearchStore] = {}


def _store_for(scope: str) -> InMemoryResearchStore:
    return _stores.setdefault(scope, InMemoryResearchStore())


async def factor_series(
    simulator: Simulator,
    alpha_type: AlphaType,
    settings: dict[str, Any],
    *,
    store: InMemoryResearchStore | None = None,
) -> dict[str, pd.Series]:
    """Simulate (or reuse the cached result for) every factor in ``factors.yaml`` under
    ``settings``, and return each one's daily PnL by factor id."""
    specs = config.factors().get("factors") or []
    if not specs:
        return {}
    scope = "{region}/{delay}/{universe}".format(**settings)
    researcher = Researcher(
        store or _store_for(scope),
        simulator,
        alpha_type,
        {},
        [],
        budget=len(specs),
        scope=scope,
    )
    cands = [
        Cand(spec["expr"], dict(settings), "_factor", spec["id"], "factor", stage="factor")
        for spec in specs
    ]
    await researcher.sim(cands, role="factor")
    out: dict[str, pd.Series] = {}
    for spec, cand in zip(specs, cands, strict=True):
        if cand.alpha_id is None:
            continue
        out[spec["id"]] = await researcher.pnl_series(cand.alpha_id)
    return out


@dataclass(slots=True)
class FactorRegression:
    #: Factor id -> coefficient. Empty when there weren't enough usable factors or overlap.
    betas: dict[str, float]
    alpha: float
    r_squared: float
    residual_sharpe: float
    n_obs: int
    #: Factors that had enough overlap with the target to be included.
    factors_used: list[str] = field(default_factory=list)
    #: Factor id -> its share of ``r_squared`` (can be negative for a factor correlated with
    #: another one in the mix). These sum to ``r_squared`` exactly.
    contributions: dict[str, float] = field(default_factory=dict)


_EMPTY = FactorRegression(
    betas={}, alpha=math.nan, r_squared=math.nan, residual_sharpe=math.nan, n_obs=0
)


def regress(target: pd.Series, factors: dict[str, pd.Series]) -> FactorRegression:
    """Multi-factor OLS: ``target = alpha + sum(beta_i * factor_i) + residual``.

    Factors without ``MIN_OVERLAP`` common days with the target (after aligning everything
    together) are dropped rather than failing the whole regression — a factor the simulator
    couldn't evaluate in this scope shouldn't block attribution on the rest.
    """

    def _overlap(s: pd.Series) -> int:
        return len(pd.concat([target, s], axis=1, join="inner").dropna())

    usable = {name: s for name, s in factors.items() if _overlap(s) >= MIN_OVERLAP}
    if not usable:
        return _EMPTY
    aligned = pd.concat({"target": target, **usable}, axis=1, join="inner").dropna()
    if len(aligned) < MIN_OVERLAP:
        return _EMPTY
    names = list(usable)
    y = aligned["target"].to_numpy(dtype=float)
    x = np.column_stack([np.ones(len(aligned)), *(aligned[n].to_numpy(dtype=float) for n in names)])
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    fitted = x @ coef
    residual = y - fitted
    ss_res = float(np.sum(residual**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else math.nan
    residual_series = pd.Series(residual, index=aligned.index)

    # Additive variance decomposition: for OLS with an intercept, SSR == sum_i beta_i * cov(x_i,
    # y) * n, so dividing each term by ss_tot gives each factor's exact share of r_squared.
    if ss_tot > 0:
        x_centered = x[:, 1:] - x[:, 1:].mean(axis=0)
        y_centered = y - y.mean()
        contributions = {
            name: float(coef[1 + i] * np.dot(x_centered[:, i], y_centered) / ss_tot)
            for i, name in enumerate(names)
        }
    else:
        contributions = dict.fromkeys(names, math.nan)

    return FactorRegression(
        betas=dict(zip(names, (float(b) for b in coef[1:]), strict=True)),
        alpha=float(coef[0]),
        r_squared=r_squared,
        residual_sharpe=an.sharpe(residual_series),
        n_obs=len(aligned),
        factors_used=names,
        contributions=contributions,
    )


def summarize(reg: FactorRegression) -> str:
    """One plain-English sentence for how much of this PnL is known factor exposure.

    e.g. "32% explained by momentum, 18% by size; residual Sharpe 1.24." Factors below 1% of
    r_squared are left out as noise in the decomposition.
    """
    if not reg.factors_used:
        return "No tracked factor explains this alpha; it looks idiosyncratic."
    ranked = sorted(reg.factors_used, key=lambda f: -abs(reg.contributions.get(f, 0.0)))
    parts = [
        f"{reg.contributions[f] * 100:.0f}% by {f}"
        for f in ranked
        if not math.isnan(reg.contributions.get(f, math.nan)) and abs(reg.contributions[f]) >= 0.01
    ]
    residual = (
        "" if math.isnan(reg.residual_sharpe) else f"residual Sharpe {reg.residual_sharpe:.2f}"
    )
    if not parts:
        return f"Factor exposure is negligible{f'; {residual}' if residual else ''}."
    return f"Explained {', '.join(parts)}{f'; {residual}' if residual else ''}."
