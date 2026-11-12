"""The plan: one integer allocation per scope, and the CSV the user types from.

A scope is one (region, delay) pair. Alphas from different scopes trade different universes on
different calendars, so each scope is planned alone and sums to its own 100,000 points.

For each scope the steps are:

1. Keep the eligible Alphas (:mod:`eligibility`). Fewer than ``min_eligible`` blocks the scope,
   and the plan says how many are missing rather than planning a smaller set.
2. Walk forward over the eligible history (:mod:`walkforward`) and judge the methods.
3. Refit the winning method on the most recent training window, using only Alphas complete over
   that window, and round the weights to points (:mod:`points`).

Nothing here calls BRAIN or spends a simulation. The inputs are PnL already stored locally, and
the outputs are a plan and a CSV the user enters on BRAIN's site by hand (hard rule 1).
The plan reports combination statistics only. It never estimates a payment.
"""

import csv
import io
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .eligibility import OsmosisStatus, eligible_ids
from .points import to_points
from .selection import greedy_select, min_combination_size
from .walkforward import Judgement, combo_stats, judge, run_walkforward
from .weights import allocate

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pandas as pd

    from .settings import Settings


@dataclass(frozen=True)
class MethodRow:
    """One method's out-of-sample figures, as shown beside the plan."""

    method: str
    sharpe: float
    max_drawdown: float
    turnover: float
    fold_win_rate: float


@dataclass
class ScopePlan:
    """The plan for one scope. ``blocked`` is set instead of ``points`` when no plan is made."""

    region: str
    delay: int
    eligible_count: int
    blocked: str | None = None
    selected: list[str] = field(default_factory=list)
    method: str | None = None
    decision: str = ""
    judgement: Judgement | None = None
    points: pd.Series | None = None
    weights: pd.Series | None = None
    fallback: bool = False
    folds: int = 0
    methods: list[MethodRow] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.region} D{self.delay}"


def _blocked(region: str, delay: int, eligible: int, reason: str) -> ScopePlan:
    return ScopePlan(region=region, delay=delay, eligible_count=eligible, blocked=reason)


def build_plan(
    pnl: pd.DataFrame,
    statuses: Mapping[str, OsmosisStatus],
    region: str,
    delay: int,
    settings: Settings,
) -> ScopePlan:
    """The plan for one scope. ``pnl`` has a column per Alpha in this scope, rows are days."""
    eligible = [a for a in eligible_ids(statuses) if a in pnl.columns]
    count = len(eligible)
    if count < settings.min_eligible:
        missing = settings.min_eligible - count
        return _blocked(
            region,
            delay,
            count,
            f"{count} eligible Alphas; need {settings.min_eligible}. "
            f"{missing} more must pass the Osmosis check.",
        )

    pnl_e = pnl[eligible]
    walk = run_walkforward(pnl_e, settings)
    if walk.folds == 0:
        return _blocked(
            region,
            delay,
            count,
            f"Not enough history: need {settings.train_days + settings.test_days} days "
            f"of PnL, have {len(pnl_e.index)}.",
        )

    judgement = judge(walk, settings)
    tail = pnl_e.iloc[-settings.train_days :]
    complete = [c for c in tail.columns if tail[c].notna().all()]
    chosen = greedy_select(tail[complete], settings) if complete else []
    if len(chosen) < min_combination_size(settings):
        return _blocked(
            region,
            delay,
            count,
            f"Only {len(chosen)} Alphas could form a combination over the latest "
            f"{settings.train_days} days; the cap needs at least "
            f"{min_combination_size(settings)}.",
        )

    allocation = allocate(judgement.winner, tail[chosen], settings)
    points = to_points(allocation.weights, settings.total_points, settings.floor, settings.cap)
    rows: list[MethodRow] = []
    for m, series in walk.oos.items():
        stats = combo_stats(series, settings)
        rows.append(
            MethodRow(
                method=m,
                sharpe=stats["sharpe"],
                max_drawdown=stats["max_drawdown"],
                turnover=walk.turnover[m],
                fold_win_rate=walk.fold_win_rate[m],
            )
        )
    return ScopePlan(
        region=region,
        delay=delay,
        eligible_count=count,
        selected=chosen,
        method=judgement.winner,
        decision=judgement.reason,
        judgement=judgement,
        points=points,
        weights=allocation.weights,
        fallback=allocation.fallback,
        folds=walk.folds,
        methods=rows,
    )


def plan_all(
    pnl: pd.DataFrame,
    statuses: Mapping[str, OsmosisStatus],
    scope_of: Mapping[str, tuple[str, int]],
    settings: Settings,
) -> list[ScopePlan]:
    """One plan per scope. ``scope_of`` maps each Alpha ID to its ``(region, delay)``.

    Alphas with no scope entry are left out; the caller sees them in ``statuses`` but not here.
    """
    by_scope: dict[tuple[str, int], list[str]] = {}
    for alpha_id in pnl.columns:
        key = scope_of.get(alpha_id)
        if key is not None:
            by_scope.setdefault(key, []).append(alpha_id)
    plans = []
    for (region, delay), alphas in sorted(by_scope.items()):
        plans.append(build_plan(pnl[alphas], statuses, region, delay, settings))
    return plans


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
                    plan.method,
                    statuses[name].status,
                ]
            )
    return buffer.getvalue()
