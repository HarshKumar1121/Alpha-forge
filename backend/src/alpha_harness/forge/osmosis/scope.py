"""One scope, end to end: its ranking, its N, its method, and its points.

A scope is one (region, delay) pair. Every submitted Alpha in it with stored PnL is a candidate.
BRAIN's ``OSMOSIS_ALLOCATION`` result is shown beside each one but never gates it: the check is
not stored for most Alphas, and BRAIN's own comment marks it as a label, not a filter
(``brain/platform.py``).

The steps, each reading only stored PnL and stored BRAIN figures:

1. **Window.** The latest ``train_days`` if enough candidates are complete over it, otherwise the
   longest trailing window in which the ``min_combination_size``-th best candidate is complete.
   A shorter window is reported as its own length, never hidden.
2. **Ranking.** Greedy forward selection over every complete candidate, with no size limit and
   no stopping rule, so the whole order is known. Each step adds the Alpha that most raises the
   equal-weight combined Sharpe on the window, skipping any Alpha correlated above the cap with
   one already ranked. Those skipped are the correlation exclusions, each named against the
   ranked Alpha it collides with.
3. **Recommended N.** Each Alpha is counted in *effective bets*: the participation ratio of
   the correlation matrix's eigenvalues (:mod:`effective_bets`). Ten copies of one signal are
   about one bet. The recommendation is the smallest N, never below ``min_eligible``, after
   which one more Alpha raises effective bets by less than ``bets_min_gain``. The Sharpe curve
   is still drawn beside it, but it no longer sets N. The ``min_eligible`` floor is an
   unverified rule of thumb, shown as one.
4. **Method.** The walk-forward winner when the scope has enough history and the winner beat
   equal weight on the holdout folds. Otherwise hierarchical risk parity (HRP), which uses no
   expected returns and so needs no history to estimate them, labelled as not validated.
5. **Points.** The chosen method is fitted on the top N over the window and rounded to whole
   points that sum to the scope's total.

Selection uses only the window's data, and the walk-forward is the only source of a
"validated" label (CLAUDE.md rule 6). Nothing here calls BRAIN, spends a simulation or submits
an Alpha. The one exception to "the window's data" is the walk-forward, which judges methods on
folds it scores itself.
"""

import hashlib
import math
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd

from .effective_bets import bets_curve, recommended_size
from .points import to_points
from .selection import Pick, greedy_trace, min_combination_size
from .walkforward import Judgement, WalkForward, combo_stats, judge, run_walkforward
from .weights import Method, allocate

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .settings import Settings

#: Cache of walk-forward judgements. Changing N or method on screen must not rerun the walk.
_JUDGED: dict[tuple[object, ...], tuple[WalkForward, Judgement | None]] = {}
_JUDGED_LIMIT = 64


@dataclass(frozen=True)
class Exclusion:
    """A candidate left out of the ranking, with the reason shown on screen."""

    alpha_id: str
    #: ``correlation``: above the cap with a ranked Alpha. ``gap``: missing PnL inside the
    #: selection window. ``flat``: no day-to-day variation, so no correlation exists.
    reason: Literal["correlation", "gap", "flat"]
    #: The ranked Alpha it correlates with most strongly (``correlation`` only).
    correlated_with: str | None = None
    #: Absolute daily-PnL correlation with that Alpha over the selection window.
    correlation: float | None = None


@dataclass(frozen=True)
class CurvePoint:
    """The combination after N Alphas: its training-window Sharpe and what the last one added."""

    n: int
    combined_sharpe: float
    marginal_sharpe: float


