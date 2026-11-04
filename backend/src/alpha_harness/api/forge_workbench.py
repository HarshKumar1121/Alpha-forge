"""``/api/forge/workbench``: the field tray behind the Workbench screen, and running the
candidates it builds (from a template preview or hand-written) for real.

Run goes through the same seams as every other forge screen that spends quota: the engine's
``BatchEngine``/``QuotaGovernor`` (CLAUDE.md rule 3), tagged ``forge.<lane>`` so the run shows up
in Tasks and the Simulation Matrix exactly like a Pipelines run does, and never calls BRAIN's
submit endpoint (CLAUDE.md rule 1). Each candidate's own template/field/region is kept in
``forge_workbench_run_item`` — durable past ``TaskRegistry``'s 60s linger — so the Workbench's own
results table can read it back grouped, the way the Pipelines run-history panel does.
"""

from typing import Any

from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select

from ..catalog.queries import Tuple4
from ..forge.pipelines.brain_adapter import BrainSimulator
from ..forge.simulator import SimOutcome, request_for
from ..forge.workbench.models import WorkbenchRunItem, WorkbenchTray
from ..schemas import Out
from ..tasks import spawn
from .catalog import Scope
from .deps import State, refuse

router = APIRouter(prefix="/api/forge/workbench", tags=["forge-workbench"])

#: Hand-approved, scratch work — the same lane manual runs from a lab screen use
#: (see ``config/forge/lanes.yaml``'s ``default_lane``). No new lane needed.
LANE = "reserve"
MAX_CANDIDATES = 200

#: Generous: a Workbench candidate run can span many fields, but a page still has to render it.
MAX_TRAY_FIELDS = 500


class TrayIn(Out):
    field_ids: list[str] = Field(default_factory=list, max_length=MAX_TRAY_FIELDS)


class TrayOut(Out):
    field_ids: list[str]


def _query(scope: Tuple4):  # noqa: ANN202 - SQLAlchemy Select, inferred fine at call sites
    return select(WorkbenchTray).where(
        WorkbenchTray.instrument_type == scope.instrument_type,
        WorkbenchTray.region == scope.region,
        WorkbenchTray.delay == scope.delay,
        WorkbenchTray.universe == scope.universe,
    )


@router.get("/tray")
async def get_tray(scope: Scope, state: State) -> TrayOut:
    """The fields in this scope's tray, or an empty one when nothing has been saved yet."""
    async with state.db.session() as s:
        row = (await s.scalars(_query(scope))).one_or_none()
    return TrayOut(field_ids=[str(f) for f in row.field_ids] if row else [])


@router.put("/tray")
async def set_tray(scope: Scope, body: TrayIn, state: State) -> TrayOut:
    """Replace this scope's tray with the given fields, in the order given."""
    field_ids = list(dict.fromkeys(f.strip() for f in body.field_ids if f.strip()))
    async with state.db.session() as s:
        row = (await s.scalars(_query(scope))).one_or_none()
        if row is None:
            row = WorkbenchTray(
                instrument_type=scope.instrument_type,
                region=scope.region,
                delay=scope.delay,
                universe=scope.universe,
                field_ids=field_ids,
            )
            s.add(row)
        else:
            row.field_ids = field_ids
        await s.flush()
    return TrayOut(field_ids=field_ids)


# --- run ------------------------------------------------------------------------


class RunCandidateIn(Out):
    expression: str = Field(min_length=1, max_length=4000)
    #: The template this came from, or omitted for a hand-written expression.
    template_id: str | None = None
    field_ids: list[str] = Field(default_factory=list, max_length=MAX_TRAY_FIELDS)


class RunRequest(Out):
    candidates: list[RunCandidateIn] = Field(min_length=1, max_length=MAX_CANDIDATES)


class RunStarted(Out):
    task_id: str
    lane: str
    #: How many of the asked-for candidates the Quota Governor admitted; the rest were dropped,
    #: same as a Pipelines run trimming its plan to fit.
    admitted: int
    asked: int


class RunItemOut(Out):
    expression: str
    template_id: str | None
    field_ids: list[str]
    region: str
    delay: int
    universe: str
    status: str
    alpha_id: str | None
    train_sharpe: float | None
    test_sharpe: float | None
    error: str | None


class RunResult(Out):
    task_id: str
    #: running while any item is still queued, done once every item has an answer.
    status: str
    items: list[RunItemOut]


