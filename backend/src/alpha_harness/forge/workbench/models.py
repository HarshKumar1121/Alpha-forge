"""The Workbench's own tables, declared on alpha-harness's own ``Base`` — same reasoning as
``forge/nuggets/models.py``: a new table lives outside ``db/models.py`` so an upstream merge
never touches it, and ``main.py`` importing this module's router before the database opens is
what makes :func:`alpha_harness.db.sqlite.migrate` create it on startup.
"""

from datetime import datetime  # noqa: TC003 - SQLAlchemy reads Mapped[datetime] at runtime
from typing import Any

from sqlalchemy import Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db.models import Base, UtcDateTime, utcnow


class WorkbenchTray(Base):
    """One scope's field tray: the fields a user is working with there right now, kept across
    visits to the Workbench so switching screens and coming back does not lose the selection."""

    __tablename__ = "forge_workbench_tray"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_type: Mapped[str] = mapped_column(String(16), default="EQUITY")
    region: Mapped[str] = mapped_column(String(16))
    delay: Mapped[int] = mapped_column(Integer)
    universe: Mapped[str] = mapped_column(String(32))
    #: Field ids, in the order they were added to the tray.
    field_ids: Mapped[list[Any]] = mapped_column(default=list)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "instrument_type", "region", "delay", "universe", name="uq_workbench_tray_scope"
        ),
    )


class WorkbenchRunItem(Base):
    """One candidate expression sent from the Workbench, and what came back.

    Durable past the in-memory ``TaskRegistry``'s 60s linger, so the results table on the
    Workbench screen survives a reload the same way the Pipelines run-history panel does
    (``forge/pipelines/store.py``). The ``task_id`` is the id the request was enqueued under
    (``forge.<lane>``), the same one Tasks and the Simulation Matrix already show it under.
    """

    __tablename__ = "forge_workbench_run_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    expression: Mapped[str] = mapped_column(Text)
    #: The template this came from, or ``None`` for a hand-written expression.
    template_id: Mapped[str | None] = mapped_column(String(80), default=None)
    field_ids: Mapped[list[Any]] = mapped_column(default=list)
    instrument_type: Mapped[str] = mapped_column(String(16), default="EQUITY")
    region: Mapped[str] = mapped_column(String(16))
    delay: Mapped[int] = mapped_column(Integer)
    universe: Mapped[str] = mapped_column(String(32))
    #: queued | done | failed
    status: Mapped[str] = mapped_column(String(16), default="queued")
    alpha_id: Mapped[str | None] = mapped_column(String(32), default=None)
    train_sharpe: Mapped[float | None] = mapped_column(Float, default=None)
    test_sharpe: Mapped[float | None] = mapped_column(Float, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)
