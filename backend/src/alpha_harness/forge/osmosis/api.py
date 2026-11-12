"""``/api/forge/osmosis``: the Osmosis Allocator's scopes, plans and CSV, from stored PnL.

Read-only. Every number comes from PnL and BRAIN figures already stored locally, the same reads
the Portfolio page makes: nothing here calls BRAIN or spends a simulation (CLAUDE.md rule 4), and
nothing submits an Alpha or tells BRAIN anything (rule 1). The GET routes are the whole surface;
a test pins that no write route exists.

Routes:

- ``/summary``: every scope at a glance, with its recommended N and chosen method.
- ``/scope``: one scope in full, for any N and method. Changing either re-runs only the cheap
  steps; the walk-forward is cached (see :mod:`scope`).
- ``/plan`` and ``/plan.csv``: every scope at its default N and method, as points to type into
  BRAIN by hand.

The points are what the user types into BRAIN by hand. The response carries combination
statistics only. It never estimates a payment, because BRAIN's multiplier formula is not known
to this tool.
"""

import asyncio
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd
from fastapi import APIRouter
from fastapi.responses import Response

from ...api.deps import State  # noqa: TC001 - FastAPI reads route parameter types at runtime
from ...db.models import utcnow
from ...forge.storage.access import touch_pnl  # forge: storage TTL must not expire what was read
from ...schemas import Out
from ...vault.store import json_list
from ...vault.yields import SUBMITTED, checks_of
from .eligibility import OsmosisStatus, osmosis_status
from .plan import ScopePlan, method_rows, plan_all, scope_frames, to_csv
from .scope import ScopeAnalysis, analyse_scope
from .settings import Settings, load_settings
from .weights import Method  # noqa: TC001 - FastAPI reads route parameter types at runtime
from .why import build_why

if TYPE_CHECKING:
    from ...state import AppState

router = APIRouter(prefix="/api/forge/osmosis", tags=["forge-osmosis"])


class OsmosisPoint(Out):
    alpha_id: str
    #: Whole points: the number the user types into BRAIN. Each scope's points sum to 100,000.
    points: int
    #: The same allocation as a share of the scope's total, from 0 to 1.
    weight: float
    #: Largest absolute daily-PnL correlation with another Alpha in this combination, over the
    #: latest training window. ``None`` when it cannot be measured.
    max_abs_correlation: float | None
    #: The Alpha's stored Osmosis check: ``eligible``, ``absent``, ``refused`` and so on.
    osmosis_status: str


class OsmosisMethod(Out):
    method: str
    #: Annualised Sharpe of the method's out-of-sample PnL over every fold.
    sharpe: float | None
    #: Largest fall from a running peak of the out-of-sample cumulative PnL, in PnL units.
    max_drawdown: float | None
    #: Average sum of absolute weight changes between consecutive fold allocations.
    turnover: float | None
    #: Share of folds where the method's mean daily PnL beat equal weight's. ``None`` for
    #: equal weight itself.
    fold_win_rate: float | None
    #: Sharpe gap over equal weight, measured on the holdout folds only. ``None`` for equal weight.
    gain: float | None
    #: Lower and upper ends of the 95% paired bootstrap interval for ``gain``.
    ci_low: float | None
    ci_high: float | None
    #: Whether the holdout check passed (the lower bound above zero). ``None`` for equal weight.
    passed_holdout: bool | None
    #: True for the method this scope ships.
    is_plan: bool


class OsmosisScope(Out):
    region: str
    delay: int
    #: Submitted Alphas in this scope with stored PnL: all candidates, not only the chosen N.
    eligible_count: int
    #: Why no plan was made, in plain words, with the history or Alphas still needed.
    blocked: str | None
    #: The method the plan ships. ``None`` when blocked.
    method: str | None
    #: One line on how the method was chosen and checked.
    decision: str
    #: True when the solver had no answer and the plan ships equal weight.
    fallback: bool
    #: Walk-forward folds scored.
    folds: int
    #: Folds skipped because fewer than two Alphas had full training data.
    skipped_folds: int
    points: list[OsmosisPoint]
    methods: list[OsmosisMethod]


