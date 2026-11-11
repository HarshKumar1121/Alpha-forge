"""What would be expired, and why — pure decisions, no deletes.

Two growth leaks, two TTL rules:

* ``brain_cache`` (SQLite): a BRAIN answer older than :data:`CACHE_TTL_DAYS` is expired,
  *unless* it is a ``check:`` or ``correlation:*:`` entry for an Alpha that is either on the
  Submittable Board right now, or was itself fetched within :data:`PROTECT_RECENT_DAYS` —
  BRAIN rate-limits correlation checks hourly, so a page that just asked must not be made to
  ask again a minute later.
* ``alpha_pnl`` (DuckDB): an Alpha's daily series is expired once it is both never submitted
  and not read (:mod:`.access`) in :data:`PNL_IDLE_DAYS`. A submitted Alpha's series is never
  a candidate — Portfolio and the correlation checker need it always.

Nothing here executes a delete. The cleanup job (``forge/storage/*`` once part (b) lands) reads
:func:`dry_run_report`, writes :func:`check_history_rows` to :class:`StorageCheckHistory` first,
then deletes exactly the rows these functions named.
"""

from dataclasses import dataclass, field
from datetime import UTC, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from ...db.models import BrainCache, utcnow
from ..board import store as board_store
from ..board.models import PipelineAlpha
from .models import StorageAccess

if TYPE_CHECKING:
    from datetime import datetime

    from ...db.duck import Catalog
    from ...db.sqlite import Database

#: Plain BRAIN answers (``alpha:``, ``performance:``, ``pnl:``, and any ``check:``/
#: ``correlation:`` not covered by the exception below) expire after this long.
CACHE_TTL_DAYS = 30
#: A ``check:``/``correlation:`` entry for an Alpha fetched this recently is kept regardless
#: of Board membership — BRAIN's correlation checks are rate-limited to once an hour.
PROTECT_RECENT_DAYS = 7
#: How far back the Submittable Board's own default view (``forge_board.Days``, 2-14) can
#: reach; used here as the generous end of that range so a cleanup run never outpaces what the
#: Board screen can still show.
BOARD_LOOKBACK_DAYS = 14
#: A never-submitted Alpha's ``alpha_pnl`` series expires once idle this long.
PNL_IDLE_DAYS = 30


