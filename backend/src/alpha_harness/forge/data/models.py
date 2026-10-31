"""The ``forge_field_set`` table, declared on alpha-harness's own ``Base``.

It lives here rather than in ``db/models.py`` so an upstream merge never touches it. The field
sets router imports this module, and ``main.py`` imports the router before the database opens,
so :func:`alpha_harness.db.sqlite.migrate` creates the table on startup.
"""

from datetime import datetime  # noqa: TC003 - SQLAlchemy reads Mapped[datetime] at runtime
from typing import Any

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...db.models import Base, UtcDateTime, utcnow

MANUAL = "manual"
DYNAMIC = "dynamic"


class FieldSet(Base):
    """A named selection of data fields, always tied to the scope it was picked in.

    A field id means different data in different markets (``revenue`` sits in different
    datasets in USA and elsewhere) and may not exist in a market at all, so a set without its
    scope would be ambiguous. Two kinds:

    - ``manual``: the ``field_ids`` picked one by one;
    - ``dynamic``: a saved Data Explorer filter, re-run on every read, so the set picks up
      fields BRAIN adds later.
    """

    __tablename__ = "forge_field_set"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    instrument_type: Mapped[str] = mapped_column(String(16))
    region: Mapped[str] = mapped_column(String(16))
    delay: Mapped[int] = mapped_column(Integer)
    universe: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16), default=MANUAL)
    field_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    #: A ``FieldFilter`` without paging; ``None`` for a manual set.
    filter: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)
