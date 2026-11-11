"""``/api/forge/board``: families of recent pipeline alphas, what is submittable, and why not.

- ``GET ""`` reads only what is already kept: free, and never reaches BRAIN.
- ``POST /fetch`` fills the gaps in the background: daily PnL for every alpha (to cluster
  families), then Check Submission, correlation and before/after for each family leader only.
  No simulation is spent; BRAIN's client honours Retry-After (CLAUDE.md rule 9).
- ``POST /repair`` queues one ``court.yaml`` repair of a refused alpha into the ``validate``
  lane, through the Quota Governor (CLAUDE.md rule 3).

There is no submit route (CLAUDE.md rule 1): the board ends at a checklist you follow by hand.
"""

from typing import Any

import structlog
from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import select

from ..brain.errors import BrainError
from ..forge import config
from ..forge.alpha_types import load_all as load_alpha_types
from ..forge.board import board as bd
from ..forge.board import checks as ck
from ..forge.board import store
from ..forge.board.models import PipelineAlpha
from ..forge.pipelines.brain_adapter import BrainSimulator
from ..forge.research.planner import Cand, apply_repair
from ..forge.simulator import request_for
from ..schemas import Out
from ..tasks import spawn
from .deps import State, refuse

router = APIRouter(prefix="/api/forge/board", tags=["forge-board"])
log = structlog.get_logger(__name__)

#: Repairs are second opinions on strong candidates: the validate lane pays (lanes.yaml).
REPAIR_LANE = "validate"
FETCH_KIND = "board-fetch"
Days = Query(default=2, ge=1, le=14, description="How many days back, today included.")


class FetchRequest(Out):
    days: int = Field(default=2, ge=1, le=14)


class TaskStarted(Out):
    task_id: str


class RepairRequest(Out):
    alpha_id: str = Field(min_length=1, max_length=32)
    #: A ``court.yaml`` repair key; the first failing check's repair when omitted.
    repair: str | None = None


class RepairQueued(Out):
    task_id: str
    alpha_id: str
    repair: str
    why: str
    expression: str
    settings: dict[str, Any]
    lane: str
    lane_remaining: int


async def _board(state: State, days: int) -> tuple[list[bd.Entry], bd.Board]:
    tz = store.timezone()
    rows = await store.rows_since(state.db, store.since(days, tz))
    entries = await store.entries(state.db, rows, tz)
    return entries, bd.build(entries)


@router.get("")
async def get_board(state: State, days: int = Days) -> bd.Board:
    """The board from what is already kept. Free: nothing here reaches BRAIN."""
    return (await _board(state, days))[1]


async def _fetch(state: State, days: int, report: Any) -> None:
    """Fill the board's gaps, PnL first (families depend on it), then leaders' BRAIN answers."""
    entries, _ = await _board(state, days)
    no_pnl = [e.alpha_id for e in entries if e.pnl is None]
    for n, alpha_id in enumerate(no_pnl, 1):
        await report(f"PnL {n}/{len(no_pnl)}: {alpha_id}")
        try:
            await store.keep(
                state.db, f"pnl:{alpha_id}", await state.endpoints.recordset_body(alpha_id, "pnl")
            )
        except BrainError as exc:
            log.warning("board.pnl_failed", alpha_id=alpha_id, error=str(exc))

    entries, board = await _board(state, days)
    by_id = {e.alpha_id: e for e in entries}
    leaders = [f.leader for d in board.days for t in d.types for f in t.families]
    for n, lead in enumerate(leaders, 1):
        e = by_id[lead.alpha_id]
        await report(f"Leader {n}/{len(leaders)}: {e.alpha_id}")
        kind = bd.correlation_kind(lead.power_pool)
        wanted = [
            (e.checks_source != "check", f"check:{e.alpha_id}", state.endpoints.check_alpha),
            (
                e.correlation is None,
                f"correlation:{kind}:{e.alpha_id}",
                lambda a, kind=kind: state.endpoints.correlations(a, kind),
            ),
            (e.performance is None, f"performance:{e.alpha_id}", state.endpoints.before_and_after),
        ]
        for needed, key, call in wanted:
            if not needed:
                continue
            try:
                await store.keep(state.db, key, await call(e.alpha_id))
            except BrainError as exc:
                log.warning("board.fetch_failed", key=key, error=str(exc))


