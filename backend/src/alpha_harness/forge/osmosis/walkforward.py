"""Walk-forward validation: does a weighting method beat equal weight on data it never saw?

The window rolls forward through history. In each fold:

1. Train on an older window: select the Alphas (:mod:`selection`) and fit every method
   (:mod:`weights`). An Alpha is eligible for the fold only if it has PnL on every training day.
2. Test on the next window, which the fit never saw: hold the weights fixed and record the
   combined daily PnL. A chosen Alpha's missing test day counts as zero PnL and is reported.

The out-of-sample daily PnL from all folds is then one long record, and every method is scored
on the same days. That record is split in two, by fold:

- **Selection folds** (the earlier ones) are the only days used to choose a method.
- **Holdout folds** (the most recent ``holdout_folds``) are the only days used to check the
  chosen method's Sharpe gap over equal weight, with a paired bootstrap.

A method wins only if the check on the holdout passes: the 2.5% lower bound of its gap over
equal weight must sit above zero. Otherwise the answer is equal weight. Ties go to equal weight,
because it is the simplest method and has the fewest ways to fail on unseen data.

Known limit: with few folds, no method can show a significant win. :func:`judge` reports the
numbers either way, so "no win" reads as "not enough evidence", not as a verdict on the method.
"""

import math
from dataclasses import dataclass, field, replace
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
    #: Out-of-sample daily combined PnL per method, one series over every scored day.
    oos: dict[str, pd.Series] = field(default_factory=dict)
    #: Average sum of absolute weight changes between consecutive fold allocations.
    turnover: dict[str, float] = field(default_factory=dict)
    #: Share of folds in which the method's test-window average PnL beat equal weight's.
    fold_win_rate: dict[str, float] = field(default_factory=dict)
    #: Alpha IDs selected in each fold, in fold order.
    selections: list[list[str]] = field(default_factory=list)
    #: Folds skipped because fewer than two Alphas had full training data.
    skipped_folds: int = 0
    #: How many of the most recent scored folds are held out of winner selection.
    holdout_folds: int = 0
    #: Out-of-sample days from the earlier folds: the only days used to choose a method.
    oos_select: dict[str, pd.Series] = field(default_factory=dict)
    #: Out-of-sample days from the holdout folds: the only days used to check the choice.
    oos_holdout: dict[str, pd.Series] = field(default_factory=dict)
    #: Chosen-Alpha test days with no PnL, counted as zero with weights held fixed.
    filled_test_days: int = 0


def _dedupe(daily: pd.Series) -> pd.Series:
    """Keep the first copy of each day. Overlapping test windows (step < test_days) would
    otherwise count the same day twice."""
    return daily[~daily.index.duplicated(keep="first")]


def run_walkforward(pnl: pd.DataFrame, settings: Settings) -> WalkForward:
    """Run every fold over ``pnl`` (rows are days, columns are eligible Alphas).

    Membership is decided on the training window alone. An Alpha takes part in a fold only if
    it has PnL on every training day, so a fold's choices never depend on that fold's test
    window. Dropping Alphas that go quiet inside the test window would keep only the survivors
    and flatter the out-of-sample record, so they stay in with their missing days counted as
    zero PnL, and ``filled_test_days`` reports how many days that was.
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
        usable = [c for c in pnl.columns if train[c].notna().all()]
        chosen = greedy_select(train[usable], settings) if usable else []
        if len(chosen) < 2:
            result.skipped_folds += 1
            continue
        scored += 1
        result.selections.append(chosen)
        test_chosen = test[chosen]
        result.filled_test_days += int(test_chosen.isna().sum().sum())
        test_values = test_chosen.fillna(0.0).to_numpy()
        fold_mean: dict[str, float] = {}
        for method in METHODS:
            allocation = allocate(method, train[chosen], settings)
            weights = allocation.weights
            combined = pd.Series(test_values @ weights.reindex(chosen).to_numpy(), index=test.index)
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
    # The holdout needs at least one selection fold before it, so it is empty on short runs.
    holdout = settings.holdout_folds if scored > settings.holdout_folds else 0
    result.holdout_folds = holdout
    for method in METHODS:
        series_list = pieces[method]
        result.oos[method] = _dedupe(pd.concat(series_list))
        if holdout:
            result.oos_holdout[method] = _dedupe(pd.concat(series_list[-holdout:]))
            earlier = pd.concat(series_list[:-holdout])
            earlier = earlier[~earlier.index.isin(result.oos_holdout[method].index)]
            result.oos_select[method] = _dedupe(earlier)
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
    """A method's Sharpe gap over equal weight on the holdout, with its bootstrap interval."""

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
    """Pick the plan's method in two steps, each using different days.

    1. **Choose on the selection folds.** Each method's Sharpe gap over equal weight is measured
       on the earlier folds only. The method with the largest positive gap there is the
       candidate. No confidence interval is used here, so the choice never sees the holdout.
    2. **Check on the holdout folds.** The candidate's gap and paired bootstrap interval are
       measured on the holdout only. It must clear the 2.5% lower bound above zero. This is the
       single test on the holdout, so it needs no multiple-comparison correction.

    The holdout intervals shown for every method are descriptive only. They do not decide
    anything, and they are not corrected for the four comparisons.
    """
    if not walk.holdout_folds or "equal_weight" not in walk.oos_holdout:
        reason = "No holdout folds to check a choice on; using equal weight."
        return Judgement("equal_weight", reason, ())
    n_select = len(walk.oos_select["equal_weight"])
    n_holdout = len(walk.oos_holdout["equal_weight"])
    equal_select = walk.oos_select["equal_weight"].to_numpy(dtype=float)
    equal_hold = walk.oos_holdout["equal_weight"].to_numpy(dtype=float)

    candidates: dict[Method, float] = {}
    for method in METHODS[1:]:
        gap = sharpe(walk.oos_select[method].to_numpy(dtype=float), settings.periods_per_year)
        gap -= sharpe(equal_select, settings.periods_per_year)
        if math.isfinite(gap) and gap > 0:
            candidates[method] = gap

    verdicts: list[MethodVerdict] = []
    for method in METHODS[1:]:
        gain, low, high = sharpe_gap_interval(
            walk.oos_holdout[method].to_numpy(dtype=float), equal_hold, settings
        )
        verdicts.append(
            MethodVerdict(
                method=method,
                gain=gain,
                ci_low=low,
                ci_high=high,
                beats_equal_weight=False,
            )
        )
    if not candidates:
        reason = "No method beat equal weight on the earlier folds; using equal weight."
        return Judgement("equal_weight", reason, tuple(verdicts))

    chosen = max(candidates, key=lambda m: candidates[m])
    check = next(v for v in verdicts if v.method == chosen)
    scope = f"chosen on {n_select} earlier days, checked on {n_holdout} holdout days"
    if math.isfinite(check.ci_low) and check.ci_low > 0:
        verdicts = [
            replace(v, beats_equal_weight=True) if v.method == chosen else v for v in verdicts
        ]
        reason = (
            f"{chosen} beat equal weight on the holdout ({scope}; Sharpe gap "
            f"{check.gain:+.2f}, 95% interval {check.ci_low:+.2f} to {check.ci_high:+.2f})."
        )
        return Judgement(chosen, reason, tuple(verdicts))
    reason = (
        f"{chosen} led on the earlier folds, but its gain did not hold on the holdout "
        f"({scope}; 95% interval {check.ci_low:+.2f} to {check.ci_high:+.2f}); "
        "using equal weight."
    )
    return Judgement("equal_weight", reason, tuple(verdicts))
