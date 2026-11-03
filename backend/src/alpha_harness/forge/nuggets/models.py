"""The Data Nugget tables, declared on alpha-harness's own ``Base`` — same reasoning as
``forge/studio/models.py``: new tables live outside ``db/models.py`` so an upstream merge
never touches them, and ``main.py`` importing this module's router before the database opens
is what makes :func:`alpha_harness.db.sqlite.migrate` create them on startup.

A nugget's fields, rationale, data-treatment and claim are stored as the user wrote them, plus
an append-only version history (the Template Studio's ``UserTemplate``/``TemplateVersion``
split, copied here for the same reason: what produced an alpha has to stay traceable).
"""

from datetime import datetime  # noqa: TC003 - SQLAlchemy reads Mapped[datetime] at runtime
from typing import Any

from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db.models import Base, UtcDateTime, utcnow


class Nugget(Base):
    """A data nugget of your own: its fields, rationale and claim, as they stand now."""

    __tablename__ = "forge_nugget"

    id: Mapped[int] = mapped_column(primary_key=True)
    nugget_id: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    instrument_type: Mapped[str] = mapped_column(String(16), default="EQUITY")
    region: Mapped[str] = mapped_column(String(16))
    delay: Mapped[int] = mapped_column(Integer)
    universe: Mapped[str] = mapped_column(String(32))
    #: Field ids, in the order the user picked them.
    field_ids: Mapped[list[Any]] = mapped_column(default=list)
    #: field_id -> data-treatment expression fragment, e.g. ``ts_backfill(field, 63)``.
    #: A field with no entry here gets the default treatment at render time.
    data_treatment: Mapped[dict[str, Any]] = mapped_column(default=dict)
    #: What each field measures and why combining them means something.
    rationale: Mapped[str] = mapped_column(Text, default="")
    #: "This predicts returns because X; it's wrong if Y."
    claim: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[Any]] = mapped_column(default=list)
    #: Bumped on every saved edit; the matching row in ``forge_nugget_version`` holds it.
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)

    @property
    def scope_label(self) -> str:
        return f"{self.instrument_type}/{self.region}/D{self.delay}/{self.universe}"


class NuggetVersion(Base):
    """Every version of a nugget, oldest kept — the claim and rationale a past alpha was built
    on has to stay readable even after the nugget is refined further."""

    __tablename__ = "forge_nugget_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    nugget_id: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer)
    #: The whole row, as JSON, the way ``my-templates`` keeps the YAML text: one snapshot
    #: that export and the version list both read back verbatim.
    snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("nugget_id", "version", name="uq_nugget_version"),)
