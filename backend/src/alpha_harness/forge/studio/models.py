"""The Template Studio's tables, declared on alpha-harness's own ``Base``.

They live here rather than in ``db/models.py`` so an upstream merge never touches them; the
Studio's router imports this module, and ``main.py`` imports that router before the database
opens, so :func:`alpha_harness.db.sqlite.migrate` creates them on startup.

A template the user writes is stored as the YAML text they wrote, not as parsed fields. The
text is the thing they edit, export and read back; parsing it is cheap and the parser is the
one authority on what it means, so storing a parsed copy would only let the two disagree.
"""

from datetime import datetime  # noqa: TC003 - SQLAlchemy reads Mapped[datetime] at runtime
from typing import Any

from sqlalchemy import Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db.models import Base, UtcDateTime, utcnow


class UserTemplate(Base):
    """A template of your own: the current YAML, and where it came from.

    ``template_id`` is the id inside the YAML, unique across the library: a user template with
    a built-in's id would make ``$X`` mean two different things depending on who was asked.
    """

    __tablename__ = "forge_user_template"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    yaml_text: Mapped[str] = mapped_column(Text)
    #: Bumped on every saved edit; the matching row in ``forge_template_version`` holds it.
    version: Mapped[int] = mapped_column(Integer, default=1)
    #: The built-in this was cloned from, if any. Built-ins stay read-only.
    cloned_from: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class TemplateVersion(Base):
    """Every version of a user template, oldest kept.

    A template is an idea being refined; the version that produced last week's alphas has to
    stay readable, or a result can no longer be traced to what was actually run.
    """

    __tablename__ = "forge_template_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer)
    yaml_text: Mapped[str] = mapped_column(Text)
    #: What changed, in the user's words.
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_template_version"),)


class TemplateStats(Base):
    """What a template has actually produced, cut by where it was used.

    One row per template x dataset category x alpha type x scope: the same template is a
    different proposition on analyst data in USA than on news data in GLB, and averaging those
    together is how a library stops telling you anything.

    Filled by the pipelines in Phase 6; Phase 5 only reads it, so an empty table is the normal
    state until then.
    """

    __tablename__ = "forge_template_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[str] = mapped_column(String(80), index=True)
    dataset_category: Mapped[str] = mapped_column(String(64), default="")
    alpha_type: Mapped[str] = mapped_column(String(32), default="")
    #: ``EQUITY/USA/D1/TOP3000``.
    scope: Mapped[str] = mapped_column(String(64), default="")

    sims: Mapped[int] = mapped_column(Integer, default=0)
    #: Alphas BRAIN has decided on, and those it passed — the pass rate's two halves.
    checked: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    families: Mapped[int] = mapped_column(Integer, default=0)
    #: Train Sharpes, newest last. A median cannot be recovered from a running mean, and the
    #: median is what survives one lucky alpha.
    train_sharpes: Mapped[list[Any]] = mapped_column(default=list)
    median_train_sharpe: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "template_id",
            "dataset_category",
            "alpha_type",
            "scope",
            name="uq_template_stats_cut",
        ),
        Index("ix_template_stats_scope", "scope"),
    )


class AlphaDescription(Base):
    """A Power Pool description, kept with the expression it describes.

    Keyed by the expression rather than an alpha id: the description is written in the Studio,
    before anything is simulated and long before BRAIN has an alpha id to hang it on. Phase 6
    looks it up by expression when it submits the simulation, and nothing here ever submits
    anything (CLAUDE.md rule 1).
    """

    __tablename__ = "forge_alpha_description"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: sha256 of the expression, so the unique index does not carry a 4 kB string.
    expression_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expression: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(64), default="")
    template_id: Mapped[str | None] = mapped_column(String(80))
    #: BRAIN's three parts, kept apart so each can be checked against its own length.
    idea: Mapped[str] = mapped_column(Text, default="")
    data_rationale: Mapped[str] = mapped_column(Text, default="")
    operator_rationale: Mapped[str] = mapped_column(Text, default="")
    #: True once the user has edited it; a draft nobody has read is not a description.
    edited: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)