class OsmosisSettings(Out):
    min_eligible: int
    min_folds: int
    holdout_folds: int
    train_days: int
    test_days: int
    step: int


class OsmosisPlan(Out):
    #: When this plan was computed, ISO 8601 UTC.
    generated_at: str
    #: The last date of stored PnL the plan used.
    data_through: str | None
    settings: OsmosisSettings
    scopes: list[OsmosisScope]


class OsmosisCurvePoint(Out):
    #: How many of the top-ranked Alphas the combination holds.
    n: int
    #: Sharpe of the equal-weight combination of the top ``n``, over the selection window.
    combined_sharpe: float | None
    #: How much the ``n``th Alpha added over the ``n - 1`` before it.
    marginal_sharpe: float | None


class OsmosisBetsPoint(Out):
    #: How many of the top-ranked Alphas the combination holds.
    n: int
    #: Effective number of independent bets of the top ``n``: the participation ratio of the
    #: correlation matrix's eigenvalues. Ten copies of one signal count as about one bet.
    effective_bets: float
    #: How much the ``n``th Alpha raised effective bets over the ``n - 1`` before it, as a share.
    #: ``None`` for the first Alpha.
    gain: float | None


class OsmosisWhy(Out):
    """The "Why this allocation" panel, one sentence per field."""

    method: str | None
    validated: bool
    method_line: str
    size_line: str
    evidence_line: str
    decay_line: str
    correlation_exclusions: int


class OsmosisRow(Out):
    #: Greedy rank: 1 is the Alpha that most raised the combination's Sharpe when it was added.
    rank: int
    alpha_id: str
    classifications: list[str]
    universe: str | None
    sharpe: float | None
    turnover: float | None
    fitness: float | None
    drawdown: float | None
    #: BRAIN's production correlation with the platform's submitted Alphas.
    prod_correlation: float | None
    #: Largest absolute correlation with another selected Alpha, over the selection window.
    max_abs_correlation: float | None
    osmosis_status: str
    weight: float
    points: int


class OsmosisStats(Out):
    sharpe: float | None
    max_drawdown: float | None
    turnover: float | None


class OsmosisCombined(Out):
    #: The chosen allocation's combined figures.
    allocation: OsmosisStats
    #: Equal weight over the same Alphas, for comparison.
    equal: OsmosisStats


class OsmosisChartPoint(Out):
    date: str
    #: Cumulative PnL of the allocation over the selection window.
    allocation: float
    #: Cumulative PnL of equal weight over the same Alphas and window.
    equal: float


class OsmosisExcluded(Out):
    alpha_id: str
    #: ``correlation``, ``gap`` or ``flat``: why the Alpha is not in the ranking.
    reason: str
    correlated_with: str | None
    correlation: float | None
    detail: str


class OsmosisScopeView(Out):
    generated_at: str
    data_through: str | None
    region: str
    delay: int
    #: Submitted Alphas in this scope with stored PnL.
    alpha_count: int
    blocked: str | None
    min_eligible: int
    #: The fewest Alphas any combination can hold: the cap's floor, ``ceil(1 / cap)``.
    n_min: int
    #: The most Alphas a combination can hold: the ranked Alphas, and the floor's limit.
    n_max: int
    #: The N the screen starts from: the smallest one after which extra Alphas add little.
    recommended_n: int
    #: The N this view allocates.
    n: int
    #: The method this view allocates with, after any fallback. ``None`` when blocked.
    method: str | None
    requested_method: str
    default_method: str
    #: True only when the walk-forward ran and the default method beat equal weight on holdout.
    validated: bool
    #: The "not validated" banner, when the default is not validated.
    banner: str | None
    fallback: bool
    decision: str
    #: Days of complete history the selection used.
    window_days: int
    have_days: int
    need_days: int
    folds: int
    skipped_folds: int
    #: True when the scope has fewer ranked Alphas than the unverified minimum.
    below_minimum: bool
    curve: list[OsmosisCurvePoint]
    #: Effective number of independent bets as the top-k ranked Alphas grow.
    bets_curve: list[OsmosisBetsPoint]
    why: OsmosisWhy
    rows: list[OsmosisRow]
    total_points: int
    combined: OsmosisCombined | None
    chart: list[OsmosisChartPoint]
    excluded: list[OsmosisExcluded]
    #: Ranked Alphas past N: not in this allocation, but not excluded for correlation either.
    ranked_beyond_n: int
    methods: list[OsmosisMethod]
    warnings: list[str]


