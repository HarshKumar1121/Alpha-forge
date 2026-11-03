"""Reading and writing ``forge_pipeline_run`` rows — a pipeline run's durable identity.

Written as soon as a run starts (``start_run``), kept current while it progresses
(``update_run``), and sealed once it finishes or fails (``finish_run``), so ``GET
/runs/{id}`` and the history panel never depend on the in-memory ``TaskRegistry`` entry,
which is pruned 60 seconds after a task finishes and lost entirely on restart.
"""

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ...db.models import utcnow
from .models import PipelineRunRecord

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...db.sqlite import Database


async def start_run(
    db: Database,
    run_id: str,
    *,
    pipeline: str,
    field_set: str,
    lane: str,
    template_ids: Sequence[str],
    budget: int,
) -> None:
    async with db.session() as session:
        await session.merge(
            PipelineRunRecord(
                id=run_id,
                pipeline=pipeline,
                field_set=field_set,
                lane=lane,
                template_ids=list(template_ids),
                budget=budget,
            )
        )


async def update_run(db: Database, run_id: str, *, detail: str) -> None:
    async with db.session() as session:
        row = await session.get(PipelineRunRecord, run_id)
        if row is not None:
            row.detail = detail


async def finish_run(
    db: Database,
    run_id: str,
    *,
    status: str,
    report: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Seal a run. ``report`` is the full RunReport payload on success; ``error`` on failure."""
    async with db.session() as session:
        row = await session.get(PipelineRunRecord, run_id)
        if row is None:
            return
        row.status = status
        row.error = error
        row.finished_at = utcnow()
        if report is not None:
            row.report = report
            row.candidates_count = len(report.get("candidates") or [])
            row.finalists_count = len(report.get("finalists") or [])
            row.rejected_count = len(report.get("rejected") or [])
            #: Every simulated candidate already passed the eligibility lint.
            row.eligible_count = row.candidates_count
            row.spent = report.get("spent") or 0


async def get_run(db: Database, run_id: str) -> PipelineRunRecord | None:
    async with db.session() as session:
        return await session.get(PipelineRunRecord, run_id)


async def list_runs(db: Database, limit: int = 50) -> list[PipelineRunRecord]:
    async with db.session() as session:
        return list(
            (
                await session.scalars(
                    select(PipelineRunRecord)
                    .order_by(PipelineRunRecord.started_at.desc())
                    .limit(limit)
                )
            ).all()
        )
