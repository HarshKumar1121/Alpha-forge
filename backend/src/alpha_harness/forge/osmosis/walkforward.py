"""Walk-forward validation: does a weighting method beat equal weight on data it never saw?

The window rolls forward through history. In each fold:

1. Train on an older window: select the Alphas (:mod:`selection`) and fit every method
   (:mod:`weights`).
2. Test on the next window, which the fit never saw: hold the weights fixed and record the
   combined daily PnL.

The out-of-sample daily PnL from all folds is then one long honest record, and every method is
scored on the same days.

A method wins only if it beats equal weight out of sample, and the win must survive a paired
bootstrap: resample the days in blocks, recompute the Sharpe gap each time, and require the 2.5%
lower bound of the gap to sit above zero. Otherwise the answer is equal weight. Ties go to equal
weight, because it is the simplest method and has the fewest ways to fail on unseen data.

Known limit: with few folds, no method can show a significant win. :func:`judge` reports the
numbers either way, so "no win" reads as "not enough evidence", not as a verdict on the method.
"""

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from .selection import greedy_select, sharpe

if TYPE_CHECKING:
    from .settings import Settings
from .weights import METHODS, Method, allocate


@dataclass(frozen=True)
class FoldSpan:
    """Positions of one fold's training and test windows in the day index."""

    train: slice
    test: slice


def fold_spans(n_days: int, settings: Settings) -> list[FoldSpan]:
    """Every fold that fits in ``n_days``: train, then test, then roll forward by ``step``."""
    spans: list[FoldSpan] = []
    start = 0
    while start + settings.train_days + settings.test_days <= n_days:
        mid = start + settings.train_days
        spans.append(FoldSpan(slice(start, mid), slice(mid, mid + settings.test_days)))
        start += settings.step
    return spans


@dataclass
class WalkForward:
    """Everything the walk-forward produced, per method."""

    folds: int
    #: Out-of-sample daily combined PnL per method, one series over the scored days.
    oos: dict[str, pd.Series] = field(default_factory=dict)
    #: Average sum of absolute weight changes between consecutive fold allocations.
    turnover: dict[str, float] = field(default_factory=dict)
    #: Share of folds in which the method's test-window average PnL beat equal weight's.
    fold_win_rate: dict[str, float] = field(default_factory=dict)
    #: Alpha IDs selected in each fold, in fold order.
    selections: list[list[str]] = field(default_factory=list)
    #: Folds skipped because no combination could be formed.
    skipped_folds: int = 0


def run_walkforward(pnl: pd.DataFrame, settings: Settings) -> WalkForward:
    """Run every fold over ``pnl`` (rows are days, columns are eligible Alphas).

    An Alpha takes part in a fold only if it has PnL on every day of that fold's training and
    test windows. Availability is checked, but no PnL value is looked at to decide membership,
    so a fold's choices do not depend on how it performed.
    """
    spans = fold_spans(len(pnl.index), settings)
    result = WalkForward(folds=0)
    if not spans:
        return result
    pieces: dict[str, list[pd.Series]] = {m: [] for m in METHODS}
    wins: dict[str, int] = dict.fromkeys(METHODS, 0)
    previous: dict[str, pd.Series] = {}
    changes: dict[str, list[float]] = {m: [] for m in METHODS}
    scored = 0

    for span in spans:
        train = pnl.iloc[span.train]
        test = pnl.iloc[span.test]
        usable = [c for c in pnl.columns if train[c].notna().all() and test[c].notna().all()]
        chosen = greedy_select(train[usable], settings) if usable else []
        if len(chosen) < 2:
            result.skipped_folds += 1
            continue
        scored += 1
        result.selections.append(chosen)
        fold_mean: dict[str, float] = {}
        for method in METHODS:
            allocation = allocate(method, train[chosen], settings)
            weights = allocation.weights
            combined = pd.Series(
                test[chosen].to_numpy() @ weights.reindex(chosen).to_numpy(),
                index=test.index,
            )
            pieces[method].append(combined)
            fold_mean[method] = float(combined.mean())
            if method in previous:
                names = previous[method].index.union(weights.index)
                moved = weights.reindex(names, fill_value=0.0) - previous[method].reindex(
                    names, fill_value=0.0
                )
                changes[method].append(float(moved.abs().sum()))
            previous[method] = weights
        for method in METHODS[1:]:
            if fold_mean[method] > fold_mean["equal_weight"]:
                wins[method] += 1

    result.folds = scored
    if scored == 0:
        return result
    for method in METHODS:
        series = pd.concat(pieces[method])
        # Overlapping test windows (step < test_days) would count a day twice.
        result.oos[method] = series[~series.index.duplicated(keep="first")]
        result.turnover[method] = float(np.mean(changes[method])) if changes[method] else 0.0
        result.fold_win_rate[method] = (
            wins[method] / scored if method != "equal_weight" else float("nan")
        )
    return result


