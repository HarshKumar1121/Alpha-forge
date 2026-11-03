"""``/api/forge/analytics``: real tear-sheet analytics for one BRAIN alpha, built from its
``pnl``, ``turnover`` and ``yearly-stats`` recordsets (docs/forge/BUILD_PLAYBOOK_V2.md, Phase 8).

Read-only: nothing here spends a simulation or touches the Quota Governor (CLAUDE.md rule 3
binds *simulations*; a recordset fetch is a GET against BRAIN's existing resources, the same kind
of call the vault's Portfolio feature already makes to read PnL). Nothing here submits anything
(CLAUDE.md rule 1).

Figures are built over the alpha's full recorded history; there is no train/test split threaded
through yet, so nothing here should be read as a ranking signal (CLAUDE.md rule 6 is about
*selection*, which this endpoint does not do).
"""

import math
from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, Query
from sqlalchemy import select

from ..db.sqlite import Database
from ..forge import alpha_types
from ..forge import analytics as an
from ..forge.board.models import PipelineAlpha
from ..forge.config import learning
from ..forge.pipelines.brain_adapter import BrainSimulator
from ..forge.research import factors as fc
from ..forge.research import pool as pl
from ..forge.research import tearsheet as ts
from ..forge.studio.models import TemplateStats
from ..schemas import Out
from .deps import State, refuse

router = APIRouter(prefix="/api/forge/analytics", tags=["forge-analytics"])

#: Recordset fetches never cost quota, so the lane only labels which engine-side queue the
#: (unused, for these calls) ``BrainSimulator`` constructor would enqueue onto.
_LANE = "explore"


def _num(v: float | None) -> float | None:
    return None if v is None or math.isnan(v) else v


class DistributionOut(Out):
    mean: float | None
    std: float | None
    skew: float | None
    kurtosis: float | None
    #: ``[theoretical, sample]`` pairs for a QQ plot.
    qq: list[list[float]]
    worst_days: list[tuple[str, float]]


class DeflatedSharpeOut(Out):
    value: float | None
    n_trials: int
    #: False when ``n_trials`` is a 1-trial fallback, not a real count — see ``_template_trials``.
    deflated: bool


class CorrelatedAlphaOut(Out):
    alpha_id: str
    correlation: float


class IcOut(Out):
    #: Trading-day lags, matching ``values`` index for index.
    horizons: list[int]
    #: Return-based IC proxy at each horizon (see ``tearsheet.return_based_ic``); ``None`` where
    #: there weren't enough observations to trust it.
    values: list[float | None]


class TearSheetOut(Out):
    alpha_id: str
    days: list[str]
    pnl: list[float]
    cumulative: list[float]
    underwater: list[float]
    rolling_sharpe: list[float | None]
    rolling_vol: list[float | None]
    ma: list[float | None]
    ma_upper: list[float | None]
    ma_lower: list[float | None]
    turnover_days: list[str]
    turnover: list[float]
    #: ``"YYYY-MM"`` -> summed PnL that month, for a returns heatmap.
    monthly_returns: dict[str, float]
    #: BRAIN's own ``yearly-stats`` recordset rows, passed through for the yearly-stats bars.
    yearly: list[dict[str, Any]]
    distribution: DistributionOut
    sharpe: float | None
    #: 90% confidence interval for the Sharpe, from a block bootstrap.
    bootstrap_sharpe_ci: tuple[float | None, float | None]
    #: Sign-flip permutation p-value: low means the direction of these returns is unlikely noise.
    permutation_p_value: float | None
    deflated_sharpe: DeflatedSharpeOut
    prob_sharpe_above_1_25: float | None
    #: Top 3 alphas already submitted whose PnL correlates most with this one's, highest first.
    pool_correlation: list[CorrelatedAlphaOut]
    #: One sentence on known-factor exposure (see ``factors.summarize``); ``None`` when the alpha
    #: isn't on the board yet, so its settings aren't known and factors can't be regressed.
    factor_summary: str | None
    ic: IcOut
    #: One sentence combining the Sharpe trend, pool crowding and factor exposure.
    verdict: str


class FactorsOut(Out):
    alpha_id: str
    #: Factor id -> beta. Only factors with enough overlap to be included appear here.
    betas: dict[str, float]
    alpha: float | None
    r_squared: float | None
    #: Sharpe of the PnL left over once the factor exposures are subtracted out.
    residual_sharpe: float | None
    n_obs: int
    factors_used: list[str]


class ClusterOut(Out):
    id: int
    left: str
    right: str
    distance: float
    members: list[str]


class PcaOut(Out):
    explained_variance_ratio: list[float]
    #: One row per alpha: its loading on each principal component.
    loadings: dict[str, list[float]]


class PoolOut(Out):
    alpha_ids: list[str]
    #: alpha_id -> {other alpha_id -> correlation}.
    correlation_matrix: dict[str, dict[str, float | None]]
    clusters: list[ClusterOut]
    pca: PcaOut
    families: list[dict[str, Any]]
    mean_abs_correlation: float | None
    max_correlation: float | None


