"""``/api/forge/breakdown``: Results Breakdown for one finished Template Lab task.

Reads only what the task already produced -- its ``Trial`` rows and the vault's current
checks/PnL for each -- and never reaches BRAIN (CLAUDE.md rule 1, rule 4 n/a: nothing here is a
test, nothing here spends). The one action that can spend anything, queueing a ``court.yaml``
repair, stays the existing ``/api/forge/board/repair`` route and its Quota Governor admission
(CLAUDE.md rule 3): this router's ``POST .../stage`` only writes a ``PipelineAlpha`` row so that
route can find a Template Lab alpha at all (it only knows pipeline alphas), and spends nothing
doing it.
"""

from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Query
from sqlalchemy import select

from ..db.models import BrainCache, Study, Trial, TrialState
from ..forge import config
from ..forge.board import breakdown as bk
from ..forge.board.models import PipelineAlpha
from ..labs.fastexpr import ParseError, data_fields, parse
from ..schemas import Out
from ..vault.yields import checks_of
from .deps import State, refuse

router = APIRouter(prefix="/api/forge/breakdown", tags=["forge-breakdown"])
log = structlog.get_logger(__name__)

GroupBy = Literal["field", "dataset", "setting"]
SettingName = Literal["neutralization", "decay", "universe"]


class Staged(Out):
    alpha_id: str


def _primary_field(expression: str) -> str:
    try:
        fields = data_fields(parse(expression), grouping=False)
    except ParseError:
        return ""
    return fields[0] if fields else ""


async def _task(state: State, task_id: int) -> Study:
    async with state.db.session() as session:
        task = await session.get(Study, task_id)
    if task is None:
        raise refuse(404, "not_found", f"No task {task_id}.")
    return task


async def _finished_trials(state: State, task_id: int) -> list[Trial]:
    async with state.db.session() as session:
        return list(
            (
                await session.scalars(
                    select(Trial).where(
                        Trial.study_id == task_id, Trial.state == TrialState.COMPLETE
                    )
                )
            ).all()
        )


async def _rows(state: State, task: Study, trials: list[Trial]) -> list[bk.AlphaRow]:
    """Trials as :class:`bk.AlphaRow`. The vault's current checks win over a trial's frozen
    copy, same rule :func:`alpha_harness.labs.study.ranked` uses for the Tasks screen itself."""
    ids = [t.alpha_id for t in trials if t.alpha_id]
    stored = await state.alphas.by_ids(ids)
    rows: list[bk.AlphaRow] = []
    for t in trials:
        if not t.expression or not t.alpha_id:
            continue
        vault_row = stored.get(t.alpha_id) or {}
        vault_checks = checks_of(vault_row.get("checks")) if vault_row.get("checks") else []
        checks = vault_checks or ((t.result or {}).get("checks") or [])
        stats = (t.result or {}).get("stats") or {}
        rows.append(
            bk.AlphaRow(
                alpha_id=t.alpha_id,
                expression=t.expression,
                settings=t.settings or {},
                field=_primary_field(t.expression),
                template_id=str(task.template_id or ""),
                train_sharpe=stats.get("sharpe"),
                test_sharpe=vault_row.get("test_sharpe"),
                checks=checks,
            )
        )
    return rows


async def _datasets_of(state: State, rows: list[bk.AlphaRow]) -> dict[str, set[str]]:
    """Every field these rows name, mapped to the datasets the catalog files it under, across
    every market: a read-only display grouping, not a submission-deciding one, so one field
    shared by two markets is not worth scoping this query to a single instrumentType/region/delay
    for."""
    fields = sorted({r.field for r in rows if r.field})
    if not fields:
        return {}
    placeholders = ", ".join("?" for _ in fields)
    found = await state.catalog.query(
        f"SELECT DISTINCT field_id, dataset_id FROM data_field "  # noqa: S608
        f"WHERE field_id IN ({placeholders}) AND dataset_id IS NOT NULL",
        fields,
    )
    out: dict[str, set[str]] = {}
    for r in found:
        out.setdefault(str(r["field_id"]), set()).add(str(r["dataset_id"]))
    return out


async def _checked_alpha_ids(state: State, alpha_ids: list[str]) -> set[str]:
    """Alpha ids that have *ever* had BRAIN's own self- and production-correlation checks
    fetched (Submittable Board's fetch and Submit Helper's check both write the same
    ``correlation:<kind>:<alpha_id>`` cache keys this reads) -- not whether they are fresh,
    just whether section (c)'s PnL-only order can be cross-checked against BRAIN's own numbers
    at all. Reads the cache table only; this never reaches BRAIN itself (CLAUDE.md rule 1)."""
    if not alpha_ids:
        return set()
    keys = [f"correlation:{kind}:{aid}" for aid in alpha_ids for kind in ("self", "prod")]
    async with state.db.session() as session:
        found = (
            await session.scalars(select(BrainCache.key).where(BrainCache.key.in_(keys)))
        ).all()
    have_self = {
        k.removeprefix("correlation:self:") for k in found if k.startswith("correlation:self:")
    }
    have_prod = {
        k.removeprefix("correlation:prod:") for k in found if k.startswith("correlation:prod:")
    }
    return have_self & have_prod


