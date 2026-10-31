"""Reading a field set's fields out of the catalog.

A manual set is its ids, looked up in its own scope; ids the catalog no longer holds there are
reported as ``missing`` rather than silently dropped, because a template bound to the set would
otherwise lose a slot without saying why. A dynamic set is its saved Data Explorer filter, run
again now, so fields BRAIN added since are in it.
"""

from typing import TYPE_CHECKING, Any

from ...catalog.queries import FieldFilter
from .models import DYNAMIC, FieldSet
from .scope import Scope

if TYPE_CHECKING:
    from ...catalog.queries import CatalogQueries

#: The most fields one set holds or resolves to: plenty for a research idea, and a bound on
#: how many slot expansions a template can draw from it.
MAX_FIELDS = 500

#: Paging belongs to a screen, not to a saved selection.
PAGING = {"limit", "offset"}


def saved_filter(filters: FieldFilter) -> dict[str, Any]:
    """A filter as stored: validated, JSON-ready, without paging."""
    return filters.model_dump(mode="json", exclude=PAGING)


def scope_of(row: FieldSet) -> Scope:
    return Scope(
        instrument_type=row.instrument_type,
        region=row.region,
        delay=row.delay,
        universe=row.universe,
    )


async def resolve(row: FieldSet, queries: CatalogQueries) -> dict[str, Any]:
    """``{total, fields, missing}`` for a set, read from its own scope."""
    scope = scope_of(row).to_tuple4()
    if row.kind == DYNAMIC:
        filters = FieldFilter.model_validate({**(row.filter or {}), "limit": MAX_FIELDS})
        page = await queries.fields(scope, filters)
        return {"total": page["total"], "fields": page["results"], "missing": []}
    ids = [str(i) for i in row.field_ids]
    if not ids:
        return {"total": 0, "fields": [], "missing": []}
    page = await queries.fields(
        scope, FieldFilter(field_ids=ids, limit=MAX_FIELDS, sort_by="field_id", sort_desc=False)
    )
    found = {r["field_id"] for r in page["results"]}
    return {
        "total": page["total"],
        "fields": page["results"],
        "missing": [i for i in ids if i not in found],
    }
