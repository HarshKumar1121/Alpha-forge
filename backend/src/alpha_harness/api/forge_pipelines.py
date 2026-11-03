"""``/api/forge/pipelines``: pick a pipeline config, a field set and template(s), preview what it
would cost, then run it for real against :class:`~..forge.pipelines.brain_adapter.BrainSimulator`.

Preview is free and offline (candidate generation and the eligibility lint are pure CPU, and the
quota estimate is a read of the governor's own ledger). Run is the only endpoint in this module
that spends anything, and it still goes through the Quota Governor exactly as the orchestrator's
own ``PipelineRun.run()`` does (CLAUDE.md rule 3) — this route adds no second admission check, it
reads the same one back for the preview. Nothing here ever calls BRAIN's submit endpoint
(CLAUDE.md rule 1): a pipeline run ends at candidates, checks and correlations, never a POST that
submits.

A run happens off the request/response cycle: ``POST .../run`` starts a background job on the
existing :class:`~..tasks.TaskRegistry` (the same one the top bar's busy indicator reads) and
returns its id immediately. Progress is the task's own ``detail``, broadcast on the ``tasks``
websocket topic the frontend already subscribes to — no new topic. The run's own identity and
report are also written to the ``forge_pipeline_run`` table (:mod:`..forge.pipelines.store`),
so ``GET /runs/{id}`` and the history panel survive past the task registry's 60s linger and a
server restart.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select

from ..forge.alpha_types import AlphaType
from ..forge.alpha_types import load_all as load_alpha_types
from ..forge.board.store import record_run
from ..forge.data.fieldsets import resolve
from ..forge.data.models import FieldSet
from ..forge.pipelines import store as pipelines_store
from ..forge.pipelines.brain_adapter import BrainSimulator
from ..forge.pipelines.config import PipelineConfig
from ..forge.pipelines.config import load_all as load_pipelines
from ..forge.pipelines.orchestrator import Cand, PipelineRun
from ..forge.quota.governor import Admission
from ..forge.research.binding import field_infos
from ..forge.research.templates import Template
from ..forge.research.templates import get as get_template
from ..forge.store import InMemoryResearchStore
from ..forge.studio.models import UserTemplate
from ..forge.studio.user_templates import TemplateTextError
from ..forge.studio.user_templates import parse as parse_template
from ..schemas import Out
from ..tasks import spawn
from .deps import State, refuse

router = APIRouter(prefix="/api/forge/pipelines", tags=["forge-pipelines"])


# --- wire shapes ----------------------------------------------------------------


class PipelineOut(Out):
    name: str
    alpha_type: str
    lane: str
    template_complexity: list[str]
    correlation_kind: str
    correlation_top_n: int
    check_top_n: int
    default_budget: int


class PlanRequest(Out):
    pipeline: str = Field(min_length=1, max_length=80)
    field_set_id: int
    template_ids: list[str] = Field(min_length=1, max_length=20)
    budget: int | None = Field(default=None, ge=1, le=10_000)


class RejectedCandidate(Out):
    expression: str
    reason: str


class QuotaAdmission(Out):
    lane: str
    asked: int
    admitted: int
    trimmed: int
    admitted_cost: int
    reason: str
    lane_remaining: int
    global_remaining: int


class PlanPreview(Out):
    pipeline: str
    field_set: str
    templates: list[str]
    budget: int
    #: Every candidate the bound templates x field set would render.
    generated: int
    #: What the alpha type's own rules would not refuse before it costs a simulation.
    eligible: int
    #: A sample of what lint would drop, and why — capped so the wire stays small.
    rejected_sample: list[RejectedCandidate]
    rejected_total: int
    admission: QuotaAdmission


class RunRequest(PlanRequest):
    pass


class RunStarted(Out):
    task_id: str
    pipeline: str
    field_set: str
    budget: int


class CandidateOut(Out):
    expression: str
    alpha_id: str | None
    train_sharpe: float | None
    test_sharpe: float | None


class RunReport(Out):
    status: str
    pipeline: str
    spent: int
    candidates: list[CandidateOut]
    finalists: list[CandidateOut]
    rejected: list[RejectedCandidate]
    checks: dict[str, dict[str, Any]]
    correlations: dict[str, dict[str, Any]]


class RunSummary(Out):
    """One row of the run-history panel — durable past ``TaskRegistry``'s 60s linger."""

    task_id: str
    pipeline: str
    field_set: str
    lane: str
    budget: int
    status: str
    candidates_count: int
    finalists_count: int
    rejected_count: int
    spent: int
    started_at: datetime
    finished_at: datetime | None


# --- helpers ----------------------------------------------------------------------


def _pipeline(name: str) -> PipelineConfig:
    pipelines = load_pipelines()
    if name not in pipelines:
        raise refuse(404, "unknown_pipeline", f"No pipeline called {name!r}.")
    return pipelines[name]


async def _field_set(state: State, field_set_id: int) -> FieldSet:
    async with state.db.session() as session:
        row = await session.get(FieldSet, field_set_id)
    if row is None:
        raise refuse(404, "not_found", f"There is no field set {field_set_id}.")
    return row


