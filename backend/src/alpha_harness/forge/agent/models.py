"""The agent's memory tables, declared on alpha-harness's own ``Base``.

They live here rather than in ``db/models.py`` so an upstream merge never touches them, same
reasoning as ``forge/board/models.py`` and ``forge/pipelines/models.py``. ``api/forge_agent.py``
imports this module (via ``forge/agent/store.py``) and ``main.py`` imports that router before
the database opens, so :func:`alpha_harness.db.sqlite.migrate` creates the tables on startup.
"""

from datetime import datetime  # noqa: TC003 - SQLAlchemy reads Mapped[datetime] at runtime
from typing import Any

from sqlalchemy import JSON, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...db.models import Base, UtcDateTime, utcnow


class AgentHypothesis(Base):
    """A hypothesis the agent or the user wrote down before anything was simulated.

    Mirrors alpha-copilot's ``hypotheses`` table (reference/alpha-copilot/src/alpha_copilot/
    store.py): a mechanism is recorded before the first simulation so the research stays
    honest about what it was testing, not fitted after the fact.
    """

    __tablename__ = "forge_agent_hypothesis"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    mechanism: Mapped[str] = mapped_column(Text, default="")
    expected_sign: Mapped[str] = mapped_column(String(40), default="")
    horizon: Mapped[str] = mapped_column(String(80), default="")
    #: Which alpha-type pipeline this hypothesis targets, e.g. ``forge_regular``.
    pipeline: Mapped[str] = mapped_column(String(80), default="")
    #: A Fast Expression template with ``$placeholders``, and the fields proposed for them.
    template: Mapped[str] = mapped_column(Text, default="")
    #: The real template this idea was picked for, if the picker named one — see
    #: ``forge.research.templates.get``. ``""`` for older hypotheses that only have free text.
    template_id: Mapped[str] = mapped_column(String(80), default="", server_default="")
    fields: Mapped[list[Any]] = mapped_column(JSON, default=list)
    #: Who wrote it: ``user`` or one of the agent roles (``researcher``, ``critic``, ...).
    source: Mapped[str] = mapped_column(String(32), default="user")
    #: ``open`` | ``proposed`` | ``closed``.
    status: Mapped[str] = mapped_column(String(16), default="open")
    verdict: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    __table_args__ = (Index("ix_agent_hypothesis_created", "created_at"),)


class AgentLesson(Base):
    """A reusable research rule, written in the user's or the agent's own words.

    ``source`` tells a reader where a lesson came from without reading its text: ``user``
    (typed by hand), an agent role (``researcher``, ``critic``, ``tutor``, ...), or
    ``weekly_job`` for the summary :func:`forge.agent.weekly.run_weekly_summary` writes
    automatically.
    """

    __tablename__ = "forge_agent_lesson"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="user")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    __table_args__ = (Index("ix_agent_lesson_created", "created_at"),)


class AgentProposal(Base):
    """Something the agent proposed that would cost quota, waiting on a human's Approve click.

    Writing a proposal never spends anything — it is a row the agent's ``propose_pipeline_run``
    tool can create freely. Only :func:`forge.agent.execution.execute_proposal`, called from a
    plain API route the agent's tool schema never lists, turns an ``approved`` row into a real
    run, and it always does so in the ``reserve`` lane regardless of what was proposed
    (CLAUDE.md rule 5).
    """

    __tablename__ = "forge_agent_proposal"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Today, always ``pipeline_run``. A dataclass, not an enum, so a new kind of proposal is a
    #: new reader of ``payload``, not a schema migration.
    kind: Mapped[str] = mapped_column(String(32), default="pipeline_run")
    #: ``{"pipeline": ..., "field_set_id": ..., "template_ids": [...], "budget": ...}``.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: Why the agent thinks this is worth the quota, shown on the approval card.
    rationale: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="researcher")
    #: ``pending`` | ``approved`` | ``rejected`` | ``executed`` | ``failed``.
    status: Mapped[str] = mapped_column(String(16), default="pending")
    #: The pipeline run's id, once ``execute_proposal`` has run it.
    run_id: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)

    __table_args__ = (Index("ix_agent_proposal_created", "created_at"),)


class AgentChatThread(Base):
    """One agent-panel conversation. The panel opens on every screen, so a thread's title is
    whatever the first message was about, not the screen it started on — the same conversation
    is expected to follow the researcher from screen to screen."""

    __tablename__ = "forge_agent_chat_thread"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120), default="")
    #: Which system prompt this thread talks to: ``researcher`` | ``critic`` | ``analyst`` |
    #: ``tutor`` (``forge/agent/prompts.py``).
    role: Mapped[str] = mapped_column(String(16), default="researcher")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_agent_chat_thread_updated", "updated_at"),)


class AgentChatMessage(Base):
    """One turn. ``meta`` holds what made the turn reproducible: the screen context the panel
    sent with a ``user`` message, and the tool calls + usage an ``assistant`` reply made."""

    __tablename__ = "forge_agent_chat_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    text: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    __table_args__ = (Index("ix_agent_chat_message_thread", "thread_id"),)
