"""The Workshop's own table, declared on alpha-harness's own ``Base``.

Lives here rather than in ``db/models.py`` so an upstream merge never touches it, same
reasoning as ``forge/board/models.py`` and ``forge/agent/models.py``. ``api/forge_workshop.py``
imports this module and ``main.py`` imports that router before the database opens, so
:func:`alpha_harness.db.sqlite.migrate` creates the table on startup.
"""

from datetime import datetime  # noqa: TC003 - SQLAlchemy reads Mapped[datetime] at runtime
from typing import Any

from sqlalchemy import JSON, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...db.models import Base, UtcDateTime, utcnow


class WorkshopTrial(Base):
    """One Hypothesis Court trial: a snapshot of the hypothesis it tested, the fields, the
    pipeline/field set it will promote into if it survives, and the ``Case`` the Court returned.

    ``hypothesis`` is a snapshot, not a live join to :class:`~..agent.models.AgentHypothesis`:
    a trial may be run on an idea typed straight into the Workshop that was never saved as a
    hypothesis row, and a saved hypothesis may change after the trial that tested it ran.
    """

    __tablename__ = "forge_workshop_trial"

    id: Mapped[int] = mapped_column(primary_key=True)
    hypothesis_id: Mapped[int | None] = mapped_column(Integer, default=None)
    hypothesis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    pipeline: Mapped[str] = mapped_column(String(80), default="")
    field_set_id: Mapped[int] = mapped_column(Integer, default=0)
    fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: Always ``validate`` today (CLAUDE.md rule 3: lanes.yaml says the Court spends from it).
    lane: Mapped[str] = mapped_column(String(32), default="validate")
    #: ``running`` | ``done`` | ``failed``.
    status: Mapped[str] = mapped_column(String(16), default="running")
    #: ``court.Case.to_dict()`` once the trial finishes.
    case: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    #: The overall verdict (``SURVIVES`` / ``WOUNDED`` / ``REJECTED``), mirrored out of
    #: ``case`` so the Workshop list can be queried/sorted without unpacking it.
    verdict: Mapped[str] = mapped_column(String(16), default="")
    spent: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    #: The :class:`~..agent.models.AgentProposal` made from this trial's survivors, if any.
    proposal_id: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)

    __table_args__ = (Index("ix_workshop_trial_created", "created_at"),)
