"""``/api/forge/agent``: the agent's tool schemas, its memory (hypotheses, lessons, proposals),
and the one route that can spend quota.

Every route except ``POST /proposals/{id}/approve`` is a read or a free write — the same split
``forge/agent/tools.py`` enforces for the model's own tool-use loop. That route is deliberately
not something a model can reach on its own: it is a plain REST call the UI's Approve button
makes, and :func:`~..forge.agent.execution.execute_proposal` forces the spend into the reserve
lane regardless of what the proposal asked for (CLAUDE.md rule 5).
"""

from typing import Any

from fastapi import APIRouter
from pydantic import Field

from ..forge.agent import store as agent_store
from ..forge.agent import weekly
from ..forge.agent.execution import execute_proposal
from ..forge.agent.tools import TOOL_SCHEMAS
from ..schemas import Out
from ..tasks import spawn
from .deps import State, refuse

router = APIRouter(prefix="/api/forge/agent", tags=["forge-agent"])


class ToolSchema(Out):
    name: str
    description: str
    input_schema: dict[str, Any]


class Hypothesis(Out):
    id: int
    title: str
    mechanism: str
    expected_sign: str
    horizon: str
    pipeline: str
    template: str
    fields: list[str]
    status: str
    verdict: str
    created_at: str


class ProposeHypothesis(Out):
    title: str = Field(min_length=1, max_length=200)
    mechanism: str = Field(min_length=1)
    expected_sign: str = ""
    horizon: str = ""
    pipeline: str = ""
    template: str = ""
    fields: list[str] = Field(default_factory=list)


class Lesson(Out):
    id: int
    text: str
    source: str
    tags: list[str]
    created_at: str


class RecordLesson(Out):
    text: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)


class Failure(Out):
    check: str
    count: int


class PipelineRunSummary(Out):
    run_id: str
    pipeline: str
    field_set: str
    lane: str
    budget: int
    status: str
    candidates: int
    finalists: int
    rejected: int
    spent: int
    started_at: str
    finished_at: str | None


class Proposal(Out):
    id: int
    kind: str
    payload: dict[str, Any]
    rationale: str
    source: str
    status: str
    run_id: str
    error: str
    created_at: str


class ProposePipelineRun(Out):
    pipeline: str
    field_set_id: int
    template_ids: list[str] = Field(min_length=1)
    budget: int = Field(ge=1)
    rationale: str = Field(min_length=1)


def _hypothesis_out(row: Any) -> Hypothesis:
    return Hypothesis(
        id=row.id,
        title=row.title,
        mechanism=row.mechanism,
        expected_sign=row.expected_sign,
        horizon=row.horizon,
        pipeline=row.pipeline,
        template=row.template,
        fields=list(row.fields or []),
        status=row.status,
        verdict=row.verdict,
        created_at=row.created_at.isoformat(),
    )


def _lesson_out(row: Any) -> Lesson:
    return Lesson(
        id=row.id,
        text=row.text,
        source=row.source,
        tags=list(row.tags or []),
        created_at=row.created_at.isoformat(),
    )


def _proposal_out(row: Any) -> Proposal:
    return Proposal(
        id=row.id,
        kind=row.kind,
        payload=row.payload,
        rationale=row.rationale,
        source=row.source,
        status=row.status,
        run_id=row.run_id,
        error=row.error,
        created_at=row.created_at.isoformat(),
    )


@router.get("/tools")
async def tools() -> list[ToolSchema]:
    """Every tool the model may call. Read-only — proof that no tool here can spend or submit
    lives in ``tests/test_forge_agent_tools.py``, not in this route."""
    return [ToolSchema.model_validate(s) for s in TOOL_SCHEMAS]


@router.get("/hypotheses")
async def list_hypotheses(state: State, status: str | None = None) -> list[Hypothesis]:
    rows = await agent_store.list_hypotheses(state.db, status=status)
    return [_hypothesis_out(r) for r in rows]


@router.post("/hypotheses")
async def propose_hypothesis(body: ProposeHypothesis, state: State) -> Hypothesis:
    row = await agent_store.propose_hypothesis(
        state.db,
        title=body.title,
        mechanism=body.mechanism,
        expected_sign=body.expected_sign,
        horizon=body.horizon,
        pipeline=body.pipeline,
        template=body.template,
        fields=body.fields,
        source="user",
    )
    return _hypothesis_out(row)


