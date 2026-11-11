"""The ``forge_pipeline_alpha`` table, declared on alpha-harness's own ``Base``.

It lives here rather than in ``db/models.py`` so an upstream merge never touches it. The board
router imports this module, and ``main.py`` imports that router before the database opens, so
:func:`alpha_harness.db.sqlite.migrate` creates the table on startup.
"""

from datetime import datetime  # noqa: TC003 - SQLAlchemy reads Mapped[datetime] at runtime
from typing import Any

from sqlalchemy import JSON, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...db.models import Base, UtcDateTime, utcnow


class PipelineAlpha(Base):
    """One alpha a pipeline produced, with the facts the board groups and repairs by.

    The engine's own ``SimulationRecord`` only knows the lane that paid (``forge.<lane>``), and
    a lane serves several alpha types, so the type has to be written down when the pipeline
    still knows it. ``settings`` and ``field`` are kept so a repair can be rebuilt from this row
    alone, without re-running the pipeline that made it.
    """

    __tablename__ = "forge_pipeline_alpha"

    id: Mapped[int] = mapped_column(primary_key=True)
    alpha_id: Mapped[str] = mapped_column(String(32), unique=True)
    pipeline: Mapped[str] = mapped_column(String(80))
    alpha_type: Mapped[str] = mapped_column(String(32))
    region: Mapped[str] = mapped_column(String(16), default="")
    expression: Mapped[str] = mapped_column(Text)
    #: BRAIN settings as simulated, camelCase.
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    field: Mapped[str] = mapped_column(String(120), default="")
    template_id: Mapped[str] = mapped_column(String(120), default="")
    #: ``r1``, ``r2``, ``repair``, ... as the Researcher staged it.
    stage: Mapped[str] = mapped_column(String(32), default="")
    #: BRAIN's simulation type (``REGULAR``, ``SUPER``, ...), for an alpha staged from outside a
    #: pipeline (Results Breakdown), where ``alpha_type`` names no Forge alpha type to look it up.
    sim_type: Mapped[str | None] = mapped_column(String(24), default=None)
    train_sharpe: Mapped[float | None] = mapped_column(Float)
    test_sharpe: Mapped[float | None] = mapped_column(Float)
    train_fitness: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)
    #: The checks the simulation itself reported; BRAIN's Check Submission, once run, wins.
    sim_checks: Mapped[list[Any]] = mapped_column(JSON, default=list)
    #: The run's task id, so a row can be traced to the run that made it.
    run_id: Mapped[str] = mapped_column(String(64), default="")
    repairs_queued: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    __table_args__ = (Index("ix_pipeline_alpha_created", "created_at"),)