def _as_utc(value: datetime | None) -> datetime | None:
    """DuckDB's ``TIMESTAMP`` has no zone and comes back naive; assume it means UTC, as every
    writer here (``utcnow()``) does."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _protected_prefix(key: str) -> str | None:
    """The Alpha id a ``check:``/``correlation:*:`` key is about, or ``None`` for any other key."""
    parts = key.split(":")
    if parts[0] == "check" and len(parts) == 2:
        return parts[1]
    if parts[0] == "correlation" and len(parts) == 3:
        return parts[2]
    return None


async def protected_alpha_ids(db: Database) -> set[str]:
    """Alpha ids the Submittable Board could show right now, within its widest window."""
    tz = board_store.timezone()
    start = board_store.since(BOARD_LOOKBACK_DAYS, tz)
    async with db.session() as session:
        rows = (
            await session.scalars(
                select(PipelineAlpha.alpha_id).where(PipelineAlpha.created_at >= start)
            )
        ).all()
    return set(rows)


@dataclass
class CheckHistoryRow:
    alpha_id: str
    check_name: str
    result: str | None
    value: Any
    limit_value: Any


def check_history_rows(alpha_id: str, body: dict[str, Any]) -> list[CheckHistoryRow]:
    """The compact, permanent verdicts a ``check:{alpha_id}`` cache body carries.

    Called on a ``check:`` entry right before it is allowed to expire, so PASS/FAIL/WARNING
    survives even once the full BRAIN response is deleted.
    """
    checks = ((body.get("is") or {}).get("checks")) or []
    return [
        CheckHistoryRow(
            alpha_id=alpha_id,
            check_name=str(c.get("name")),
            result=c.get("result"),
            value=c.get("value"),
            limit_value=c.get("limit"),
        )
        for c in checks
        if isinstance(c, dict) and c.get("name")
    ]


@dataclass
class CacheExpiry:
    key: str
    fetched_at: datetime
    #: Permanent records to write (via :class:`StorageCheckHistory`) before this key is deleted.
    #: Empty for every key that is not a ``check:`` entry.
    preserve: list[CheckHistoryRow] = field(default_factory=list)


async def expired_cache_rows(db: Database, *, now: datetime | None = None) -> list[CacheExpiry]:
    """Every ``brain_cache`` row the TTL rule above says is safe to delete."""
    now = now or utcnow()
    cutoff = now - timedelta(days=CACHE_TTL_DAYS)
    recent_cutoff = now - timedelta(days=PROTECT_RECENT_DAYS)
    protected = await protected_alpha_ids(db)
    async with db.session() as session:
        rows = (await session.scalars(select(BrainCache))).all()
    expired: list[CacheExpiry] = []
    for row in rows:
        if row.fetched_at >= cutoff:
            continue
        alpha_id = _protected_prefix(row.key)
        if alpha_id is not None and (alpha_id in protected or row.fetched_at >= recent_cutoff):
            continue
        preserve = (
            check_history_rows(alpha_id, row.body)
            if alpha_id is not None and row.key.startswith("check:")
            else []
        )
        expired.append(CacheExpiry(key=row.key, fetched_at=row.fetched_at, preserve=preserve))
    return expired


async def expired_pnl_alpha_ids(
    db: Database, catalog: Catalog, *, now: datetime | None = None
) -> list[str]:
    """Alpha ids whose ``alpha_pnl`` series is both never submitted and idle past the TTL."""
    now = now or utcnow()
    candidates = await catalog.query(
        "SELECT a.alpha_id AS alpha_id, a.fetched_at AS fetched_at FROM alpha a "
        "WHERE a.date_submitted IS NULL "
        "AND a.alpha_id IN (SELECT DISTINCT alpha_id FROM alpha_pnl)"
    )
    if not candidates:
        return []
    async with db.session() as session:
        accessed = (await session.scalars(select(StorageAccess))).all()
    last_access = {row.alpha_id: row.last_accessed_at for row in accessed}
    cutoff = now - timedelta(days=PNL_IDLE_DAYS)
    expired = []
    for row in candidates:
        alpha_id = row["alpha_id"]
        # No recorded access yet (rollout, or an Alpha never opened since) falls back to when
        # it was last fetched from BRAIN at all — the best idleness signal available.
        seen = last_access.get(alpha_id) or _as_utc(row["fetched_at"])
        if seen is None or seen < cutoff:
            expired.append(alpha_id)
    return expired


@dataclass
class StorageDryRunReport:
    cache_total: int
    cache_expired: int
    cache_preserved_checks: int
    pnl_alpha_total: int
    pnl_alpha_expired: int


async def dry_run_report(
    db: Database, catalog: Catalog, *, now: datetime | None = None
) -> StorageDryRunReport:
    """What a real cleanup run would delete, computed against the live databases.

    Deletes nothing.
    """
    now = now or utcnow()
    async with db.session() as session:
        cache_total = await session.scalar(select(func.count()).select_from(BrainCache))
    expired_cache = await expired_cache_rows(db, now=now)
    pnl_total_row = await catalog.scalar("SELECT count(DISTINCT alpha_id) FROM alpha_pnl")
    expired_pnl = await expired_pnl_alpha_ids(db, catalog, now=now)
    return StorageDryRunReport(
        cache_total=cache_total or 0,
        cache_expired=len(expired_cache),
        cache_preserved_checks=sum(len(e.preserve) for e in expired_cache),
        pnl_alpha_total=int(pnl_total_row or 0),
        pnl_alpha_expired=len(expired_pnl),
    )