class OsmosisSummaryRow(Out):
    region: str
    delay: int
    alpha_count: int
    blocked: str | None
    recommended_n: int
    n: int
    method: str | None
    default_method: str
    validated: bool
    banner: str | None
    #: Sharpe of the combined allocation at its N and method, over the selection window.
    combined_sharpe: float | None
    below_minimum: bool


class OsmosisSummary(Out):
    generated_at: str
    data_through: str | None
    min_eligible: int
    scopes: list[OsmosisSummaryRow]
    #: Submitted parent alphas (region ``ALL``): left out everywhere, with no PnL of their own.
    parents_excluded: list[str]


@dataclass
class _Inputs:
    """Everything the planners read from storage: daily PnL, checks, scopes, settings, figures."""

    pnl: pd.DataFrame
    statuses: dict[str, OsmosisStatus]
    scope_of: dict[str, tuple[str, int]]
    settings: Settings
    generated_at: str
    data_through: str | None
    #: Each submitted Alpha's stored row, keyed by ID: BRAIN's figures, shown as they are.
    facts: dict[str, dict[str, Any]]
    parents: list[str]


def _num(value: object) -> float | None:
    """A finite number, or ``None``: NaN has no JSON form and means "not measurable here"."""
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None


def _frame(series: dict[str, dict[Any, tuple[float, float]]], ids: list[str]) -> pd.DataFrame:
    """Daily PnL with a column per Alpha, rows on the union of their dates, NaN where an Alpha
    has no row. Aligned by date, never by position: a scope's days must line up with each other."""
    frame = pd.DataFrame(
        {a: pd.Series({d: v[0] for d, v in series[a].items()}, dtype=float) for a in ids}
    )
    frame.index = pd.to_datetime(frame.index)
    return frame.sort_index()


async def _parents(state: AppState) -> list[str]:
    """Submitted region-agnostic parents: left out of every scope, named once on the summary."""
    rows = await state.alphas.catalog.query(
        f"SELECT a.alpha_id FROM alpha a WHERE {SUBMITTED} AND a.sim_type = 'RA_PARENT' "  # noqa: S608
        "ORDER BY a.alpha_id"
    )
    return [str(r["alpha_id"]) for r in rows]


async def _load(state: AppState) -> _Inputs:
    """The stored submissions, read once: the one place the reads happen for every plan."""
    rows = await state.alphas.submitted_members()
    ids = [str(r["alpha_id"]) for r in rows]
    series = await state.alphas.series(ids)
    meta = await state.alphas.by_ids(ids)
    await touch_pnl(state.db, ids)  # forge: storage TTL must not expire what was just read
    statuses = {a: osmosis_status(checks_of((meta.get(a) or {}).get("checks"))) for a in ids}
    scope_of = {
        str(r["alpha_id"]): (str(r["region"]), int(r["delay"]))
        for r in rows
        if r.get("region") and r.get("delay") is not None
    }
    pnl = _frame(series, [a for a in ids if series.get(a)])
    data_through = None if pnl.empty else pnl.index.max().date().isoformat()
    return _Inputs(
        pnl=pnl,
        statuses=statuses,
        scope_of=scope_of,
        settings=load_settings(),
        generated_at=utcnow().isoformat(timespec="seconds"),
        data_through=data_through,
        facts={str(r["alpha_id"]): dict(r) for r in rows},
        parents=await _parents(state),
    )


