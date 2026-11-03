"""Workshop trials, and the evidence challenge read from the researcher's own history.

The evidence challenge is a read over ``forge_pipeline_alpha``, the same table
``forge/agent/store.py``'s ``failures_by_reason`` reads — it is never out of sync with what the
Submittable Board already shows, just filtered to the fields/pipeline a hypothesis is about.
"""

from collections import Counter
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ...db.models import utcnow
from ..board.models import PipelineAlpha
from .models import WorkshopTrial

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...db.sqlite import Database


async def challenge(
    db: Database,
    *,
    pipeline: str = "",
    fields: Sequence[str] = (),
    days: int = 90,
) -> dict[str, Any]:
    """ "Your last N alphas on these fields failed this check" — or nothing yet on file.

    Matches a row by field (``PipelineAlpha.field``) when fields are given, and by pipeline
    name when one is given; with neither, it reads the whole recent history, same as
    ``failures_by_reason``. A row counts toward "no decay" when its simulated settings asked
    for none (``decay`` 0 or absent) — the repair ``court.yaml`` suggests for ``HIGH_TURNOVER``.
    """
    since = utcnow() - timedelta(days=days)
    field_set = set(fields)
    async with db.session() as session:
        stmt = select(PipelineAlpha).where(PipelineAlpha.created_at >= since)
        if pipeline:
            stmt = stmt.where(PipelineAlpha.pipeline == pipeline)
        rows = list((await session.scalars(stmt)).all())
    if field_set:
        rows = [r for r in rows if r.field in field_set]

    check_counts: Counter[str] = Counter()
    check_no_decay: Counter[str] = Counter()
    for row in rows:
        decayed = bool((row.settings or {}).get("decay"))
        for check in row.sim_checks or []:
            name = str(check.get("name", "")).upper()
            result = str(check.get("result", "")).upper()
            if name and result in ("FAIL", "ERROR"):
                check_counts[name] += 1
                if not decayed:
                    check_no_decay[name] += 1

    findings = [
        {
            "check": name,
            "count": count,
            "sample_size": len(rows),
            "no_decay": check_no_decay.get(name, 0),
            "note": _sentence(name, count, len(rows), check_no_decay.get(name, 0), fields),
        }
        for name, count in check_counts.most_common()
    ]
    return {
        "pipeline": pipeline,
        "fields": list(fields),
        "days": days,
        "sample_size": len(rows),
        "findings": findings,
    }


def _sentence(check: str, count: int, total: int, no_decay: int, fields: Sequence[str]) -> str:
    on = f" on {', '.join(fields)}" if fields else ""
    base = f"Your last {total} alpha{'s' if total != 1 else ''}{on} failed {check} {count} time(s)."
    if no_decay and no_decay == count:
        return base + " None of them used decay — court.yaml's HIGH_TURNOVER repair is decay."
    if no_decay:
        return base + f" {no_decay} of those had no decay set."
    return base


# --- trials --------------------------------------------------------------------------------


async def create_trial(
    db: Database,
    *,
    hypothesis_id: int | None,
    hypothesis: dict[str, Any],
    pipeline: str,
    field_set_id: int,
    fields: Sequence[str],
) -> WorkshopTrial:
    row = WorkshopTrial(
        hypothesis_id=hypothesis_id,
        hypothesis=hypothesis,
        pipeline=pipeline,
        field_set_id=field_set_id,
        fields=list(fields),
    )
    async with db.session() as session:
        session.add(row)
        await session.flush()
        await session.refresh(row)
    return row


async def get_trial(db: Database, trial_id: int) -> WorkshopTrial | None:
    async with db.session() as session:
        return await session.get(WorkshopTrial, trial_id)


async def list_trials(db: Database, limit: int = 50) -> list[WorkshopTrial]:
    async with db.session() as session:
        return list(
            (
                await session.scalars(
                    select(WorkshopTrial).order_by(WorkshopTrial.created_at.desc()).limit(limit)
                )
            ).all()
        )


async def finish_trial(
    db: Database, trial_id: int, *, case: dict[str, Any], verdict: str, spent: int
) -> WorkshopTrial | None:
    async with db.session() as session:
        row = await session.get(WorkshopTrial, trial_id)
        if row is None:
            return None
        row.status, row.case, row.verdict, row.spent = "done", case, verdict, spent
        row.finished_at = utcnow()
        await session.flush()
        await session.refresh(row)
    return row


async def fail_trial(db: Database, trial_id: int, *, error: str) -> WorkshopTrial | None:
    async with db.session() as session:
        row = await session.get(WorkshopTrial, trial_id)
        if row is None:
            return None
        row.status, row.error, row.finished_at = "failed", error, utcnow()
        await session.flush()
        await session.refresh(row)
    return row


async def set_trial_proposal(db: Database, trial_id: int, proposal_id: int) -> None:
    async with db.session() as session:
        row = await session.get(WorkshopTrial, trial_id)
        if row is not None:
            row.proposal_id = proposal_id
