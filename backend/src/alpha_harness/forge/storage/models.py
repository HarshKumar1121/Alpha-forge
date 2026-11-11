"""Storage-housekeeping tables, declared on alpha-harness's own ``Base``.

They live here rather than in ``db/models.py`` so an upstream merge never touches them, same
reasoning as ``forge/agent/models.py``. Reached from ``main.py``'s router imports (``api/vault.py``,
``api/lab_tasks.py``, ``api/forge_nuggets.py``, all via ``forge/storage/access.py``) before the
database opens, so :func:`alpha_harness.db.sqlite.migrate` creates the tables on startup.
"""

from datetime import datetime  # noqa: TC003 - SQLAlchemy reads Mapped[datetime] at runtime
from typing import Any

from sqlalchemy import JSON, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ...db.models import Base, UtcDateTime, utcnow


class StorageAccess(Base):
    """The last time a human (or a correlation sweep) actually looked at an Alpha's daily PnL.

    ``alpha_pnl`` lives in DuckDB and is re-fetchable from BRAIN, so it is safe to expire — but
    only once it is both unsubmitted and genuinely idle. DuckDB has no per-row metadata of its
    own here, so the "idle" clock is kept in this small SQLite table instead, one row per Alpha
    ever read, updated by :func:`forge.storage.access.touch_pnl`.
    """

    __tablename__ = "forge_storage_access"

    alpha_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_accessed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)


class StorageCheckHistory(Base):
    """A compact, permanent record of one BRAIN submission-check verdict.

    Written just before a ``brain_cache`` ``check:`` entry is allowed to expire, so the
    PASS/FAIL/WARNING verdict survives even once the full BRAIN response is gone. ``value`` and
    ``limit_value`` are JSON because :class:`brain.schemas.Check` makes no promise about their
    shape (a number for most checks, a list of pool names for ``HT_INVESTABLE_...``).
    """

    __tablename__ = "forge_storage_check_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    alpha_id: Mapped[str] = mapped_column(String(64), index=True)
    check_name: Mapped[str] = mapped_column(String(80))
    result: Mapped[str | None] = mapped_column(String(16))
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    limit_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    __table_args__ = (Index("ix_storage_check_history_alpha", "alpha_id", "check_name"),)