def _turnover(data: _Inputs) -> dict[str, float]:
    """Each Alpha's BRAIN turnover, where stored: the combined figures weight these."""
    found: dict[str, float] = {}
    for alpha_id, row in data.facts.items():
        value = _num(row.get("turnover"))
        if value is not None:
            found[alpha_id] = value
    return found


def _analyse(
    data: _Inputs, region: str, delay: int, n: int | None, method: Method | None
) -> ScopeAnalysis:
    """One scope's analysis, or a blocked one when the scope holds no PnL at all."""
    frame = scope_frames(data.pnl, data.scope_of).get((region, delay))
    if frame is None:
        return ScopeAnalysis(
            region=region,
            delay=delay,
            alpha_count=0,
            blocked=f"No submitted Alphas with stored PnL in {region} D{delay}.",
        )
    return analyse_scope(frame, region, delay, data.settings, _turnover(data), n, method)


def _analyse_all(data: _Inputs) -> list[ScopeAnalysis]:
    """Every scope at its default N and method, for the summary and the plan."""
    return [
        analyse_scope(frame, region, delay, data.settings, _turnover(data))
        for (region, delay), frame in scope_frames(data.pnl, data.scope_of).items()
    ]


def _method_views(analysis: ScopeAnalysis, settings: Settings) -> list[OsmosisMethod]:
    """Every method against equal weight: the walk-forward's table, with the holdout verdicts."""
    if analysis.walk is None:
        return []
    verdicts = {str(v.method): v for v in analysis.judgement.verdicts} if analysis.judgement else {}
    views: list[OsmosisMethod] = []
    for row in method_rows(analysis.walk, settings):
        verdict = verdicts.get(row.method)
        views.append(
            OsmosisMethod(
                method=row.method,
                sharpe=_num(row.sharpe),
                max_drawdown=_num(row.max_drawdown),
                turnover=_num(row.turnover),
                fold_win_rate=_num(row.fold_win_rate),
                gain=_num(verdict.gain) if verdict else None,
                ci_low=_num(verdict.ci_low) if verdict else None,
                ci_high=_num(verdict.ci_high) if verdict else None,
                passed_holdout=verdict.beats_equal_weight if verdict else None,
                is_plan=row.method == analysis.method,
            )
        )
    return views


def _rows(analysis: ScopeAnalysis, data: _Inputs) -> list[OsmosisRow]:
    """The allocation's table: one row per chosen Alpha, sorted by points, largest first."""
    if analysis.blocked is not None or analysis.points is None or analysis.weights is None:
        return []
    rank = {p.alpha: i + 1 for i, p in enumerate(analysis.ranking)}
    rows: list[OsmosisRow] = []
    for alpha_id in analysis.chosen:
        fact = data.facts.get(alpha_id, {})
        universe = fact.get("universe")
        rows.append(
            OsmosisRow(
                rank=rank[alpha_id],
                alpha_id=alpha_id,
                classifications=json_list(fact.get("classifications")),
                universe=str(universe) if universe is not None else None,
                sharpe=_num(fact.get("sharpe")),
                turnover=_num(fact.get("turnover")),
                fitness=_num(fact.get("fitness")),
                drawdown=_num(fact.get("drawdown")),
                prod_correlation=_num(fact.get("prod_correlation")),
                max_abs_correlation=_num(analysis.max_abs_correlation.get(alpha_id)),
                osmosis_status=data.statuses[alpha_id].status,
                weight=float(analysis.weights[alpha_id]),
                points=int(analysis.points[alpha_id]),
            )
        )
    rows.sort(key=lambda r: (-r.points, r.rank))
    return rows


_EXCLUSION_DETAIL = {
    "correlation": "Correlation with {partner} is above the cap, so it cannot be added.",
    "gap": "No PnL on every day of the selection window, so it cannot be ranked.",
    "flat": "Its PnL has no day-to-day variation, so its correlation cannot be measured.",
}


