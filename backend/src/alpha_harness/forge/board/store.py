"""Reading and writing what the board needs: pipeline alphas, and BRAIN's kept answers.

BRAIN's answers live in alpha-harness's own ``brain_cache`` under the keys the Alpha page
already uses (``check:``, ``correlation:<kind>:``, ``performance:``, ``pnl:``), so an answer
fetched on either screen is never paid for again on the other (CLAUDE.md rule 3).
"""

import math
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ...api.forge_studio import expression_hash
from ...brain.schemas import RecordSet
from ...db.models import BrainCache, utcnow
from ...vault.metrics import daily_pnl
from .. import analytics as an
from .. import config
from ..research.description import compose
from ..studio.models import AlphaDescription
from .board import DescriptionOut, Entry, correlation_kind, is_power_pool
from .models import PipelineAlpha

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...db.sqlite import Database
    from ..pipelines.orchestrator import PipelineReport
    from ..research.planner import Cand


def timezone() -> ZoneInfo:
    """The user's clock (``lanes.yaml``), which decides what "today" means on the board."""
    return ZoneInfo(str(config.lanes().get("timezone") or "UTC"))


def day_of(moment: datetime, tz: ZoneInfo) -> date:
    return moment.astimezone(tz).date()


def since(days: int, tz: ZoneInfo, now: datetime | None = None) -> datetime:
    """The start of the earliest of the last ``days`` days on the user's clock, in UTC."""
    today = day_of(now or utcnow(), tz)
    first = today - timedelta(days=days - 1)
    return datetime(first.year, first.month, first.day, tzinfo=tz).astimezone(UTC)


# --- brain_cache -----------------------------------------------------------------------


async def kept(db: Database, keys: Sequence[str]) -> dict[str, dict[str, Any]]:
    if not keys:
        return {}
    async with db.session() as session:
        rows = (await session.scalars(select(BrainCache).where(BrainCache.key.in_(keys)))).all()
    return {r.key: r.body for r in rows}


async def keep(db: Database, key: str, body: dict[str, Any]) -> None:
    async with db.session() as session:
        await session.merge(BrainCache(key=key, body=body, fetched_at=utcnow()))


# --- recording a pipeline run -----------------------------------------------------------


def _num(v: float | None) -> float | None:
    return None if v is None or math.isnan(v) else float(v)


def row_for(c: Cand, pipeline: str, alpha_type: str, run_id: str) -> PipelineAlpha:
    alpha = c.alpha or {}
    settings = alpha.get("settings") or c.settings
    return PipelineAlpha(
        alpha_id=str(c.alpha_id),
        pipeline=pipeline,
        alpha_type=alpha_type,
        region=str(settings.get("region") or ""),
        expression=c.expression,
        settings=dict(c.settings),
        field=c.field,
        template_id=c.template,
        stage=c.stage,
        train_sharpe=_num(c.train_sharpe),
        test_sharpe=_num(c.m("test", "sharpe")),
        train_fitness=_num(c.m("train", "fitness")),
        turnover=_num(c.m("is", "turnover")),
        sim_checks=[x for x in (alpha.get("is") or {}).get("checks") or [] if isinstance(x, dict)],
        run_id=run_id,
    )


async def record_alphas(db: Database, rows: Sequence[PipelineAlpha]) -> int:
    """Insert rows for alphas not already on the board; an alpha is recorded once."""
    if not rows:
        return 0
    async with db.session() as session:
        known = set(
            (
                await session.scalars(
                    select(PipelineAlpha.alpha_id).where(
                        PipelineAlpha.alpha_id.in_([r.alpha_id for r in rows])
                    )
                )
            ).all()
        )
        fresh = {r.alpha_id: r for r in rows if r.alpha_id not in known}
        session.add_all(fresh.values())
    return len(fresh)


async def record_run(
    db: Database,
    report: PipelineReport,
    *,
    pipeline: str,
    alpha_type: str,
    correlation: str,
    run_id: str,
) -> int:
    """Put a finished run on the board: its alphas, and the checks and correlations it paid
    for, under the keys every screen reads."""
    rows = [
        row_for(c, pipeline, alpha_type, run_id)
        for c in report.research.candidates
        if c.alpha_id is not None
    ]
    for alpha_id, body in report.checks.items():
        await keep(db, f"check:{alpha_id}", body)
    for alpha_id, body in report.correlations.items():
        await keep(db, f"correlation:{correlation}:{alpha_id}", body)
    return await record_alphas(db, rows)


# --- loading the board ------------------------------------------------------------------


def _series(body: dict[str, Any] | None) -> Any:
    if not body:
        return None
    rows = daily_pnl(RecordSet.model_validate(body).rows())
    return an.to_series(rows) if rows else None


def _checks(body: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    if body is None:
        return None
    return [c for c in (body.get("is") or {}).get("checks") or [] if isinstance(c, dict)]


async def rows_since(db: Database, start: datetime) -> list[PipelineAlpha]:
    async with db.session() as session:
        return list(
            (
                await session.scalars(
                    select(PipelineAlpha)
                    .where(PipelineAlpha.created_at >= start)
                    .order_by(PipelineAlpha.created_at)
                )
            ).all()
        )


async def entries(db: Database, rows: Sequence[PipelineAlpha], tz: ZoneInfo) -> list[Entry]:
    """Every row with what ``brain_cache`` and the Studio's descriptions already hold for it."""
    ids = [r.alpha_id for r in rows]
    cache = await kept(
        db,
        [
            f"{prefix}{a}"
            for a in ids
            for prefix in (
                "check:",
                "pnl:",
                "performance:",
                "correlation:self:",
                "correlation:power-pool:",
            )
        ],
    )
    hashes = {expression_hash(r.expression): r.alpha_id for r in rows}
    async with db.session() as session:
        written = (
            await session.scalars(
                select(AlphaDescription).where(AlphaDescription.expression_hash.in_(list(hashes)))
            )
        ).all()
    descriptions = {
        hashes[d.expression_hash]: DescriptionOut(
            text=compose(d.idea, d.data_rationale, d.operator_rationale), edited=d.edited
        )
        for d in written
    }

    out: list[Entry] = []
    for r in rows:
        checked = _checks(cache.get(f"check:{r.alpha_id}"))
        checks = checked if checked is not None else list(r.sim_checks or [])
        e = Entry(
            alpha_id=r.alpha_id,
            day=day_of(r.created_at, tz),
            alpha_type=r.alpha_type,
            pipeline=r.pipeline,
            region=r.region,
            expression=r.expression,
            field=r.field,
            template_id=r.template_id,
            stage=r.stage,
            train_sharpe=r.train_sharpe,
            test_sharpe=r.test_sharpe,
            train_fitness=r.train_fitness,
            turnover=r.turnover,
            checks=checks,
            checks_source="check" if checked is not None else ("simulation" if checks else None),
            performance=cache.get(f"performance:{r.alpha_id}"),
            pnl=_series(cache.get(f"pnl:{r.alpha_id}")),
            description=descriptions.get(r.alpha_id),
            repairs_queued=r.repairs_queued,
        )
        e.correlation = cache.get(f"correlation:{correlation_kind(is_power_pool(e))}:{r.alpha_id}")
        out.append(e)
    return out