def _item_out(row: WorkbenchRunItem) -> RunItemOut:
    return RunItemOut(
        expression=row.expression,
        template_id=row.template_id,
        field_ids=[str(f) for f in row.field_ids],
        region=row.region,
        delay=row.delay,
        universe=row.universe,
        status=row.status,
        alpha_id=row.alpha_id,
        train_sharpe=row.train_sharpe,
        test_sharpe=row.test_sharpe,
        error=row.error,
    )


def _num(alpha: dict[str, Any] | None, block: str, key: str) -> float | None:
    v = ((alpha or {}).get(block) or {}).get(key)
    return float(v) if v is not None else None


async def _run(state: State, task_id: str, scope: Tuple4, candidates: list[RunCandidateIn]) -> None:
    """Simulate every candidate and write its outcome back, one row at a time, so the results
    table fills in as answers arrive rather than waiting for the slowest one."""
    settings = {
        "instrumentType": scope.instrument_type,
        "region": scope.region,
        "delay": scope.delay,
        "universe": scope.universe,
    }
    simulator = BrainSimulator(state.engine, state.db, state.endpoints, LANE)
    requests = [request_for(c.expression, settings) for c in candidates]

    async def _mark(index: int, outcome: SimOutcome | None, error: str | None) -> None:
        async with state.db.session() as s:
            row = (
                await s.scalars(
                    select(WorkbenchRunItem)
                    .where(WorkbenchRunItem.task_id == task_id)
                    .order_by(WorkbenchRunItem.id)
                    .offset(index)
                    .limit(1)
                )
            ).one_or_none()
            if row is None:
                return
            if error is not None:
                row.status = "failed"
                row.error = error
            elif outcome is not None:
                row.status = "failed" if not outcome.ok else "done"
                row.alpha_id = outcome.alpha_id
                row.train_sharpe = _num(outcome.alpha, "train", "sharpe") or _num(
                    outcome.alpha, "is", "sharpe"
                )
                row.test_sharpe = _num(outcome.alpha, "test", "sharpe")
                row.error = outcome.error

    try:
        outcomes = await simulator.simulate(requests)
    except Exception as exc:  # noqa: BLE001 - surfaced per item, not raised out of the task
        for i in range(len(candidates)):
            await _mark(i, None, str(exc))
        return
    for i, outcome in enumerate(outcomes):
        await _mark(i, outcome, None)


@router.post("/run")
async def run(scope: Scope, body: RunRequest, state: State) -> RunStarted:
    """Run every candidate for real, through the Quota Governor, tagged so it shows up in Tasks
    and the Simulation Matrix like any other run. Never submits anything."""
    governor = state.engine.governor
    if governor is None:
        raise refuse(503, "quota_governor_off", "The Quota Governor is not running.")

    admission = await governor.admit(LANE, [1] * len(body.candidates))
    candidates = body.candidates[: admission.admitted]
    if not candidates:
        raise refuse(
            429,
            "no_quota",
            f"The {LANE} lane has no room today: {admission.reason}",
        )

    task = await state.tasks.start(
        "workbench",
        f"{len(candidates)} candidates on {scope.label}",
        scope=scope.label,
    )
    async with state.db.session() as s:
        for c in candidates:
            s.add(
                WorkbenchRunItem(
                    task_id=task.id,
                    expression=c.expression,
                    template_id=c.template_id,
                    field_ids=c.field_ids,
                    instrument_type=scope.instrument_type,
                    region=scope.region,
                    delay=scope.delay,
                    universe=scope.universe,
                )
            )

    async def execute() -> None:
        try:
            await _run(state, task.id, scope, candidates)
            await state.tasks.finish(task, state="done")
        except Exception as exc:  # noqa: BLE001 - surfaced through the task record, not raised
            await state.tasks.finish(task, state="failed", error=str(exc))

    spawn(execute(), name="workbench-run")
    return RunStarted(
        task_id=task.id, lane=LANE, admitted=admission.admitted, asked=admission.asked
    )


@router.get("/run/{task_id}")
async def get_run(task_id: str, state: State) -> RunResult:
    """This run's candidates and whatever has come back for each so far."""
    async with state.db.session() as s:
        rows = (
            await s.scalars(
                select(WorkbenchRunItem)
                .where(WorkbenchRunItem.task_id == task_id)
                .order_by(WorkbenchRunItem.id)
            )
        ).all()
    if not rows:
        raise refuse(404, "not_found", f"There is no Workbench run {task_id!r}.")
    status = "running" if any(r.status == "queued" for r in rows) else "done"
    return RunResult(task_id=task_id, status=status, items=[_item_out(r) for r in rows])