@router.get("/lessons")
async def list_lessons(state: State) -> list[Lesson]:
    rows = await agent_store.list_lessons(state.db)
    return [_lesson_out(r) for r in rows]


@router.post("/lessons")
async def record_lesson(body: RecordLesson, state: State) -> Lesson:
    row = await agent_store.record_lesson(state.db, body.text, source="user", tags=body.tags)
    return _lesson_out(row)


@router.get("/failures")
async def failures_by_reason(state: State, days: int = 30) -> list[Failure]:
    counts = await agent_store.failures_by_reason(state.db, days=days)
    return [Failure(check=name, count=count) for name, count in counts]


@router.get("/runs")
async def pipeline_runs(state: State, limit: int = 20) -> list[PipelineRunSummary]:
    rows = await agent_store.list_pipeline_runs(state.db, limit=limit)
    return [
        PipelineRunSummary(
            run_id=r.id,
            pipeline=r.pipeline,
            field_set=r.field_set,
            lane=r.lane,
            budget=r.budget,
            status=r.status,
            candidates=r.candidates_count,
            finalists=r.finalists_count,
            rejected=r.rejected_count,
            spent=r.spent,
            started_at=r.started_at.isoformat(),
            finished_at=r.finished_at.isoformat() if r.finished_at else None,
        )
        for r in rows
    ]


@router.get("/proposals")
async def list_proposals(state: State, status: str | None = None) -> list[Proposal]:
    rows = await agent_store.list_proposals(state.db, status=status)
    return [_proposal_out(r) for r in rows]


@router.post("/proposals")
async def propose_pipeline_run(body: ProposePipelineRun, state: State) -> Proposal:
    """Write an approval card. Spends nothing (CLAUDE.md rule 5) — the same write the model's
    own ``propose_pipeline_run`` tool makes."""
    row = await agent_store.create_proposal(
        state.db,
        kind="pipeline_run",
        payload={
            "pipeline": body.pipeline,
            "field_set_id": body.field_set_id,
            "template_ids": body.template_ids,
            "budget": body.budget,
        },
        rationale=body.rationale,
        source="user",
    )
    return _proposal_out(row)


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: int, state: State) -> Proposal:
    """The only route in this router that can spend quota. Marks the proposal approved and
    starts the run in the background, in the reserve lane — this is the UI's Approve button,
    never something the model's tool-use loop calls on its own. Poll ``GET /proposals`` or
    ``GET /runs`` for completion, the same pattern ``POST /api/forge/pipelines/run`` uses."""
    proposal = await agent_store.get_proposal(state.db, proposal_id)
    if proposal is None:
        raise refuse(404, "not_found", f"No proposal {proposal_id}.")
    if proposal.status != "pending":
        raise refuse(
            409, "already_decided", f"Proposal {proposal_id} is already {proposal.status}."
        )
    approved = await agent_store.set_proposal_status(state.db, proposal_id, "approved")
    if approved is None:  # pragma: no cover - just read above; only a concurrent delete gets here
        raise refuse(404, "not_found", f"No proposal {proposal_id}.")
    spawn(execute_proposal(state, approved), name="agent-proposal-execute")
    return _proposal_out(approved)


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: int, state: State) -> Proposal:
    row = await agent_store.get_proposal(state.db, proposal_id)
    if row is None:
        raise refuse(404, "not_found", f"No proposal {proposal_id}.")
    if row.status != "pending":
        raise refuse(409, "already_decided", f"Proposal {proposal_id} is already {row.status}.")
    rejected = await agent_store.set_proposal_status(state.db, proposal_id, "rejected")
    if rejected is None:  # pragma: no cover - just read above; only a concurrent delete gets here
        raise refuse(404, "not_found", f"No proposal {proposal_id}.")
    return _proposal_out(rejected)


@router.post("/weekly-summary")
async def run_weekly_summary(state: State) -> Lesson:
    """Trigger the weekly job by hand (also runs automatically; see ``forge/agent/weekly.py``)."""
    row = await weekly.run_weekly_summary(state.db)
    return _lesson_out(row)