def combo_stats(daily: pd.Series, settings: Settings) -> dict[str, float]:
    """Out-of-sample figures for one method's combined PnL.

    ``sharpe`` is annualised. ``max_drawdown`` is the largest fall from a running peak of the
    cumulative PnL, in the series' own unit, never negative.
    """
    values = daily.to_numpy(dtype=float)
    cumulative = np.cumsum(values)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cumulative]))[1:]
    drawdown = float(np.max(peak - cumulative)) if values.size else 0.0
    return {
        "sharpe": sharpe(values, settings.periods_per_year),
        "max_drawdown": max(0.0, drawdown),
        "days": float(values.size),
    }


def stationary_bootstrap_indices(n: int, mean_block: float, rng: np.random.Generator) -> np.ndarray:
    """One resample of ``range(n)`` built from blocks of random length (Politis & Romano, 1994).

    A block starts at a random day and runs on, wrapping at the end, for a geometric length with
    the given mean. Blocks keep the day-to-day structure of PnL intact; a plain resample of days
    would scramble it and understate how uncertain the Sharpe gap is.
    """
    if n == 0:
        return np.empty(0, dtype=np.int64)
    new_block = rng.random(n) < (1.0 / mean_block)
    new_block[0] = True
    starts = rng.integers(0, n, size=n)
    positions = np.arange(n)
    anchor = np.maximum.accumulate(np.where(new_block, positions, 0))
    return (starts[anchor] + (positions - anchor)) % n


@dataclass(frozen=True)
class MethodVerdict:
    """A method's Sharpe gap over equal weight, with its bootstrap interval."""

    method: Method
    gain: float
    ci_low: float
    ci_high: float
    beats_equal_weight: bool


@dataclass(frozen=True)
class Judgement:
    """Which method the plan uses, and the one-line reason shown on screen."""

    winner: Method
    reason: str
    verdicts: tuple[MethodVerdict, ...]


def sharpe_gap_interval(
    method_daily: np.ndarray, equal_daily: np.ndarray, settings: Settings
) -> tuple[float, float, float]:
    """The Sharpe gap (method minus equal weight) and its 95% stationary-bootstrap interval.

    Both series are resampled on the same days, so the comparison is paired: market days that
    were good for one are good for the other too, which removes that shared noise.
    """
    a = np.asarray(method_daily, dtype=float)
    b = np.asarray(equal_daily, dtype=float)
    gain = sharpe(a, settings.periods_per_year) - sharpe(b, settings.periods_per_year)
    rng = np.random.default_rng(settings.seed)
    gaps = np.empty(settings.bootstrap_samples)
    for k in range(settings.bootstrap_samples):
        idx = stationary_bootstrap_indices(a.size, settings.bootstrap_block, rng)
        gaps[k] = sharpe(a[idx], settings.periods_per_year) - sharpe(
            b[idx], settings.periods_per_year
        )
    finite = gaps[np.isfinite(gaps)]
    if finite.size == 0:
        return float(gain), math.nan, math.nan
    low, high = np.percentile(finite, [2.5, 97.5])
    return float(gain), float(low), float(high)


def judge(walk: WalkForward, settings: Settings) -> Judgement:
    """Pick the plan's method: the best out-of-sample Sharpe among those that beat equal weight
    beyond the bootstrap's uncertainty, otherwise equal weight."""
    equal = walk.oos["equal_weight"]
    verdicts: list[MethodVerdict] = []
    for method in METHODS[1:]:
        gain, low, high = sharpe_gap_interval(
            walk.oos[method].to_numpy(), equal.to_numpy(), settings
        )
        verdicts.append(
            MethodVerdict(
                method=method,
                gain=gain,
                ci_low=low,
                ci_high=high,
                beats_equal_weight=bool(math.isfinite(low) and low > 0),
            )
        )
    winners = [v for v in verdicts if v.beats_equal_weight]
    if not winners:
        reason = "No method beat equal weight out of sample; using equal weight."
        return Judgement("equal_weight", reason, tuple(verdicts))
    best = max(winners, key=lambda v: _oos_sharpe(walk, v.method, settings))
    reason = (
        f"{best.method} beat equal weight out of sample (Sharpe gap {best.gain:+.2f}, "
        f"95% interval {best.ci_low:+.2f} to {best.ci_high:+.2f})."
    )
    return Judgement(best.method, reason, tuple(verdicts))


def _oos_sharpe(walk: WalkForward, method: str, settings: Settings) -> float:
    value = sharpe(walk.oos[method].to_numpy(), settings.periods_per_year)
    return value if math.isfinite(value) else -math.inf
