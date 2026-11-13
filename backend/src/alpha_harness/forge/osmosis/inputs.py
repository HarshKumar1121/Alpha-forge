"""Two inputs the robust methods read: which PnL basis to use, and how consistent each Alpha is.

**PnL basis.** BRAIN's daily PnL is gross: it ignores what trading costs. After-cost PnL takes a
cost of ``bps`` basis points on every dollar traded each day, using the same formula as the
vault's after-cost figures (:func:`vault.metrics.after_cost`). The basis is a setting, but it is
used for a scope only when every candidate in that scope has turnover stored. A mix of gross and
net PnL inside one scope would compare apples with oranges, so a scope falls back to gross as a
whole, and the screen says so.

**Consistency.** A Sharpe ratio measured over a whole training window hides whether the Alpha
earned it steadily or in one lucky stretch. We cut the training window into half-years (126
trading days), compute each half-year's Sharpe, and take their standard deviation. A higher number
means a less steady Alpha. Half-years are a minimum of four, because fewer gives a standard
deviation that is itself mostly noise. Below that the score is neutral, which is 0, so it adds no
extra uncertainty. A 504-day window (two years) holds only four half-years, so a calendar-year
measure would never be available inside a walk-forward fold.

Everything here reads the training window it is given and nothing after it (hard rule 6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from ...vault import metrics

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .settings import Settings

#: Each PnL basis a setting may name, with the trading cost it charges in basis points.
BASIS_COST_BPS: dict[str, float] = {
    "gross": 0.0,
    "after_cost_5bps": 5.0,
}


@dataclass(frozen=True)
class ResolvedBasis:
    """The basis a scope actually uses, and why it is that one."""

    #: The name used on screen and in the CSV, e.g. ``after_cost_5bps`` or ``gross``.
    name: str
    #: The cost charged on each dollar traded. Zero for gross PnL.
    cost_bps: float
    #: One plain sentence for the screen. Says what happened when the request was not honoured.
    reason: str


def resolve_basis(requested: str, has_turnover: Mapping[str, bool]) -> ResolvedBasis:
    """The basis for one scope: the requested one when every candidate supports it.

    ``has_turnover`` maps each candidate Alpha to whether its turnover is stored for every day it
    has PnL. A scope with a single Alpha missing turnover is reported as gross, with the reason.
    """
    if requested not in BASIS_COST_BPS:
        raise ValueError(f"unknown PnL basis {requested!r}; choose one of {sorted(BASIS_COST_BPS)}")
    if requested == "gross":
        return ResolvedBasis("gross", 0.0, "Gross PnL, as BRAIN reports it.")
    missing = sorted(alpha for alpha, ok in has_turnover.items() if not ok)
    if not has_turnover or missing:
        shown = ", ".join(missing[:3]) + (" and more" if len(missing) > 3 else "")
        reason = (
            f"Gross PnL: {len(missing)} candidate(s) lack stored turnover ({shown}), so the "
            "whole scope uses gross PnL."
            if missing
            else "Gross PnL: the scope has no candidates with turnover."
        )
        return ResolvedBasis("gross", 0.0, reason)
    cost = BASIS_COST_BPS[requested]
    return ResolvedBasis(requested, cost, f"Net of a {cost:g} bps cost on every dollar traded.")


def turnover_complete(pnl: np.ndarray, turnover: np.ndarray) -> np.ndarray:
    """Per Alpha: True when turnover is present on every day that has PnL.

    Both arrays are days by Alphas and aligned by row. Days without PnL are ignored.
    """
    have_pnl = np.isfinite(pnl)
    have_turnover = np.isfinite(turnover)
    return np.all(have_turnover | ~have_pnl, axis=0)


def apply_basis(pnl: np.ndarray, turnover: np.ndarray, cost_bps: float) -> np.ndarray:
    """PnL net of ``cost_bps`` on each day's turnover. Gross (zero cost) returns PnL unchanged.

    The formula is the vault's own (:func:`vault.metrics.after_cost`), so the Osmosis figures and
    the rest of the app quote the same net PnL.
    """
    if cost_bps == 0:
        return np.asarray(pnl, dtype=float).copy()
    return np.asarray(metrics.after_cost(pnl, turnover, cost_bps), dtype=float)


def half_year_sharpes(x: np.ndarray, settings: Settings) -> list[np.ndarray]:
    """Per Alpha, the annualised Sharpe of each complete half-year of the window.

    ``x`` is days by Alphas, oldest day first, with no missing values (the robust methods only ever
    see complete Alphas). A trailing partial half-year is dropped, so every Sharpe is measured on
    the same number of days. A half-year whose PnL has no variation has no Sharpe and is skipped.
    """
    days = settings.half_year_days
    full = x.shape[0] // days
    out: list[np.ndarray] = []
    for col in range(x.shape[1]):
        sharpes: list[float] = []
        for k in range(full):
            chunk = x[k * days : (k + 1) * days, col]
            sd = float(np.std(chunk, ddof=1))
            if sd > 0:
                sharpes.append(float(np.mean(chunk)) / sd * np.sqrt(settings.periods_per_year))
        out.append(np.asarray(sharpes, dtype=float))
    return out


def consistency_scores(x: np.ndarray, settings: Settings) -> np.ndarray:
    """Per Alpha, the standard deviation of its half-year Sharpes; 0 (neutral) if too few.

    Higher means less consistent. An Alpha with fewer than ``min_half_years`` measurable
    half-years gets 0, which adds no extra uncertainty to the Bayesian blend (see
    :mod:`robust`). Computed from the training window only.
    """
    scores = np.zeros(x.shape[1], dtype=float)
    for col, sharpes in enumerate(half_year_sharpes(x, settings)):
        if sharpes.size >= settings.min_half_years:
            scores[col] = float(np.std(sharpes, ddof=1))
    return scores