async def _alpha_row(db: Database, alpha_id: str) -> PipelineAlpha | None:
    async with db.session() as session:
        return (
            await session.scalars(select(PipelineAlpha).where(PipelineAlpha.alpha_id == alpha_id))
        ).first()


@router.get("/factors/{alpha_id}")
async def factor_regression(alpha_id: str, state: State) -> FactorsOut:
    """Betas, R-squared and residual Sharpe of ``alpha_id`` against ``factors.yaml``.

    Needs the alpha's own settings (region/delay/universe/...) to run the factor expressions in
    a comparable scope, so only alphas the board already knows about (``forge_pipeline_alpha``)
    can be regressed.
    """
    row = await _alpha_row(state.db, alpha_id)
    if row is None:
        raise refuse(
            404, "unknown_alpha", f"Alpha {alpha_id!r} is not on the Submittable Board yet."
        )
    simulator = BrainSimulator(state.engine, state.db, state.endpoints, lane=_LANE)
    pnl_rows = await simulator.pnl(alpha_id)
    if not pnl_rows:
        raise refuse(404, "no_pnl", f"BRAIN has no pnl recordset for alpha {alpha_id!r} yet.")
    target = an.to_series(pnl_rows)
    series = await fc.factor_series(simulator, alpha_types.load(row.alpha_type), row.settings)
    reg = fc.regress(target, series)
    return FactorsOut(
        alpha_id=alpha_id,
        betas=reg.betas,
        alpha=_num(reg.alpha),
        r_squared=_num(reg.r_squared),
        residual_sharpe=_num(reg.residual_sharpe),
        n_obs=reg.n_obs,
        factors_used=reg.factors_used,
    )


@router.get("/pool")
async def pool_compare(
    state: State,
    alpha_ids: Annotated[str, Query(description="Comma-separated BRAIN alpha ids.")],
) -> PoolOut:
    """Correlation matrix, dendrogram, PCA and families across a handful of alphas."""
    ids = [a.strip() for a in alpha_ids.split(",") if a.strip()]
    simulator = BrainSimulator(state.engine, state.db, state.endpoints, lane=_LANE)
    series: dict[str, pd.Series] = {}
    for aid in ids:
        rows = await simulator.pnl(aid)
        if not rows:
            raise refuse(404, "no_pnl", f"BRAIN has no pnl recordset for alpha {aid!r} yet.")
        series[aid] = an.to_series(rows)
    scores = {aid: an.sharpe(s) for aid, s in series.items()}
    corr = pl.correlation_matrix(series)
    corr_values = corr.to_numpy(dtype=float)
    clusters = pl.hierarchical_clusters(corr)
    comparison = pl.compare(series, scores)
    pca_out = pl.pca(series)
    return PoolOut(
        alpha_ids=ids,
        correlation_matrix={
            a: {b: _num(float(corr_values[i, j])) for j, b in enumerate(ids)}
            for i, a in enumerate(ids)
        },
        clusters=[
            ClusterOut(
                id=c.id,
                left=str(c.left),
                right=str(c.right),
                distance=c.distance,
                members=c.members,
            )
            for c in clusters
        ],
        pca=PcaOut(
            explained_variance_ratio=pca_out["explained_variance_ratio"],
            loadings=pca_out["loadings"],
        ),
        families=comparison["families"],
        mean_abs_correlation=_num(comparison["mean_abs_correlation"]),
        max_correlation=_num(comparison["max_correlation"]),
    )


async def _template_trials(db: Database, alpha_id: str) -> tuple[int, bool]:
    """How many simulations the alpha's own template has behind it, from
    ``forge_template_stats`` (Deflated Sharpe needs the real trial count, not a guess). Falls
    back to 1 trial (no deflation) when the alpha's template is unknown or that table has no
    rows for it yet — it is filled by the pipelines (Phase 6) and may simply be empty so far.
    """
    async with db.session() as session:
        alpha_row = (
            await session.scalars(select(PipelineAlpha).where(PipelineAlpha.alpha_id == alpha_id))
        ).first()
        if alpha_row is None or not alpha_row.template_id:
            return 1, False
        sims = (
            await session.scalars(
                select(TemplateStats.sims).where(TemplateStats.template_id == alpha_row.template_id)
            )
        ).all()
    total = sum(sims)
    return (total, True) if total > 0 else (1, False)