def _bets_view(bets: list[float]) -> list[OsmosisBetsPoint]:
    """The effective-bets curve with each step's share of gain, for the chart."""
    points: list[OsmosisBetsPoint] = []
    for k, value in enumerate(bets, start=1):
        step = None if k == 1 or bets[k - 2] <= 0 else value / bets[k - 2] - 1.0
        points.append(OsmosisBetsPoint(n=k, effective_bets=value, gain=_num(step)))
    return points


def _scope_view(analysis: ScopeAnalysis, data: _Inputs) -> OsmosisScopeView:
    """One scope, in full, for the screen."""
    settings = data.settings
    rows = _rows(analysis, data)
    chart: list[OsmosisChartPoint] = []
    if analysis.daily is not None:
        cumulative = analysis.daily.cumsum()
        for when, alloc, equal in zip(
            cumulative.index, cumulative["allocation"], cumulative["equal"], strict=True
        ):
            chart.append(
                OsmosisChartPoint(
                    date=when.date().isoformat(), allocation=float(alloc), equal=float(equal)
                )
            )
    combined = None
    if analysis.combined:
        combined = OsmosisCombined(
            allocation=OsmosisStats(
                **{k: _num(v) for k, v in analysis.combined["allocation"].items()}
            ),
            equal=OsmosisStats(**{k: _num(v) for k, v in analysis.combined["equal"].items()}),
        )
    excluded = [
        OsmosisExcluded(
            alpha_id=e.alpha_id,
            reason=e.reason,
            correlated_with=e.correlated_with,
            correlation=_num(e.correlation),
            detail=_EXCLUSION_DETAIL[e.reason].format(partner=e.correlated_with),
        )
        for e in analysis.excluded
    ]
    bets_curve = _bets_view(analysis.bets)
    why = build_why(analysis, settings)
    return OsmosisScopeView(
        generated_at=data.generated_at,
        bets_curve=bets_curve,
        why=OsmosisWhy(
            method=why.method,
            validated=why.validated,
            method_line=why.method_line,
            size_line=why.size_line,
            evidence_line=why.evidence_line,
            decay_line=why.decay_line,
            correlation_exclusions=why.correlation_exclusions,
        ),
        data_through=data.data_through,
        region=analysis.region,
        delay=analysis.delay,
        alpha_count=analysis.alpha_count,
        blocked=analysis.blocked,
        min_eligible=settings.min_eligible,
        n_min=analysis.n_min,
        n_max=analysis.n_max,
        recommended_n=analysis.recommended_n,
        n=analysis.n,
        method=analysis.method if analysis.blocked is None else None,
        requested_method=analysis.requested_method,
        default_method=analysis.default_method,
        validated=analysis.validated,
        banner=analysis.banner,
        fallback=analysis.fallback,
        decision=analysis.decision,
        window_days=analysis.window_days,
        have_days=analysis.have_days,
        need_days=analysis.need_days,
        folds=analysis.folds,
        skipped_folds=analysis.skipped_folds,
        below_minimum=analysis.below_minimum,
        curve=[
            OsmosisCurvePoint(
                n=c.n,
                combined_sharpe=_num(c.combined_sharpe),
                marginal_sharpe=_num(c.marginal_sharpe),
            )
            for c in analysis.curve
        ],
        rows=rows,
        total_points=sum(r.points for r in rows),
        combined=combined,
        chart=chart,
        excluded=excluded,
        ranked_beyond_n=max(len(analysis.ranking) - analysis.n, 0),
        methods=_method_views(analysis, settings),
        warnings=analysis.warnings,
    )


