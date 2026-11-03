"""Dataset crowding as a gate on the field search a nugget is built from, not just a number on
a page: "low crowding only" narrows the results, and adding a field from an already
heavily-used dataset gets a warning — never a silent block, since a crowded field can still be
the right one (CLAUDE.md: crowding informs, BRAIN's own checks decide what submits).

Scores with the same :func:`..data.crowding.crowding_scores` the Datasets tab uses, so
"crowded" means one thing everywhere it is shown — this just ranks a field's own page instead
of the tab's dataset list.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...catalog.queries import CatalogQueries, FieldFilter, Tuple4
from .. import config as forge_config
from ..data.crowding import crowding_scores

if TYPE_CHECKING:
    from ...db.duck import Catalog


def default_crowding_threshold() -> float:
    """The "low crowding only" filter's default cutoff, from ``learning.yaml``."""
    return float((forge_config.learning().get("nuggets") or {}).get("crowding_threshold", 0.7))


def heavy_dataset_crowding() -> float:
    """Above this, a dataset counts as "heavily used" for the add-a-field warning. Not the
    same knob as a caller's own "low crowding only" threshold, which is theirs to choose."""
    return float((forge_config.learning().get("nuggets") or {}).get("heavy_dataset_crowding", 0.75))


@dataclass(frozen=True, slots=True)
class FieldSearchResult:
    field_id: str
    dataset_id: str | None
    description: str | None
    field_type: str
    crowding: float | None
    #: Set when ``dataset_crowding`` is above :data:`HEAVY_DATASET_CROWDING` — advisory only.
    dataset_warning: str | None


async def _dataset_crowding(catalog: Catalog, scope: Tuple4) -> dict[str, float | None]:
    queries = CatalogQueries(catalog)
    rows = await queries.datasets(scope)
    return crowding_scores(rows, key="dataset_id")


async def search_fields_for_nugget(
    catalog: Catalog,
    scope: Tuple4,
    filters: FieldFilter,
    *,
    low_crowding_only: bool = False,
    crowding_threshold: float | None = None,
) -> list[FieldSearchResult]:
    """A page of fields, each carrying its crowding and a dataset-crowding warning.

    ``low_crowding_only`` drops fields whose own crowding is above ``crowding_threshold``
    (unknown crowding is never dropped — "unknown" is not "uncrowded", but it is not "too
    crowded" either). The dataset warning is separate and always computed: a field can be
    individually un-mined in a dataset that, as a whole, is heavily picked over.
    """
    threshold = (
        crowding_threshold if crowding_threshold is not None else default_crowding_threshold()
    )
    heavy = heavy_dataset_crowding()
    queries = CatalogQueries(catalog)
    page = await queries.fields(scope, filters, counted=False)
    rows = page["results"]
    field_rows = await catalog.query(
        f"SELECT field_id, alpha_count, user_count FROM data_field WHERE {Tuple4.WHERE}",  # noqa: S608
        list(scope.params),
    )
    field_crowding = crowding_scores(field_rows, key="field_id")
    dataset_crowding = await _dataset_crowding(catalog, scope)

    out: list[FieldSearchResult] = []
    for r in rows:
        crowding = field_crowding.get(r["field_id"])
        if low_crowding_only and crowding is not None and crowding > threshold:
            continue
        ds_crowding = dataset_crowding.get(r.get("dataset_id") or "")
        warning = (
            f"Dataset {r['dataset_id']!r} is heavily used on this account "
            f"(crowding {ds_crowding:.2f}) — a new alpha here is more likely to fail "
            "self-correlation."
            if ds_crowding is not None and ds_crowding > heavy
            else None
        )
        out.append(
            FieldSearchResult(
                field_id=r["field_id"],
                dataset_id=r.get("dataset_id"),
                description=r.get("description"),
                field_type=r.get("field_type") or "MATRIX",
                crowding=crowding,
                dataset_warning=warning,
            )
        )
    return out
