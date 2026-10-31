"""Local analytics on daily PnL — the numbers BRAIN doesn't show you, but a real desk would.

Why each one exists:

* ``sharpe``            — BRAIN annualises over 250 days (measured, not 252).
* ``after_cost_sharpe`` — a signal that dies at 5 bps of cost was never alpha.
* ``yearly_sharpes``    — one great year carrying a 10-year Sharpe is fragile.
* ``deflated_sharpe``   — Bailey & Lopez de Prado (2014). If you tried 200 variants, the best
                          Sharpe is inflated by selection. DSR is the probability the true
                          Sharpe > 0 after accounting for how many things you tried.
* ``max_correlation``   — self-correlation against your own pool, computed locally so you
                          don't burn rate limit asking BRAIN for every candidate.

Ported from alpha-copilot ``analytics.py``.
"""

import math
from statistics import NormalDist
from typing import TYPE_CHECKING, cast

import numpy as np
import pandas as pd

from ..brain.platform import BOOK_SIZE, TRADING_DAYS

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

DAYS = TRADING_DAYS
_N = NormalDist()
#: Euler-Mascheroni: the expected maximum of N Gaussian draws is built from it.
_GAMMA = 0.5772156649


def moment(value: object) -> float:
    """A pandas reduction (``skew``, ``kurt``) of float data as a float. The stubs type these as
    any scalar, complex included; on a float series they are always a float."""
    return float(cast("float", value))


def to_series(pnl: Iterable[tuple[date | str, float]]) -> pd.Series:
    """Daily PnL rows as a date-indexed series, from the first day the book traded."""
    s = pd.Series({pd.Timestamp(d): v for d, v in pnl}, dtype=float).sort_index()
    nz = s.to_numpy().nonzero()[0]
    return s.iloc[nz[0] :] if len(nz) else s


def sharpe(daily: pd.Series) -> float:
    sd = float(daily.std(ddof=1)) if len(daily) > 1 else math.nan
    return float(daily.mean()) / sd * math.sqrt(DAYS) if sd > 0 else math.nan


def after_cost_sharpe(
    daily: pd.Series, turnover: pd.Series, bps: float = 5.0, book: float = BOOK_SIZE
) -> float:
    cost = turnover.reindex(daily.index).fillna(0.0) * book * bps / 1e4
    return sharpe(daily - cost)


def yearly_sharpes(daily: pd.Series) -> dict[int, float]:
    years = pd.DatetimeIndex(daily.index).year
    return {int(y): round(sharpe(g), 2) for y, g in daily.groupby(years) if len(g) > 20}


def stability(daily: pd.Series) -> float:
    """Share of years with positive Sharpe. A desk wants >= 0.8."""
    ys = [v for v in yearly_sharpes(daily).values() if not math.isnan(v)]
    return sum(v > 0 for v in ys) / len(ys) if ys else math.nan


def deflated_sharpe(
    daily: pd.Series, n_trials: int, trial_sharpes: list[float] | None = None
) -> float:
    """Probability that the true (daily) Sharpe exceeds the best you'd expect from
    ``n_trials`` lucky draws. Rule of thumb: want DSR > 0.95.

    ``trial_sharpes`` are the *annualised* Sharpes of every variant tried in this hypothesis
    family; their spread sets the luck benchmark. Without them we assume a spread of 0.5
    annualised, which is typical for BRAIN parameter sweeps.
    """
    x = daily.to_numpy(dtype=float)
    t = len(x)
    if t < 30 or n_trials < 1:
        return math.nan
    sr = float(x.mean() / x.std(ddof=1))  # daily, not annualised
    skew = moment(pd.Series(x).skew())
    kurt = moment(pd.Series(x).kurt()) + 3.0  # pandas gives excess kurtosis
    if trial_sharpes and len(trial_sharpes) > 1:
        var_sr = float(np.var(np.array(trial_sharpes) / math.sqrt(DAYS), ddof=1))
    else:
        var_sr = (0.5 / math.sqrt(DAYS)) ** 2
    sr0 = (
        0.0
        if n_trials == 1
        else math.sqrt(var_sr)
        * (
            (1 - _GAMMA) * _N.inv_cdf(1 - 1 / n_trials)
            + _GAMMA * _N.inv_cdf(1 - 1 / (n_trials * math.e))
        )
    )
    denom = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr * sr, 1e-12))
    return _N.cdf((sr - sr0) * math.sqrt(t - 1) / denom)


def correlation(a: pd.Series, b: pd.Series, min_overlap: int = 250, years: int = 4) -> float:
    """Pearson on days both traded, over BRAIN's ~4-calendar-year window."""
    df = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(df):
        last = pd.Timestamp(df.index[-1])
        df = df[df.index >= pd.Timestamp(year=last.year - years + 1, month=1, day=1)]
    if len(df) < min_overlap:
        return math.nan
    return float(df.iloc[:, 0].corr(df.iloc[:, 1]))


def max_correlation(candidate: pd.Series, pool: dict[str, pd.Series]) -> tuple[str | None, float]:
    best_id: str | None = None
    best = -1.0
    for aid, s in pool.items():
        c = correlation(candidate, s)
        if not math.isnan(c) and c > best:
            best_id, best = aid, c
    return best_id, best


def collision_verdict(
    cand_sharpe: float,
    candidate: pd.Series,
    pool: dict[str, pd.Series],
    pool_sharpes: dict[str, float],
    ceiling: float,
    escape: float,
) -> tuple[bool, str]:
    """BRAIN-style rule (as measured by alpha-harness): above the ceiling, the candidate must
    beat the Sharpe of EVERY colliding member by ``escape``x, not just the most correlated."""
    corrs = {aid: correlation(candidate, s) for aid, s in pool.items()}
    collisions = {k: v for k, v in corrs.items() if not math.isnan(v) and v >= ceiling}
    if not collisions:
        return True, "below ceiling"
    bar = escape * max(pool_sharpes.get(k, 0.0) for k in collisions)
    if cand_sharpe >= bar:
        return True, f"collides with {len(collisions)} but Sharpe {cand_sharpe:.2f} >= {bar:.2f}"
    worst = max(collisions, key=lambda k: collisions[k])
    return False, f"corr {collisions[worst]:.2f} with {worst}; needs Sharpe >= {bar:.2f}"