def _scoped_to(alpha_type: AlphaType, row: FieldSet) -> AlphaType:
    """``alpha_type``'s own settings, except the scope: that always comes from the field set
    the user actually picked, never the type YAML's default market.

    A type's own settings exist to describe *that type's* simulation rules (decay, neutraliz-
    ation, truncation); the scope is a property of the fields being crossed, not of the type.
    Keeping the YAML's region/universe/delay/instrumentType here would simulate one market's
    fields under another market's settings, which BRAIN either refuses or runs on the wrong
    data (CLAUDE.md: the generated candidates must be this field set's, scope included).
    """
    scoped_settings = {
        **alpha_type.settings,
        "instrumentType": row.instrument_type,
        "region": row.region,
        "universe": row.universe,
        "delay": row.delay,
    }
    raw = {
        **alpha_type.raw,
        "simulation": {**alpha_type.raw["simulation"], "settings": scoped_settings},
    }
    return AlphaType(name=alpha_type.name, raw=raw)


async def _template(state: State, template_id: str) -> Template:
    """A built-in, or one of the user's own — same resolution Template Studio uses."""
    found = get_template(template_id)
    if found is not None:
        return found
    async with state.db.session() as session:
        row = (
            await session.scalars(
                select(UserTemplate).where(UserTemplate.template_id == template_id)
            )
        ).first()
    if row is None:
        raise refuse(404, "unknown_template", f"No template {template_id!r}.")
    try:
        return parse_template(row.yaml_text).template
    except TemplateTextError as exc:
        raise refuse(
            422, "template_unreadable", f"Your template {template_id!r} cannot be read: {exc}"
        ) from exc


def _admission_out(admission: Admission) -> QuotaAdmission:
    return QuotaAdmission(
        lane=admission.lane,
        asked=admission.asked,
        admitted=admission.admitted,
        trimmed=admission.trimmed,
        admitted_cost=admission.admitted_cost,
        reason=admission.reason,
        lane_remaining=admission.lane_remaining,
        global_remaining=admission.global_remaining,
    )


def _num(v: float | None) -> float | None:
    return None if v is None or math.isnan(v) else v


def _candidate_out(c: Cand) -> CandidateOut:
    return CandidateOut(
        expression=c.expression,
        alpha_id=c.alpha_id,
        train_sharpe=_num(c.train_sharpe),
        test_sharpe=_num(c.m("test", "sharpe")),
    )


@dataclass(slots=True)
class _Plan:
    pipeline: PipelineConfig
    field_set: FieldSet
    templates: list[Template]
    budget: int
    run: PipelineRun
    generated: list[Cand]
    eligible: list[Cand]
    rejected: list[tuple[str, str]]
    admission: Admission


async def _build_plan(body: PlanRequest, state: State) -> _Plan:
    pipeline = _pipeline(body.pipeline)
    types = load_alpha_types()
    if pipeline.alpha_type not in types:
        raise refuse(
            422,
            "unknown_alpha_type",
            f"Pipeline {pipeline.name!r} names alpha type {pipeline.alpha_type!r}, "
            "which has no YAML.",
        )
    row = await _field_set(state, body.field_set_id)
    alpha_type = _scoped_to(types[pipeline.alpha_type], row)
    resolved = await resolve(row, state.queries)
    field_set = field_infos(resolved["fields"])
    if not field_set:
        raise refuse(
            422, "empty_field_set", f"Field set {row.name!r} resolves to no fields right now."
        )

    templates = [await _template(state, tid) for tid in body.template_ids]
    budget = body.budget if body.budget is not None else pipeline.default_budget

    governor = state.engine.governor
    if governor is None:
        raise refuse(503, "QUOTA_GOVERNOR_OFF", "The Quota Governor is not running.")

    run = PipelineRun(
        InMemoryResearchStore(),
        BrainSimulator(state.engine, state.db, state.endpoints, pipeline.lane),
        governor,
        state.endpoints,
        pipeline,
        alpha_type,
        field_set,
        templates,
        budget=budget,
        target_dataset=next((r.get("dataset_id") for r in resolved["fields"]), None),
    )
    generated = run.generate()
    eligible, rejected = run.lint(generated)
    admission = await governor.admit(pipeline.lane, [1] * budget)
    return _Plan(pipeline, row, templates, budget, run, generated, eligible, rejected, admission)


# --- routes -------------------------------------------------------------------


@router.get("")
async def list_pipelines() -> list[PipelineOut]:
    """Every pipeline config under ``config/forge/pipelines`` (CLAUDE.md rule 7: the lane, the
    template complexities and the budget are platform/personal choices, kept in YAML)."""
    return [
        PipelineOut(
            name=p.name,
            alpha_type=p.alpha_type,
            lane=p.lane,
            template_complexity=list(p.template_complexity),
            correlation_kind=p.correlation_kind,
            correlation_top_n=p.correlation_top_n,
            check_top_n=p.check_top_n,
            default_budget=p.default_budget,
        )
        for p in sorted(load_pipelines().values(), key=lambda p: p.name)
    ]