@dataclass
class ScopeAnalysis:
    """Everything the screen shows for one scope. ``blocked`` is set instead of a plan."""

    region: str
    delay: int
    alpha_count: int
    blocked: str | None = None
    #: Days of complete history the selection used.
    window_days: int = 0
    have_days: int = 0
    need_days: int = 0
    folds: int = 0
    skipped_folds: int = 0
    walk: WalkForward | None = None
    judgement: Judgement | None = None
    validated: bool = False
    banner: str | None = None
    default_method: Method = "hrp"
    requested_method: Method = "hrp"
    #: The method whose weights ship. Equal weight when the requested method had no solution.
    method: Method | None = None
    fallback: bool = False
    decision: str = ""
    n: int = 0
    n_min: int = 0
    n_max: int = 0
    recommended_n: int = 0
    #: Fewer ranked Alphas than the unverified minimum: the scope is shown with a warning.
    below_minimum: bool = False
    curve: list[CurvePoint] = field(default_factory=list)
    #: Effective number of independent bets of the top-k ranked Alphas, for each k.
    bets: list[float] = field(default_factory=list)
    ranking: list[Pick] = field(default_factory=list)
    chosen: list[str] = field(default_factory=list)
    excluded: list[Exclusion] = field(default_factory=list)
    max_abs_correlation: dict[str, float] = field(default_factory=dict)
    weights: pd.Series | None = None
    points: pd.Series | None = None
    #: Daily PnL over the selection window: ``allocation`` and ``equal`` columns.
    daily: pd.DataFrame | None = None
    #: Combined statistics over the selection window, for ``allocation`` and ``equal``.
    combined: dict[str, dict[str, float | None]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _trailing_run(series: pd.Series) -> int:
    """Days, counted back from the last day, over which this Alpha has PnL with no gap."""
    run = 0
    for present in series.notna().to_numpy()[::-1]:
        if not present:
            break
        run += 1
    return run


def judged_walk(pnl: pd.DataFrame, settings: Settings) -> tuple[WalkForward, Judgement | None]:
    """The walk-forward and its judgement, computed once per distinct input."""
    # The values are part of the key: frames of the same shape can hold different PnL.
    values = np.ascontiguousarray(pnl.to_numpy(dtype=float))
    key = (
        tuple(pnl.columns),
        tuple(str(d) for d in pnl.index[[0, -1]]) if len(pnl.index) else (),
        hashlib.blake2b(values.tobytes(), digest_size=16).hexdigest(),
        repr(settings),
    )
    hit = _JUDGED.get(key)
    if hit is not None:
        return hit
    walk = run_walkforward(pnl, settings)
    judgement = judge(walk, settings) if walk.folds >= settings.min_folds else None
    if len(_JUDGED) >= _JUDGED_LIMIT:
        _JUDGED.clear()
    _JUDGED[key] = (walk, judgement)
    return walk, judgement


def _validated_winner(judgement: Judgement | None) -> Method | None:
    """The walk-forward winner, only when it beat equal weight on the holdout folds."""
    if judgement is None or judgement.winner == "equal_weight":
        return None
    passed = any(v.method == judgement.winner and v.beats_equal_weight for v in judgement.verdicts)
    return judgement.winner if passed else None


def analyse_scope(
    pnl: pd.DataFrame,
    region: str,
    delay: int,
    settings: Settings,
    turnover: Mapping[str, float],
    n: int | None = None,
    method: Method | None = None,
) -> ScopeAnalysis:
    """The full analysis of one scope. ``pnl`` has a column per candidate, rows are its days.

    ``n`` and ``method`` are the user's choices; ``None`` means the recommendation and the
    default method. ``turnover`` maps each Alpha to its BRAIN turnover, for the combined figures.
    """
    pnl = pnl.copy()
    pnl.columns = pnl.columns.astype(str)
    cols = list(pnl.columns)
    count = len(cols)
    out = ScopeAnalysis(region=region, delay=delay, alpha_count=count)
    min_size = min_combination_size(settings)
    if count < min_size:
        out.blocked = (
            f"Too few alphas to allocate under the {round(settings.cap * 100)}% cap: {count} "
            f"with PnL, need at least {min_size}."
        )
        return out

    # Walk-forward: the only source of a "validated" label.
    walk, judgement = judged_walk(pnl, settings)
    out.walk = walk
    out.judgement = judgement
    out.folds = walk.folds
    out.skipped_folds = walk.skipped_folds
    out.have_days = len(pnl.index)
    out.need_days = (
        settings.train_days + settings.test_days + (settings.min_folds - 1) * settings.step
    )
    winner = _validated_winner(judgement)
    if winner is not None:
        out.validated = True
        out.default_method = winner
        out.decision = judgement.reason if judgement else ""
    else:
        out.default_method = "hrp"
        if judgement is None:
            out.banner = (
                f"Not validated: not enough history for walk-forward (have {out.have_days} "
                f"days, need ~{out.need_days}). Using HRP, which uses no expected returns."
            )
        else:
            out.banner = (
                "Not validated: no method beat equal weight on the holdout folds. Using HRP, "
                "which uses no expected returns."
            )
            out.decision = judgement.reason
    requested: Method = method or out.default_method
    out.requested_method = requested

    # Window: as in the module docstring.
    runs = {c: _trailing_run(pnl[c]) for c in cols}
    kth = sorted(runs.values(), reverse=True)[min_size - 1]
    window = min(settings.train_days, kth)
    out.window_days = window
    if window < 2:
        out.blocked = (
            f"Not enough complete history: fewer than {min_size} Alphas have two or more days of "
            "PnL with no gap, so no combination can be ranked."
        )
        return out

    tail = pnl.iloc[-window:]
    complete = [c for c in cols if runs[c] >= window]
    gaps = [c for c in cols if runs[c] < window]

    # Ranking over every complete candidate: no size limit and no stopping rule, so the whole
    # order is known and N can be chosen afterwards. The correlation cap still applies.
    full = replace(settings, max_selected=max(len(complete), 1), min_sharpe_gain=-math.inf)
    ranking = greedy_trace(tail[complete], full) if complete else []
    total = len(ranking)
    if total < min_size:
        out.blocked = (
            f"Only {total} Alphas could form a combination over the latest {window} days; "
            f"the cap needs at least {min_size}."
        )
        return out

    out.ranking = ranking
    out.curve = [
        CurvePoint(k + 1, p.combined_sharpe, p.marginal_sharpe) for k, p in enumerate(ranking)
    ]

    # Recommended N: the smallest size, at least the floor, after which one more Alpha adds
    # under bets_min_gain of effective bets (the rule in the module docstring, step 3).
    ranked_frame = tail[[p.alpha for p in ranking]]
    out.bets = bets_curve(ranked_frame)
    out.recommended_n = recommended_size(
        out.bets, min(settings.min_eligible, total), settings.bets_min_gain
    )
    out.n_min = min_size
    out.n_max = min(total, int(1.0 / settings.floor + 1e-9))
    out.n = min(max(out.recommended_n if n is None else n, min_size), out.n_max)
    out.below_minimum = total < settings.min_eligible

    chosen = [p.alpha for p in ranking[: out.n]]
    out.chosen = chosen
    tail_chosen = tail[chosen]
    allocation = allocate(requested, tail_chosen, settings)
    out.fallback = allocation.fallback
    out.method = "equal_weight" if allocation.fallback else requested
    out.weights = allocation.weights
    out.points = to_points(allocation.weights, settings.total_points, settings.floor, settings.cap)
    if allocation.fallback:
        out.warnings.append(f"{requested} found no solution on this window; using equal weight.")
    if out.n < settings.min_eligible:
        out.warnings.append(
            f"Below {settings.min_eligible} alphas (unverified Osmosis minimum): this allocation "
            f"holds {out.n}, and it may not count on BRAIN."
        )
    if walk.skipped_folds:
        out.warnings.append(
            f"{walk.skipped_folds} walk-forward fold(s) skipped: fewer than 2 Alphas could be "
            "selected on their training window."
        )

    # Exclusions: candidates the ranking could not add, and those with a gap in the window.
    for alpha in gaps:
        out.excluded.append(Exclusion(alpha, "gap"))
    corr = tail[complete].corr().abs().to_numpy()
    where = {c: i for i, c in enumerate(complete)}
    ranked = [p.alpha for p in ranking]
    for alpha in complete:
        if alpha in ranked:
            continue
        values = [(float(corr[where[alpha], where[s]]), s) for s in ranked]
        finite = [(v, s) for v, s in values if math.isfinite(v)]
        if not finite:
            out.excluded.append(Exclusion(alpha, "flat"))
            continue
        top, partner = max(finite)
        out.excluded.append(Exclusion(alpha, "correlation", partner, top))
    out.excluded.sort(key=lambda e: (e.reason != "correlation", -(e.correlation or 0.0)))

    chosen_corr = tail_chosen.corr().abs().to_numpy()
    at = {c: i for i, c in enumerate(chosen)}
    out.max_abs_correlation = {
        a: float(np.nanmax([chosen_corr[at[a], at[s]] for s in chosen if s != a])) for a in chosen
    }

    # Combined statistics over the selection window, for the allocation and for equal weight.
    alloc_daily = tail_chosen.to_numpy() @ allocation.weights.reindex(chosen).to_numpy()
    equal_daily = tail_chosen.mean(axis=1).to_numpy()
    out.daily = pd.DataFrame(
        {"allocation": alloc_daily, "equal": equal_daily}, index=tail_chosen.index
    )
    own = np.array([float(turnover.get(a, math.nan)) for a in chosen])
    weights = allocation.weights.reindex(chosen).to_numpy()
    out.combined = {
        "allocation": _stats(out.daily["allocation"], settings, float(np.dot(weights, own))),
        "equal": _stats(out.daily["equal"], settings, float(np.mean(own))),
    }
    out.decision = out.decision or "Walk-forward did not run; no method was judged."
    return out


def _stats(daily: pd.Series, settings: Settings, turnover: float) -> dict[str, float | None]:
    """Sharpe and drawdown of a combined PnL, and the turnover it inherits from its Alphas."""
    figures = combo_stats(daily, settings)
    return {
        "sharpe": figures["sharpe"],
        "max_drawdown": figures["max_drawdown"],
        "turnover": turnover if math.isfinite(turnover) else None,
    }