async def _pnl_series(state: State, alpha_ids: list[str]) -> dict[str, Any]:
    import pandas as pd

    ids, dates, pnl = await state.alphas.pnl_grid(alpha_ids)
    if not ids:
        return {}
    index = pd.to_datetime(dates)
    return {aid: pd.Series(pnl[:, i], index=index).dropna() for i, aid in enumerate(ids)}


@router.get("/{task_id}")
async def get_breakdown(
    task_id: int,
    state: State,
    group_by: Annotated[GroupBy, Query()] = "field",
    setting_name: Annotated[SettingName | None, Query()] = None,
    min_n: Annotated[int, Query(ge=1, le=100)] = 3,
    pool_collision_ceiling: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
) -> bk.Breakdown:
    """Three sections over a finished task's own results. Free: nothing here reaches BRAIN."""
    task = await _task(state, task_id)
    trials = await _finished_trials(state, task_id)
    rows = await _rows(state, task, trials)

    court_cfg = config.court().get("court") or {}
    near_miss_margin = float(court_cfg.get("near_miss_margin", 0.75))
    families_cfg = config.learning().get("families") or {}
    overlap_ceiling = (
        pool_collision_ceiling
        if pool_collision_ceiling is not None
        else float(families_cfg.get("pool_collision", 0.7))
    )
    family_threshold = float(families_cfg.get("pnl_corr_threshold", 0.6))

    datasets_of = await _datasets_of(state, rows)
    passing = [r for r in rows if bk.status_of(r) == "ready"]
    pool_members = await state.alphas.submitted_members()
    pool_ids = [str(m["alpha_id"]) for m in pool_members]
    series = await _pnl_series(state, [*(r.alpha_id for r in passing), *pool_ids])
    for r in passing:
        r.pnl = series.get(r.alpha_id)
    pool_series = {aid: series[aid] for aid in pool_ids if aid in series}
    checked = await _checked_alpha_ids(state, [r.alpha_id for r in passing])

    return bk.build(
        rows,
        passing,
        pool_series,
        group_by=group_by,
        setting_name=setting_name,
        min_n=min_n,
        datasets_of=datasets_of,
        family_threshold=family_threshold,
        near_miss_margin=near_miss_margin,
        pool_collision_ceiling=overlap_ceiling,
        checked=checked,
    )


@router.post("/{task_id}/alphas/{alpha_id}/stage")
async def stage_for_repair(task_id: int, alpha_id: str, state: State) -> Staged:
    """Write (or refresh) the ``PipelineAlpha`` row ``/api/forge/board/repair`` needs to find
    this Template Lab alpha. Spends nothing: it is a local write, not a simulation."""
    task = await _task(state, task_id)
    async with state.db.session() as session:
        trial = (
            await session.scalars(
                select(Trial).where(Trial.study_id == task_id, Trial.alpha_id == alpha_id)
            )
        ).first()
    if trial is None or not trial.expression:
        raise refuse(404, "not_on_task", f"{alpha_id} is not a finished alpha of task {task_id}.")
    stored = (await state.alphas.by_ids([alpha_id])).get(alpha_id) or {}
    checks = (
        checks_of(stored.get("checks"))
        if stored.get("checks")
        else ((trial.result or {}).get("checks") or [])
    )
    sim_type = str(stored.get("sim_type") or "REGULAR")
    async with state.db.session() as session:
        row = (
            await session.scalars(select(PipelineAlpha).where(PipelineAlpha.alpha_id == alpha_id))
        ).first()
        if row is None:
            session.add(
                PipelineAlpha(
                    alpha_id=alpha_id,
                    pipeline="template-lab",
                    alpha_type="external",
                    sim_type=sim_type,
                    region=str(trial.settings.get("region", "")) if trial.settings else "",
                    expression=trial.expression,
                    settings=trial.settings or {},
                    field=_primary_field(trial.expression),
                    template_id=str(task.template_id or ""),
                    stage="template-lab",
                    sim_checks=checks,
                )
            )
        else:
            row.expression = trial.expression
            row.settings = trial.settings or {}
            row.sim_checks = checks
            if not row.sim_type:
                row.sim_type = sim_type
    return Staged(alpha_id=alpha_id)
