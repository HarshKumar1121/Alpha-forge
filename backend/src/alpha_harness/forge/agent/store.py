"""Reading and writing the agent's memory: hypotheses, lessons, proposals, and read-only views
over pipeline run history and failures by reason that already live in other tables.

Failures-by-reason and run history are not duplicated here: they are read straight from
``forge_pipeline_run`` (:mod:`..pipelines.store`) and ``forge_pipeline_alpha``
(:mod:`..board.models`), the same rows the Pipelines screen and the Submittable Board already
show, so the agent's "your last 6 sentiment alphas failed HIGH_TURNOVER" is never out of sync
with what the user sees.
"""

from collections import Counter
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ...db.models import utcnow
from ..board.models import PipelineAlpha
from ..pipelines import store as pipelines_store
from .models import AgentHypothesis, AgentLesson, AgentProposal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...db.sqlite import Database
    from ..pipelines.models import PipelineRunRecord

# --- hypotheses --------------------------------------------------------------------------


async def propose_hypothesis(
    db: Database,
    *,
    title: str,
    mechanism: str,
    expected_sign: str = "",
    horizon: str = "",
    pipeline: str = "",
    template: str = "",
    fields: Sequence[str] = (),
    source: str = "researcher",
) -> AgentHypothesis:
    row = AgentHypothesis(
        title=title,
        mechanism=mechanism,
        expected_sign=expected_sign,
        horizon=horizon,
        pipeline=pipeline,
        template=template,
        fields=list(fields),
        source=source,
        status="proposed",
    )
    async with db.session() as session:
        session.add(row)
        await session.flush()
        await session.refresh(row)
    return row


async def list_hypotheses(
    db: Database, status: str | None = None, limit: int = 50
) -> list[AgentHypothesis]:
    async with db.session() as session:
        stmt = select(AgentHypothesis).order_by(AgentHypothesis.created_at.desc()).limit(limit)
        if status is not None:
            stmt = stmt.where(AgentHypothesis.status == status)
        return list((await session.scalars(stmt)).all())


async def close_hypothesis(db: Database, hypothesis_id: int, verdict: str) -> None:
    async with db.session() as session:
        row = await session.get(AgentHypothesis, hypothesis_id)
        if row is not None:
            row.status = "closed"
            row.verdict = verdict


# --- lessons -----------------------------------------------------------------------------


async def record_lesson(
    db: Database, text: str, *, source: str = "user", tags: Sequence[str] = ()
) -> AgentLesson:
    row = AgentLesson(text=text, source=source, tags=list(tags))
    async with db.session() as session:
        session.add(row)
        await session.flush()
        await session.refresh(row)
    return row


async def list_lessons(db: Database, limit: int = 50) -> list[AgentLesson]:
    async with db.session() as session:
        return list(
            (
                await session.scalars(
                    select(AgentLesson).order_by(AgentLesson.created_at.desc()).limit(limit)
                )
            ).all()
        )


# --- proposals ---------------------------------------------------------------------------


async def create_proposal(
    db: Database,
    *,
    kind: str,
    payload: dict[str, Any],
    rationale: str,
    source: str = "researcher",
) -> AgentProposal:
    row = AgentProposal(kind=kind, payload=payload, rationale=rationale, source=source)
    async with db.session() as session:
        session.add(row)
        await session.flush()
        await session.refresh(row)
    return row


async def get_proposal(db: Database, proposal_id: int) -> AgentProposal | None:
    async with db.session() as session:
        return await session.get(AgentProposal, proposal_id)


async def list_proposals(
    db: Database, status: str | None = None, limit: int = 50
) -> list[AgentProposal]:
    async with db.session() as session:
        stmt = select(AgentProposal).order_by(AgentProposal.created_at.desc()).limit(limit)
        if status is not None:
            stmt = stmt.where(AgentProposal.status == status)
        return list((await session.scalars(stmt)).all())


async def set_proposal_status(
    db: Database, proposal_id: int, status: str, *, run_id: str = "", error: str = ""
) -> AgentProposal | None:
    async with db.session() as session:
        row = await session.get(AgentProposal, proposal_id)
        if row is None:
            return None
        row.status = status
        row.decided_at = utcnow()
        if run_id:
            row.run_id = run_id
        if error:
            row.error = error
        await session.flush()
        await session.refresh(row)
    return row


# --- pipeline run history and failures by reason ------------------------------------------


async def list_pipeline_runs(db: Database, limit: int = 20) -> list[PipelineRunRecord]:
    """The run history the Pipelines screen itself reads — nothing agent-specific about it."""
    return await pipelines_store.list_runs(db, limit)


async def failures_by_reason(db: Database, days: int = 30) -> list[tuple[str, int]]:
    """How many alphas failed each BRAIN check in the last ``days`` days, worst first.

    Reads the checks each simulation itself reported (``PipelineAlpha.sim_checks``), the same
    field the board falls back to before BRAIN's own ``/check`` has been fetched — good enough
    for "what keeps failing", which does not need BRAIN's live verdict, only the pattern.
    """
    since = utcnow() - timedelta(days=days)
    async with db.session() as session:
        rows = list(
            (
                await session.scalars(
                    select(PipelineAlpha).where(PipelineAlpha.created_at >= since)
                )
            ).all()
        )
    counts: Counter[str] = Counter()
    for row in rows:
        for check in row.sim_checks or []:
            name = str(check.get("name", "")).upper()
            result = str(check.get("result", "")).upper()
            if name and result in ("FAIL", "ERROR"):
                counts[name] += 1
    return counts.most_common()
