"""The Quota Governor's two tables, declared on alpha-harness's own ``Base``.

They live here, not in ``db/models.py``, so an upstream merge never touches them. Being on the
same ``Base`` is enough for :func:`alpha_harness.db.sqlite.migrate` to create them on startup:
this module is imported by the engine's lifecycle hook, which loads before the database opens.

``day`` is BRAIN's day (``YYYY-MM-DD`` on the US-Eastern clock it resets on), stored as text
like ``KeyUsage.day``, so "today's spend" is an equality on an indexed column rather than a
time-zone calculation in SQL.
"""

from datetime import datetime  # noqa: TC003 - SQLAlchemy reads Mapped[datetime] at runtime

from sqlalchemy import Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db.models import Base, UtcDateTime, utcnow


class QuotaLedger(Base):
    """One row per simulation that touched the day's allowance — or would have.

    A simulation BRAIN started costs ``cost`` (1, or 4 for a region-agnostic one). A cache hit
    (an identical request already simulated, answered from the dedup memory) is recorded too,
    with ``cost = 0`` and ``cached = True``: it is research progress that cost nothing, and the
    Today screen shows how much the cache saved.
    """

    __tablename__ = "quota_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[str] = mapped_column(String(10))
    lane: Mapped[str] = mapped_column(String(32))
    task: Mapped[str] = mapped_column(String(64))
    #: The ``simulation_record`` row. Unique among spends so a re-launch never counts twice.
    record_id: Mapped[int | None] = mapped_column(Integer)
    request_hash: Mapped[str] = mapped_column(String(64))
    alpha_id: Mapped[str | None] = mapped_column(String(64))
    cost: Mapped[int] = mapped_column(Integer, default=1)
    cached: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    __table_args__ = (
        Index("ix_quota_ledger_day_lane", "day", "lane"),
        Index("ix_quota_ledger_created", "created_at"),
        Index("ix_quota_ledger_record", "record_id"),
    )


class LaneState(Base):
    """The allocation a lane was given for one day.

    With ``allocation: fixed`` this is just ``lanes.yaml`` copied out, but writing it down per
    day is what lets the Phase 9 bandit move shares and still explain any past day exactly.
    """

    __tablename__ = "lane_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[str] = mapped_column(String(10))
    lane: Mapped[str] = mapped_column(String(32))
    share: Mapped[float] = mapped_column(Float)
    #: ``share`` x the day's budget, in simulations.
    cap: Mapped[int] = mapped_column(Integer)
    allocation: Mapped[str] = mapped_column(String(16), default="fixed")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("day", "lane", name="uq_lane_state_day_lane"),)
