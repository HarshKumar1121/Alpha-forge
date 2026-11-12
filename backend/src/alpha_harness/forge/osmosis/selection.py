"""Choosing the combination: greedy forward selection on training data.

The question this answers: which handful of eligible Alphas, held together, gives the best
risk-adjusted combined PnL? Greedy forward selection answers it one Alpha at a time:

1. Start with nothing.
2. Try each remaining candidate in the combination with an equal weight, and measure the
   combined Sharpe. The gain that candidate adds is its *marginal contribution*.
3. Keep the best candidate, unless it would breach the correlation cap.
4. Repeat until the combination is full, or no candidate adds Sharpe.

Two guards matter here:

- The correlation cap. Two Alphas with daily PnL correlation near 1 look like two Alphas but
  are one bet. Adding the second adds almost no diversification, and the Sharpe gain it shows
  on training data is fragile.
- Training data only. Each candidate is judged on the training window. Judging on the test
  window would leak the answer into the selection and inflate every out-of-sample number.
  :mod:`walkforward` calls this inside each fold, so the test window never reaches it.

A combination must reach :func:`min_combination_size` Alphas even when extra ones add no
Sharpe. That size comes from the per-Alpha cap: with a 25% cap, four Alphas are the fewest that
can hold 100% of the points.
"""

import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import pandas as pd

    from .settings import Settings


def sharpe(daily: np.ndarray, periods_per_year: int) -> float:
    """Annualised Sharpe ratio of a daily PnL series: mean over standard deviation, times
    the square root of the trading days in a year.

    Scale-free: multiplying every PnL by the same number leaves it unchanged, so the unit of
    the series does not matter. ``nan`` when there are too few days or no variation.
    """
    if daily.size < 2:
        return math.nan
    sd = float(np.std(daily, ddof=1))
    if not math.isfinite(sd) or sd == 0.0:
        return math.nan
    return float(np.mean(daily)) / sd * math.sqrt(periods_per_year)


def combo(frame: pd.DataFrame) -> pd.Series:
    """The equal-weighted combination of the columns: each day's average PnL across them."""
    return frame.mean(axis=1)


def min_combination_size(settings: Settings) -> int:
    """The fewest Alphas whose caps can add up to the whole: ``ceil(1 / cap)``."""
    return math.ceil(1.0 / settings.cap - 1e-12)


def greedy_select(train: pd.DataFrame, settings: Settings) -> list[str]:
    """The Alphas chosen on ``train`` (columns are Alpha IDs, rows are days).

    Returns fewer than :func:`min_combination_size` Alphas only when the correlation cap leaves
    no other candidate; the caller reports that as a blocked scope.
    """
    cols = [str(c) for c in train.columns]
    if not cols:
        return []
    corr = train.corr().to_numpy()
    position = {c: i for i, c in enumerate(train.columns)}
    floor_size = min_combination_size(settings)
    chosen: list[str] = []
    best_so_far = -math.inf
    while len(chosen) < settings.max_selected:
        best: str | None = None
        best_sh = -math.inf
        for candidate in cols:
            if candidate in chosen:
                continue
            # ``not (x <= cap)`` also rejects NaN, which a constant series produces.
            if any(
                not (abs(float(corr[position[candidate], position[s]])) <= settings.corr_cap)
                for s in chosen
            ):
                continue
            sh = sharpe(combo(train[[*chosen, candidate]]).to_numpy(), settings.periods_per_year)
            if math.isfinite(sh) and sh > best_sh:
                best, best_sh = candidate, sh
        if best is None:
            break
        if len(chosen) >= floor_size and not (best_sh > best_so_far + settings.min_sharpe_gain):
            break
        chosen.append(best)
        best_so_far = best_sh
    return chosen
