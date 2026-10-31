"""Writing to the quota ledger, and the sums the governor reads back from it.

Plain functions over a session, so alpha-harness's engine can call them from inside its own
transactions without holding any Forge state: the spend is committed in the same transaction
that records BRAIN's simulation id, and a crash cannot separate the two.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from ...brain.filters import PLATFORM_TZ
from ...brain.platform import RA_CHILDREN
from ...brain.schemas import SimulationType
from ...db.models import SimStatus, SimulationRecord, utcnow
from ...engine.lifecycle import SIMULATION_COST
from .config import load
from .models import QuotaLedger

if TYPE_CHECKING:
    from collections.abc import Iterable
    from zoneinfo import ZoneInfo

    from sqlalchemy.ext.asyncio import AsyncSession

#: Rows the engine holds but BRAIN has not yet been charged for. PENDING is a POST in the air.
WAITING = (SimStatus.QUEUED, SimStatus.PENDING)


def platform_day(moment: datetime | None = None) -> str:
    """BRAIN's day a moment falls in — the US-Eastern date its quota resets on."""
    return (moment or utcnow()).astimezone(PLATFORM_TZ).strftime("%Y-%m-%d")


def platform_day_bounds(moment: datetime) -> tuple[datetime, datetime]:
    """Start and end of BRAIN's day around ``moment``, in UTC.

    Built from the next local *date*, not ``+24h``, so the 23- and 25-hour days around a
    daylight-saving change still end at midnight Eastern.
    """
    local = moment.astimezone(PLATFORM_TZ)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), PLATFORM_TZ)
    return start.astimezone(UTC), end.astimezone(UTC)


def cost_of(sim_type: str | None) -> int:
    """What one simulation costs off the day: a region-agnostic one is charged per region.

    The Python twin of :data:`alpha_harness.engine.lifecycle.SIMULATION_COST`.
    """
    return RA_CHILDREN if sim_type == SimulationType.REGION_AGNOSTIC else 1


# -- writes -------------------------------------------------------------------


async def record_spend(
    session: AsyncSession, record_ids: Iterable[int], *, now: datetime | None = None
) -> int:
    """Charge the lanes of simulations BRAIN just accepted. Returns the cost recorded.

    Batch parents are skipped (BRAIN charges their children, which are passed alongside).
    A row already charged is not charged again: reconcile can re-launch an orphaned row, and
    the ledger must count what BRAIN counts — once.
    """
    ids = list(dict.fromkeys(record_ids))
    if not ids:
        return 0
    moment = now or utcnow()
    charged = set(
        await session.scalars(
            select(QuotaLedger.record_id).where(
                QuotaLedger.record_id.in_(ids), QuotaLedger.cached.is_(False)
            )
        )
    )
    records = await session.scalars(
        select(SimulationRecord).where(
            SimulationRecord.id.in_(ids), SimulationRecord.is_batch.is_(False)
        )
    )
    config = load()
    total = 0
    for record in records:
        if record.id in charged:
            continue
        cost = cost_of(record.sim_type)
        session.add(
            QuotaLedger(
                day=platform_day(moment),
                lane=config.lane_of(record.task),
                task=record.task,
                record_id=record.id,
                request_hash=record.request_hash,
                cost=cost,
                cached=False,
                created_at=moment,
            )
        )
        total += cost
    return total


def record_cache_hit(
    session: AsyncSession, record: SimulationRecord, *, now: datetime | None = None
) -> None:
    """Note a request answered from the dedup memory: progress for the lane, cost zero.

    ``record`` is the SKIPPED row the engine just added; it has no id until flushed, so the
    ledger keeps its hash and alpha instead.
    """
    moment = now or utcnow()
    session.add(
        QuotaLedger(
            day=platform_day(moment),
            lane=load().lane_of(record.task),
            task=record.task,
            record_id=None,
            request_hash=record.request_hash,
            alpha_id=record.alpha_id,
            cost=0,
            cached=True,
            created_at=moment,
        )
    )


# -- reads --------------------------------------------------------------------


async def spent_by_lane(session: AsyncSession, day: str) -> dict[str, int]:
    """Simulations charged per lane on BRAIN's ``day``."""
    rows = await session.execute(
        select(QuotaLedger.lane, func.sum(QuotaLedger.cost))
        .where(QuotaLedger.day == day, QuotaLedger.cached.is_(False))
        .group_by(QuotaLedger.lane)
    )
    return {lane: int(total or 0) for lane, total in rows.tuples()}


async def cached_by_lane(session: AsyncSession, day: str) -> dict[str, int]:
    """Requests answered from the cache per lane on ``day`` — each one a simulation saved."""
    rows = await session.execute(
        select(QuotaLedger.lane, func.count())
        .where(QuotaLedger.day == day, QuotaLedger.cached.is_(True))
        .group_by(QuotaLedger.lane)
    )
    return {lane: int(total) for lane, total in rows.tuples()}


async def spent_since(session: AsyncSession, moment: datetime) -> int:
    """Simulations charged since ``moment``: the rolling hour that pacing caps."""
    total = await session.scalar(
        select(func.sum(QuotaLedger.cost)).where(
            QuotaLedger.created_at >= moment, QuotaLedger.cached.is_(False)
        )
    )
    return int(total or 0)


async def hourly(session: AsyncSession, day: str, tz: ZoneInfo) -> dict[str, int]:
    """Spend on ``day`` bucketed by the hour it happened on YOUR clock, ``"HH:00"`` keys.

    Bucketed in Python: at most a few thousand rows a day, and SQLite has no time zones.
    """
    buckets: dict[str, int] = defaultdict(int)
    rows = await session.execute(
        select(QuotaLedger.created_at, QuotaLedger.cost).where(
            QuotaLedger.day == day, QuotaLedger.cached.is_(False)
        )
    )
    for created_at, cost in rows.tuples():
        buckets[created_at.astimezone(tz).strftime("%H:00")] += cost
    return dict(buckets)


async def waiting_by_lane(session: AsyncSession) -> dict[str, int]:
    """Cost of the work the engine holds but has not sent yet, per lane.

    Read from ``simulation_record`` grouped by task — the engine's queue is the truth about
    what is waiting, so the ledger does not duplicate it.
    """
    config = load()
    rows = await session.execute(
        select(SimulationRecord.task, func.sum(SIMULATION_COST))
        .where(SimulationRecord.status.in_(WAITING), SimulationRecord.is_batch.is_(False))
        .group_by(SimulationRecord.task)
    )
    waiting: dict[str, int] = defaultdict(int)
    for task, total in rows.tuples():
        waiting[config.lane_of(task)] += int(total or 0)
    return dict(waiting)