def _scope_out(plan: ScopePlan, statuses: dict[str, OsmosisStatus]) -> OsmosisScope:
    """One scope's plan, as ``/plan`` returns it."""
    verdicts = {str(v.method): v for v in plan.judgement.verdicts} if plan.judgement else {}
    points: list[OsmosisPoint] = []
    if plan.points is not None and plan.weights is not None:
        for alpha_id, pts, weight in zip(
            plan.points.index, plan.points.to_numpy(), plan.weights.to_numpy(), strict=True
        ):
            name = str(alpha_id)
            points.append(
                OsmosisPoint(
                    alpha_id=name,
                    points=int(pts),
                    weight=float(weight),
                    max_abs_correlation=_num(plan.max_abs_correlation.get(name)),
                    osmosis_status=statuses[name].status,
                )
            )
    methods: list[OsmosisMethod] = []
    for row in plan.methods:
        verdict = verdicts.get(row.method)
        methods.append(
            OsmosisMethod(
                method=row.method,
                sharpe=_num(row.sharpe),
                max_drawdown=_num(row.max_drawdown),
                turnover=_num(row.turnover),
                fold_win_rate=_num(row.fold_win_rate),
                gain=_num(verdict.gain) if verdict else None,
                ci_low=_num(verdict.ci_low) if verdict else None,
                ci_high=_num(verdict.ci_high) if verdict else None,
                passed_holdout=verdict.beats_equal_weight if verdict else None,
                is_plan=row.method == plan.method,
            )
        )
    return OsmosisScope(
        region=plan.region,
        delay=plan.delay,
        eligible_count=plan.eligible_count,
        blocked=plan.blocked,
        method=plan.method,
        decision=plan.decision,
        fallback=plan.fallback,
        folds=plan.folds,
        skipped_folds=plan.skipped_folds,
        points=points,
        methods=methods,
    )


async def _plans(state: AppState) -> tuple[list[ScopePlan], _Inputs]:
    """Every scope's default plan, from the stored submissions."""
    data = await _load(state)
    plans = await asyncio.to_thread(
        plan_all, data.pnl, data.scope_of, data.settings, _turnover(data)
    )
    return plans, data


@router.get("/summary")
async def osmosis_summary(state: State) -> OsmosisSummary:
    """Every scope at a glance: its alpha count, recommended N, method and combined Sharpe."""
    data = await _load(state)
    analyses = await asyncio.to_thread(_analyse_all, data)
    scopes = [
        OsmosisSummaryRow(
            region=a.region,
            delay=a.delay,
            alpha_count=a.alpha_count,
            blocked=a.blocked,
            recommended_n=a.recommended_n,
            n=a.n,
            method=a.method if a.blocked is None else None,
            default_method=a.default_method,
            validated=a.validated,
            banner=a.banner,
            combined_sharpe=_num(a.combined["allocation"]["sharpe"]) if a.combined else None,
            below_minimum=a.below_minimum,
        )
        for a in analyses
    ]
    return OsmosisSummary(
        generated_at=data.generated_at,
        data_through=data.data_through,
        min_eligible=data.settings.min_eligible,
        scopes=scopes,
        parents_excluded=data.parents,
    )


@router.get("/scope")
async def osmosis_scope(
    state: State, region: str, delay: int, n: int | None = None, method: Method | None = None
) -> OsmosisScopeView:
    """One scope in full. ``n`` and ``method`` are optional; omit them for the recommendation and
    the default method. Read-only: nothing here calls BRAIN."""
    data = await _load(state)
    analysis = await asyncio.to_thread(_analyse, data, region, delay, n, method)
    return _scope_view(analysis, data)


@router.get("/plan")
async def osmosis_plan(state: State) -> OsmosisPlan:
    """Every scope's default plan: blocked reasons, the points, and the method comparison."""
    plans, data = await _plans(state)
    return OsmosisPlan(
        generated_at=data.generated_at,
        data_through=data.data_through,
        settings=OsmosisSettings(
            min_eligible=data.settings.min_eligible,
            min_folds=data.settings.min_folds,
            holdout_folds=data.settings.holdout_folds,
            train_days=data.settings.train_days,
            test_days=data.settings.test_days,
            step=data.settings.step,
        ),
        scopes=[_scope_out(p, data.statuses) for p in plans],
    )


@router.get("/plan.csv")
async def osmosis_plan_csv(state: State) -> Response:
    """The same points as a CSV, one row per Alpha, for typing into BRAIN by hand."""
    plans, data = await _plans(state)
    text = to_csv(
        plans,
        data.statuses,
        generated_at=data.generated_at,
        data_through=data.data_through or "",
    )
    return Response(
        text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="osmosis-plan.csv"'},
    )
