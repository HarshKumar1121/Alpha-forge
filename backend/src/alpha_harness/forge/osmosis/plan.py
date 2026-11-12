"""The plan: one integer allocation per scope, and the CSV the user types from.

A scope is one (region, delay) pair. Alphas from different scopes trade different universes on
different calendars, so each scope is planned alone and sums to its own 100,000 points.

Every submitted Alpha with stored PnL is a candidate. The Osmosis check is shown with each Alpha
but never gates it (see :mod:`scope`). The steps for each scope are in :func:`scope.analyse_scope`:
the window, the greedy ranking, the recommended N, the walk-forward method, and the points.

This module turns that analysis into the plan that ``/plan`` and ``/plan.csv`` return, with each
scope's default N and method. Nothing here calls BRAIN or spends a simulation. The inputs are PnL
and figures already stored locally, and the outputs are a plan and a CSV the user enters on
BRAIN's site by hand (hard rule 1). The plan reports combination statistics only. It never
estimates a payment.
"""

import csv
import io
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .scope import ScopeAnalysis, analyse_scope
from .walkforward import Judgement, WalkForward, combo_stats

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pandas as pd

    from .eligibility import OsmosisStatus
    from .settings import Settings


@dataclass(frozen=True)
class MethodRow:
    """One weighting method's out-of-sample figures, from the walk-forward."""

    method: str
    sharpe: float
    max_drawdown: float
    turnover: float
    fold_win_rate: float


@dataclass
class ScopePlan:
    """One scope's plan. ``blocked`` is set instead of ``points`` when there is no plan."""

    region: str
    delay: int
    #: Candidates with stored PnL in this scope (all of them, not only the chosen N).
    eligible_count: int
    blocked: str | None = None
    selected: list[str] = field(default_factory=list)
    method: str | None = None
    decision: str = ""
    judgement: Judgement | None = None
    points: pd.Series | None = None
    weights: pd.Series | None = None
    #: True when the solver had no answer and the plan ships equal weight.
    fallback: bool = False
    folds: int = 0
    skipped_folds: int = 0
    methods: list[MethodRow] = field(default_factory=list)
    #: Each selected Alpha's largest absolute correlation with another selected Alpha.
    max_abs_correlation: dict[str, float] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.region} D{self.delay}"


def method_rows(walk: WalkForward, settings: Settings) -> list[MethodRow]:
    """Every method's out-of-sample figures from one walk-forward, equal weight included."""
    rows: list[MethodRow] = []
    for method, series in walk.oos.items():
        stats = combo_stats(series, settings)
        rows.append(
            MethodRow(
                method=method,
                sharpe=stats["sharpe"],
                max_drawdown=stats["max_drawdown"],
                turnover=walk.turnover[method],
                fold_win_rate=walk.fold_win_rate[method],
            )
        )
    return rows


def plan_from(analysis: ScopeAnalysis, settings: Settings) -> ScopePlan:
    """The plan for one analysed scope: its default allocation, or the reason there is none."""
    plan = ScopePlan(
        region=analysis.region,
        delay=analysis.delay,
        eligible_count=analysis.alpha_count,
        blocked=analysis.blocked,
    )
    if analysis.walk is not None:
        plan.folds = analysis.folds
        plan.skipped_folds = analysis.skipped_folds
        plan.methods = method_rows(analysis.walk, settings)
    if analysis.blocked is not None:
        return plan
    plan.selected = analysis.chosen
    plan.method = analysis.method
    plan.decision = analysis.decision
    plan.judgement = analysis.judgement
    plan.points = analysis.points
    plan.weights = analysis.weights
    plan.fallback = analysis.fallback
    plan.max_abs_correlation = analysis.max_abs_correlation
    return plan


def build_plan(
    pnl: pd.DataFrame,
    region: str,
    delay: int,
    settings: Settings,
    turnover: Mapping[str, float] | None = None,
) -> ScopePlan:
    """The plan for one scope. ``pnl`` has a column per Alpha in this scope, rows are days."""
    analysis = analyse_scope(pnl, region, delay, settings, turnover or {})
    return plan_from(analysis, settings)


def scope_frames(
    pnl: pd.DataFrame, scope_of: Mapping[str, tuple[str, int]]
) -> dict[tuple[str, int], pd.DataFrame]:
    """Each scope's PnL frame: its own columns, and only its own trading days.

    A day that only another scope traded is all-NaN here and would otherwise count as a gap in
    this scope's history.
    """
    by_scope: dict[tuple[str, int], list[str]] = {}
    for alpha_id in pnl.columns:
        key = scope_of.get(alpha_id)
        if key is not None:
            by_scope.setdefault(key, []).append(alpha_id)
    return {key: pnl[alphas].dropna(how="all") for key, alphas in sorted(by_scope.items())}


def plan_all(
    pnl: pd.DataFrame,
    scope_of: Mapping[str, tuple[str, int]],
    settings: Settings,
    turnover: Mapping[str, float] | None = None,
) -> list[ScopePlan]:
    """One plan per scope. ``scope_of`` maps each Alpha ID to its ``(region, delay)``.

    Alphas with no scope entry are left out.
    """
    return [
        build_plan(frame, region, delay, settings, turnover)
        for (region, delay), frame in scope_frames(pnl, scope_of).items()
    ]


CSV_COLUMNS = (
    "generated_at",
    "data_through",
    "region",
    "delay",
    "alpha_id",
    "points",
    "weight",
    "method",
    "osmosis_status",
)


def to_csv(
    plans: list[ScopePlan],
    statuses: Mapping[str, OsmosisStatus],
    generated_at: str,
    data_through: str,
) -> str:
    """The CSV for every planned scope: one row per Alpha, with the points to type into BRAIN.

    Blocked scopes contribute no rows. ``generated_at`` and ``data_through`` are passed in,
    not read from the clock, so the same inputs always give the same file.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for plan in plans:
        if plan.blocked or plan.points is None or plan.weights is None:
            continue
        # A fallback is written into the method column, so 1/N never reads as a chosen method.
        method = f"{plan.method} (fallback)" if plan.fallback else (plan.method or "")
        for alpha_id, pts, weight in zip(
            plan.points.index, plan.points.to_numpy(), plan.weights.to_numpy(), strict=True
        ):
            name = str(alpha_id)
            writer.writerow(
                [
                    generated_at,
                    data_through,
                    plan.region,
                    plan.delay,
                    name,
                    int(pts),
                    f"{float(weight):.6f}",
                    method,
                    statuses[name].status,
                ]
            )
    return buffer.getvalue()
