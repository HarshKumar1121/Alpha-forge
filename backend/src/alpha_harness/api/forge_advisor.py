"""``/api/forge/advisor``: the Run Advisor — a before/after synthesis layer over field probes,
Nugget pairing, ``forge_template_stats``, the Board's checks/repairs and tear-sheet analytics.

No new simulation logic (CLAUDE.md rules 3-4): ``/preflight`` only reads the catalog and
``forge_template_stats``; ``/postrun`` reads what the Board and the tear sheet already fetched
and computed, reusing ``forge_analytics.tear_sheet`` rather than re-running its math. Nothing
here submits anything (CLAUDE.md rule 1).
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..brain.errors import BrainError
from ..forge.advisor import advisor as adv
from ..forge.board import checks as ck
from ..forge.board import store
from ..forge.board.models import PipelineAlpha
from ..forge.data.scope import Scope as ScopeModel
from ..forge.nuggets.suggest import suggest_pairings
from ..forge.research.field_profile import profile_field
from ..forge.studio.models import TemplateStats
from ..schemas import Out
from .deps import State, refuse
from .forge_analytics import tear_sheet as compute_tear_sheet

router = APIRouter(prefix="/api/forge/advisor", tags=["forge-advisor"])


class PreflightRequest(Out):
    template_id: str
    field_ids: list[str]
    region: str
    universe: str
    delay: int
    instrument_type: str = "EQUITY"


class PreflightOut(Out):
    paragraph: str
    warnings: list[str]


@router.post("/preflight")
async def preflight(body: PreflightRequest, state: State) -> PreflightOut:
    """One paragraph before a run: field behaviour, any pairing concern, and how this template
    has scored on data like this before. Free: a catalog read and a local table lookup."""
    scope = ScopeModel(
        instrument_type=body.instrument_type,
        region=body.region,
        delay=body.delay,
        universe=body.universe,
    ).to_tuple4()
    rows = await state.queries.fields_by_id(scope, body.field_ids)
    profiles = [profile_field(r) for r in rows]
    pairings = suggest_pairings(profiles) if len(profiles) > 1 else []
    async with state.db.session() as session:
        stats_rows = (
            await session.scalars(
                select(TemplateStats).where(
                    TemplateStats.template_id == body.template_id,
                    TemplateStats.scope == scope.label,
                )
            )
        ).all()
    similar = [
        adv.SimilarAttempt(
            dataset_category=r.dataset_category,
            alpha_type=r.alpha_type,
            sims=r.sims,
            checked=r.checked,
            passed=r.passed,
            median_train_sharpe=r.median_train_sharpe,
        )
        for r in stats_rows
    ]
    summary = adv.preflight(profiles, pairings, similar)
    return PreflightOut(paragraph=summary.paragraph, warnings=summary.warnings)


class PostRunOut(Out):
    alpha_id: str
    verdict: str
    action: str
    action_kind: adv.ActionKind


@router.get("/postrun/{alpha_id}")
async def postrun(alpha_id: str, state: State) -> PostRunOut:
    """One verdict and one concrete next step after a run lands: train/test Sharpe, factor
    exposure and the Board's check status tied together, reusing the Board's repair lookup and
    the tear sheet's own numbers rather than computing anything new."""
    async with state.db.session() as session:
        row = (
            await session.scalars(select(PipelineAlpha).where(PipelineAlpha.alpha_id == alpha_id))
        ).first()
    if row is None:
        raise refuse(
            404, "not_on_board", f"{alpha_id} is not a pipeline alpha on the Submittable Board yet."
        )
    entry = (await store.entries(state.db, [row], store.timezone()))[0]
    reading = ck.read(entry.checks)

    tear_verdict: str | None = None
    pool_correlation: list[tuple[str, float]] = []
    permutation_p_value: float | None = None
    has_pnl = True
    try:
        ts_out = await compute_tear_sheet(alpha_id, state)
    except HTTPException, BrainError:
        has_pnl = False
    else:
        tear_verdict = ts_out.verdict
        pool_correlation = [(c.alpha_id, c.correlation) for c in ts_out.pool_correlation]
        permutation_p_value = ts_out.permutation_p_value

    summary = adv.postrun(
        reading=reading,
        train_sharpe=row.train_sharpe,
        test_sharpe=row.test_sharpe,
        tear_verdict=tear_verdict,
        pool_correlation=pool_correlation,
        permutation_p_value=permutation_p_value,
        has_pnl=has_pnl,
    )
    return PostRunOut(
        alpha_id=alpha_id,
        verdict=summary.verdict,
        action=summary.action,
        action_kind=summary.action_kind,
    )