@router.post("/preview")
async def preview(body: PlanRequest, state: State) -> PlanPreview:
    """What this pipeline would generate, what lint would keep, and what the Quota Governor
    would admit — all free: nothing here queues a simulation."""
    plan = await _build_plan(body, state)
    sample = [RejectedCandidate(expression=e, reason=r) for e, r in plan.rejected[:20]]
    return PlanPreview(
        pipeline=plan.pipeline.name,
        field_set=plan.field_set.name,
        templates=[t.id for t in plan.templates],
        budget=plan.budget,
        generated=len(plan.generated),
        eligible=len(plan.eligible),
        rejected_sample=sample,
        rejected_total=len(plan.rejected),
        admission=_admission_out(plan.admission),
    )


@router.post("/run")
async def run_pipeline(body: RunRequest, state: State) -> RunStarted:
    """Start a pipeline run in the background. Spends real quota through the Quota Governor
    the orchestrator already calls; never submits anything (CLAUDE.md rule 1)."""
    plan = await _build_plan(body, state)

    task = await state.tasks.start(
        "pipeline",
        f"{plan.pipeline.name} on {plan.field_set.name}",
        pipeline=plan.pipeline.name,
        fieldSet=plan.field_set.name,
        budget=plan.budget,
    )
    await pipelines_store.start_run(
        state.db,
        task.id,
        pipeline=plan.pipeline.name,
        field_set=plan.field_set.name,
        lane=plan.pipeline.lane,
        template_ids=[t.id for t in plan.templates],
        budget=plan.budget,
    )

    def log(message: str) -> None:
        spawn(state.tasks.update(task, detail=message), name="pipeline-run-log")
        spawn(
            pipelines_store.update_run(state.db, task.id, detail=message),
            name="pipeline-run-log-db",
        )

    run = PipelineRun(
        plan.run.store,
        plan.run.simulator,
        plan.run.governor,
        plan.run.checker,
        plan.pipeline,
        plan.run.type,
        plan.run.field_set,
        plan.templates,
        budget=plan.budget,
        target_dataset=plan.run.target_dataset,
        log=log,
    )

    async def execute() -> None:
        try:
            report = await run.run()
            # Before the report is published: a finished run is always on the Board.
            await record_run(
                state.db,
                report,
                pipeline=plan.pipeline.name,
                alpha_type=plan.pipeline.alpha_type,
                correlation=plan.pipeline.correlation_kind,
                run_id=task.id,
            )
            out = RunReport(
                status="done",
                pipeline=task.id,
                spent=report.research.spent,
                candidates=[_candidate_out(c) for c in report.research.candidates],
                finalists=[_candidate_out(c) for c in report.research.finalists],
                rejected=[RejectedCandidate(expression=e, reason=r) for e, r in report.rejected],
                checks=report.checks,
                correlations=report.correlations,
            )
            await pipelines_store.finish_run(
                state.db, task.id, status="done", report=out.model_dump(mode="json")
            )
            await state.tasks.finish(task, state="done")
        except Exception as exc:  # noqa: BLE001 - surfaced through the task record, not raised
            await pipelines_store.finish_run(state.db, task.id, status="failed", error=str(exc))
            await state.tasks.finish(task, state="failed", error=str(exc))

    spawn(execute(), name="pipeline-run")
    return RunStarted(
        task_id=task.id,
        pipeline=plan.pipeline.name,
        field_set=plan.field_set.name,
        budget=plan.budget,
    )


@router.get("/runs/{task_id}")
async def get_run(task_id: str, state: State) -> RunReport:
    """The finished report for a run started with ``POST /run``, read from its durable row —
    survives past ``TaskRegistry``'s 60s linger and a server restart.

    While it is still going, the caller is expected to watch the ``tasks`` websocket topic for
    progress and call this once the task's state turns ``done``.
    """
    row = await pipelines_store.get_run(state.db, task_id)
    if row is None:
        raise refuse(404, "not_found", f"No pipeline run {task_id!r}.")
    if row.status == "failed":
        raise refuse(500, "run_failed", row.error or "The pipeline run failed.")
    if row.status != "done" or row.report is None:
        # Not 202: a 2xx response here would read as a `RunReport` to the frontend's http client.
        # `not_ready` is a refusal the screen polls past, same as any other `ApiError` code.
        raise refuse(409, "not_ready", "Still running — watch the tasks topic for progress.")
    return RunReport.model_validate(row.report)


@router.get("/runs")
async def list_runs(state: State) -> list[RunSummary]:
    """Every pipeline run this process knows about, most recent first — the history panel."""
    rows = await pipelines_store.list_runs(state.db)
    return [
        RunSummary(
            task_id=r.id,
            pipeline=r.pipeline,
            field_set=r.field_set,
            lane=r.lane,
            budget=r.budget,
            status=r.status,
            candidates_count=r.candidates_count,
            finalists_count=r.finalists_count,
            rejected_count=r.rejected_count,
            spent=r.spent,
            started_at=r.started_at,
            finished_at=r.finished_at,
        )
        for r in rows
    ]
