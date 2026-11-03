"""The ``forge_pipeline_run`` table, declared on alpha-harness's own ``Base``.

It lives here rather than in ``db/models.py`` so an upstream merge never touches it.
``api/forge_pipelines.py`` imports this module (via ``forge/pipelines/store.py``), and
``main.py`` imports that router before the database opens, so
:func:`alpha_harness.db.sqlite.migrate` creates the table on startup — the same mechanism
``forge/board/models.py``'s ``PipelineAlpha`` already relies on.

Named ``PipelineRunRecord`` rather than ``PipelineRun``: the orchestrator already has a
``PipelineRun`` class for the compute object that generates/lints/simulates a round, and this
is its durable record instead.
"""

from datetime import datetime  # noqa: TC003 - SQLAlchemy reads Mapped[datetime] at runtime
from typing import Any

from sqlalchemy import JSON, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...db.models import Base, UtcDateTime, utcnow


class PipelineRunRecord(Base):
    """One pipeline run, from the moment it starts to the moment it finishes (or fails).

    Written as soon as ``POST /run`` starts a run and updated as it progresses, so
    ``GET /runs/{id}`` and the history panel both read a row that survives a restart — unlike
    the in-memory ``TaskRegistry`` entry, which is deliberately transient. ``report`` holds the
    full candidate/rejected/check detail once the run is done, not just the kept alphas
    (those are recorded separately, as ``PipelineAlpha`` rows, by ``board/store.py``).
    """

    __tablename__ = "forge_pipeline_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pipeline: Mapped[str] = mapped_column(String(80))
    field_set: Mapped[str] = mapped_column(String(120))
    lane: Mapped[str] = mapped_column(String(32))
    template_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    budget: Mapped[int] = mapped_column(Integer)
    #: ``running`` | ``done`` | ``failed``.
    status: Mapped[str] = mapped_column(String(16), default="running")
    detail: Mapped[str] = mapped_column(String(240), default="")
    error: Mapped[str | None] = mapped_column(Text, default=None)
    candidates_count: Mapped[int] = mapped_column(Integer, default=0)
    eligible_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    finalists_count: Mapped[int] = mapped_column(Integer, default=0)
    spent: Mapped[int] = mapped_column(Integer, default=0)
    #: The RunReport payload (candidates, finalists, rejected, checks, correlations), written
    #: once the run finishes.
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)

    __table_args__ = (Index("ix_pipeline_run_started", "started_at"),)