@router.post("/fetch")
async def fetch(body: FetchRequest, state: State) -> TaskStarted:
    """Start filling the board from BRAIN in the background. Spends no simulations."""
    if any(t["kind"] == FETCH_KIND and t["state"] == "running" for t in state.tasks.list_tasks()):
        raise refuse(409, "already_fetching", "The board is already fetching from BRAIN.")
    task = await state.tasks.start(FETCH_KIND, "Submittable Board: fetching from BRAIN")

    async def report(detail: str) -> None:
        await state.tasks.update(task, detail=detail)

    async def execute() -> None:
        try:
            await _fetch(state, body.days, report)
            await state.tasks.finish(task, state="done")
        except Exception as exc:  # noqa: BLE001 - surfaced through the task record
            await state.tasks.finish(task, state="failed", error=str(exc))

    spawn(execute(), name="board-fetch")
    return TaskStarted(task_id=task.id)


async def _source(state: State, alpha_id: str) -> PipelineAlpha:
    async with state.db.session() as session:
        found = (
            await session.scalars(select(PipelineAlpha).where(PipelineAlpha.alpha_id == alpha_id))
        ).first()
    if found is None:
        raise refuse(404, "not_on_board", f"{alpha_id} is not a pipeline alpha on the board.")
    return found


def _repair_key(row: PipelineAlpha, checks: list[dict[str, Any]], asked: str | None) -> str:
    repairs: dict[str, Any] = config.court().get("repairs") or {}
    if asked is not None:
        if asked not in repairs:
            raise refuse(422, "unknown_repair", f"court.yaml has no repair {asked!r}.")
        return asked
    for name in ck.read(checks).failing:
        if (key := ck.repair_for(name).key) is not None:
            return key
    raise refuse(422, "no_repair", f"{row.alpha_id} fails nothing a court.yaml repair can answer.")


@router.post("/repair")
async def queue_repair(body: RepairRequest, state: State) -> RepairQueued:
    """Simulate one repair of a refused alpha in the validate lane. Never submits anything."""
    row = await _source(state, body.alpha_id)
    entry = (await store.entries(state.db, [row], store.timezone()))[0]
    key = _repair_key(row, entry.checks, body.repair)
    rule: dict[str, Any] = (config.court().get("repairs") or {})[key]
    expression, settings = apply_repair(row.expression, row.settings, row.field, rule)

    types = load_alpha_types()
    if row.alpha_type in types:
        sim_type = types[row.alpha_type].sim_type
    elif row.sim_type:
        # Staged from outside a pipeline (Results Breakdown): no Forge alpha type names it,
        # so the simulation type it already ran under, recorded when it was staged, stands in.
        sim_type = row.sim_type
    else:
        raise refuse(422, "unknown_alpha_type", f"No alpha type {row.alpha_type!r}.")
    request = request_for(expression, settings, sim_type)

    governor = state.engine.governor
    if governor is None:
        raise refuse(503, "QUOTA_GOVERNOR_OFF", "The Quota Governor is not running.")
    admission = await governor.admit(REPAIR_LANE, [1])
    if not admission.admitted:
        raise refuse(429, "lane_full", admission.reason)

    task = await state.tasks.start(
        "board-repair", f"Repair {row.alpha_id}: {key}", alphaId=row.alpha_id, repair=key
    )
    async with state.db.session() as session:
        stored = await session.get(PipelineAlpha, row.id)
        if stored is not None:
            stored.repairs_queued += 1

    async def execute() -> None:
        try:
            simulator = BrainSimulator(state.engine, state.db, state.endpoints, REPAIR_LANE)
            outcome = (await simulator.simulate([request]))[0]
            if outcome.alpha is None:
                await state.tasks.finish(task, state="failed", error=outcome.error)
                return
            cand = Cand(
                expression,
                settings,
                row.field,
                f"{row.template_id}+{key.lower()}",
                "",
                stage="repair",
                alpha=outcome.alpha,
            )
            await store.record_alphas(
                state.db, [store.row_for(cand, row.pipeline, row.alpha_type, task.id)]
            )
            await state.tasks.finish(task, state="done")
        except Exception as exc:  # noqa: BLE001 - surfaced through the task record
            await state.tasks.finish(task, state="failed", error=str(exc))

    spawn(execute(), name="board-repair")
    return RepairQueued(
        task_id=task.id,
        alpha_id=row.alpha_id,
        repair=key,
        why=str(rule.get("why", "")),
        expression=expression,
        settings=settings,
        lane=REPAIR_LANE,
        lane_remaining=admission.lane_remaining,
    )