@router.get("/tear-sheet/{alpha_id}")
async def tear_sheet(alpha_id: str, state: State) -> TearSheetOut:
    """Everything a desk looks at for one alpha, from its real BRAIN recordsets."""
    simulator = BrainSimulator(state.engine, state.db, state.endpoints, lane=_LANE)
    pnl_rows = await simulator.pnl(alpha_id)
    if not pnl_rows:
        raise refuse(404, "no_pnl", f"BRAIN has no pnl recordset for alpha {alpha_id!r} yet.")
    turnover_rows = await simulator.turnover(alpha_id)
    yearly_rows = await simulator.yearly_stats(alpha_id)

    daily = an.to_series(pnl_rows)
    cum = ts.cumulative(daily)
    under = ts.underwater(daily)
    roll_sharpe = ts.rolling_sharpe(daily)
    roll_vol = ts.rolling_vol(daily)
    bands = ts.moving_average_bands(daily)
    monthly = ts.monthly_returns(daily)
    dist = ts.return_distribution(daily)

    montecarlo = learning().get("montecarlo", {})
    block_days = int(montecarlo.get("bootstrap_block_days", 21))
    boot_samples = int(montecarlo.get("bootstrap_samples", 2000))
    perm_samples = int(montecarlo.get("permutation_samples", 500))
    ci_low, ci_high = ts.bootstrap_sharpe_ci(daily, block_days=block_days, samples=boot_samples)
    p_value = ts.permutation_test(daily, samples=perm_samples)

    n_trials, deflated = await _template_trials(state.db, alpha_id)
    dsr = an.deflated_sharpe(daily, n_trials)

    turnover_series = an.to_series(turnover_rows) if turnover_rows else pd.Series(dtype=float)

    # Correlation to the user's real pool: alphas already submitted, per the Portfolio feature's
    # own definition (vault.store.AlphaVault.submitted_members) — not a local, unpopulated notion.
    pool_rows = await state.alphas.submitted_members()
    pool_ids = [str(r["alpha_id"]) for r in pool_rows if str(r["alpha_id"]) != alpha_id]
    pool_pnl = await state.alphas.daily_pnl(pool_ids) if pool_ids else {}
    pool_series = {aid: pd.Series(rows).sort_index() for aid, rows in pool_pnl.items() if rows}
    top_pool = pl.top_correlated(daily, pool_series, n=3)

    # Factor exposure needs the alpha's own region/delay/universe to run comparable factor
    # expressions, so it's only available once the alpha is on the board.
    row = await _alpha_row(state.db, alpha_id)
    factor_summary: str | None = None
    dominant_factor: tuple[str, float] | None = None
    residual_sharpe: float | None = None
    if row is not None:
        factor_series_map = await fc.factor_series(
            simulator, alpha_types.load(row.alpha_type), row.settings
        )
        reg = fc.regress(daily, factor_series_map)
        residual_sharpe = reg.residual_sharpe
        if reg.factors_used:
            factor_summary = fc.summarize(reg)
            dominant_factor = max(
                ((f, reg.contributions.get(f, math.nan)) for f in reg.factors_used),
                key=lambda p: -1.0 if math.isnan(p[1]) else abs(p[1]),
            )

    ic_values = ts.return_based_ic(daily)
    verdict = ts.plain_verdict(
        trend=ts.sharpe_trend(roll_sharpe),
        top_correlation=top_pool[0] if top_pool else None,
        dominant_factor=dominant_factor,
        residual_sharpe=residual_sharpe,
    )

    return TearSheetOut(
        alpha_id=alpha_id,
        days=[d.date().isoformat() for d in pd.DatetimeIndex(daily.index)],
        pnl=[float(v) for v in daily.to_numpy()],
        cumulative=[float(v) for v in cum.to_numpy()],
        underwater=[float(v) for v in under.to_numpy()],
        rolling_sharpe=[_num(v) for v in roll_sharpe.to_numpy()],
        rolling_vol=[_num(v) for v in roll_vol.to_numpy()],
        ma=[_num(v) for v in bands["ma"].to_numpy()],
        ma_upper=[_num(v) for v in bands["upper"].to_numpy()],
        ma_lower=[_num(v) for v in bands["lower"].to_numpy()],
        turnover_days=[d.date().isoformat() for d in pd.DatetimeIndex(turnover_series.index)],
        turnover=[float(v) for v in turnover_series.to_numpy()],
        monthly_returns={f"{y:04d}-{m:02d}": v for (y, m), v in monthly.items()},
        yearly=yearly_rows,
        distribution=DistributionOut(
            mean=_num(dist["mean"]),  # type: ignore[arg-type]
            std=_num(dist["std"]),  # type: ignore[arg-type]
            skew=_num(dist["skew"]),  # type: ignore[arg-type]
            kurtosis=_num(dist["kurtosis"]),  # type: ignore[arg-type]
            qq=[[float(t), float(s)] for t, s in dist["qq"]],  # type: ignore[union-attr]
            worst_days=[(d.isoformat(), v) for d, v in dist["worst_days"]],  # type: ignore[union-attr]
        ),
        sharpe=_num(an.sharpe(daily)),
        bootstrap_sharpe_ci=(_num(ci_low), _num(ci_high)),
        permutation_p_value=_num(p_value),
        deflated_sharpe=DeflatedSharpeOut(value=_num(dsr), n_trials=n_trials, deflated=deflated),
        prob_sharpe_above_1_25=_num(ts.prob_sharpe_above(daily)),
        pool_correlation=[CorrelatedAlphaOut(alpha_id=aid, correlation=c) for aid, c in top_pool],
        factor_summary=factor_summary,
        ic=IcOut(horizons=list(ic_values), values=[_num(v) for v in ic_values.values()]),
        verdict=verdict,
    )
