"""``/api/forge/scopes`` and ``/api/forge/data/...``: real scopes, crowding and my history.

Read-only. The scopes come from the account's cached ``OPTIONS /simulations`` schema; datasets
and fields from the synced catalog; the account's pyramid multipliers from BRAIN's activity
endpoints (cached for hours, and optional: without a session the page still works without them).
"""

import logging
import math
from typing import Any

from fastapi import APIRouter
from pydantic import Field

from ..brain.errors import BrainError
from ..brain.platform import PYRAMID_MIN_ALPHAS
from ..catalog.pyramids import pyramid_grid
from ..catalog.queries import Tuple4
from ..forge.data.crowding import crowding_scores
from ..forge.data.scope import scope_tree
from ..forge.data.usage import DatasetHistory, Usage, load_usage
from ..schemas import Out
from .catalog import Scope
from .deps import State, refuse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/forge", tags=["forge-data"])

#: Coverage histogram: ten bins of 10 percentage points.
COVERAGE_BINS = 10


# --- wire shapes --------------------------------------------------------------


class Choice(Out):
    value: str
    label: str


class UniverseNode(Out):
    value: str
    label: str
    #: The local catalog holds this market's fields.
    synced: bool
    fields: int
    neutralizations: list[Choice]


class DelayNode(Out):
    value: int
    label: str
    universes: list[UniverseNode]


class RegionNode(Out):
    value: str
    label: str
    #: Region ``ALL``: one alpha run across every region at once.
    region_agnostic: bool
    delays: list[DelayNode]


class InstrumentNode(Out):
    value: str
    label: str
    regions: list[RegionNode]


class ScopeTree(Out):
    instrument_types: list[InstrumentNode]


class MyHistory(Out):
    #: Simulations sent to BRAIN from here whose expression reads this data.
    sims: int
    #: Alphas in the vault (from here or the website) reading this data.
    alphas: int
    #: Alphas BRAIN has decided: every check passed, or one failed.
    checked: int
    #: Alphas whose every gating check passed.
    passed: int
    #: ``passed / checked``; ``None`` until BRAIN has decided one.
    pass_rate: float | None


class ForgeDataset(Out):
    dataset_id: str
    name: str | None
    description: str | None
    category_id: str | None
    category_name: str | None
    subcategory_name: str | None
    coverage: float | None
    value_score: float | None
    user_count: int | None
    alpha_count: int | None
    field_count: int | None
    #: What BRAIN lists on the dataset itself.
    pyramid_multiplier: float | None
    #: The account's multiplier for this category in this region/delay (``None`` offline).
    account_multiplier: float | None
    #: The pyramid has the 3 submitted alphas this quarter that BRAIN counts.
    pyramid_lit: bool | None
    #: 0-1, see ``forge/data/crowding.py``. Ranked within this scope.
    crowding: float | None
    mine: MyHistory


class ForgeDatasets(Out):
    datasets: list[ForgeDataset]
    #: Expressions the parser could not read, left out of every "my" count.
    unparsed: int
    #: Whether the account pyramid multipliers could be read from BRAIN.
    pyramids_live: bool


class FieldExtrasRequest(Out):
    field_ids: list[str] = Field(max_length=500)


class FieldExtra(Out):
    crowding: float | None
    mine: MyHistory


class FieldExtras(Out):
    fields: dict[str, FieldExtra]


class CoverageBin(Out):
    #: Lower edge, inclusive, as a share (0.3 = 30%).
    low: float
    high: float
    fields: int


class ScatterPoint(Out):
    dataset_id: str
    name: str | None
    category_id: str | None
    crowding: float | None
    multiplier: float | None
    field_count: int | None
    #: This page's dataset.
    current: bool


class UniverseHistory(MyHistory):
    universe: str


class DatasetHistoryOut(MyHistory):
    #: Distinct field combinations used here: structural families until Phase 7's PnL ones.
    structural_families: int
    by_universe: list[UniverseHistory]


class PyramidStatus(Out):
    category_id: str | None
    multiplier: float | None
    #: My submitted alphas in this pyramid this quarter.
    alpha_count: int | None
    needed: int
    lit: bool | None


class FieldTypeCount(Out):
    field_type: str
    fields: int


class DatasetPage(Out):
    scope: str
    dataset: ForgeDataset
    coverage_bins: list[CoverageBin]
    field_types: list[FieldTypeCount]
    scatter: list[ScatterPoint]
    history: DatasetHistoryOut
    pyramid: PyramidStatus


# --- helpers ------------------------------------------------------------------


def _mine(t: Any | None) -> MyHistory:
    if t is None:
        return MyHistory(sims=0, alphas=0, checked=0, passed=0, pass_rate=None)
    return MyHistory(
        sims=t.sims, alphas=t.alphas, checked=t.checked, passed=t.passed, pass_rate=t.pass_rate
    )


async def _schema(state: State) -> dict[str, Any]:
    schema = await state.metadata.cached_settings_schema()
    if schema is None:
        raise refuse(
            503,
            "no_schema",
            "Sign in to BRAIN first: Forge reads which markets your account can run from it.",
        )
    return schema


async def _pyramids(state: State) -> dict[tuple[str, str, int], dict[str, Any]] | None:
    """The account's pyramid cells by (category, region, delay), or ``None`` when BRAIN can't
    be asked (signed out, rate limited). Cached for hours inside ``pyramid_grid``."""
    try:
        grid = await pyramid_grid(state.endpoints, state.catalog)
    except BrainError as e:
        log.info("pyramid multipliers unavailable: %s", type(e).__name__)
        return None
    return {(c["categoryId"], c["region"], int(c["delay"])): c for c in grid["cells"]}


def _datasets(
    rows: list[dict[str, Any]],
    scope: Tuple4,
    usage: Usage,
    pyramids: dict[tuple[str, str, int], dict[str, Any]] | None,
) -> list[ForgeDataset]:
    crowding = crowding_scores(rows, key="dataset_id")
    out: list[ForgeDataset] = []
    for r in rows:
        cell = (
            pyramids.get((r.get("category_id") or "", scope.region, scope.delay))
            if pyramids is not None
            else None
        )
        out.append(
            ForgeDataset(
                dataset_id=r["dataset_id"],
                name=r.get("name"),
                description=r.get("description"),
                category_id=r.get("category_id"),
                category_name=r.get("category_name"),
                subcategory_name=r.get("subcategory_name"),
                coverage=r.get("coverage"),
                value_score=r.get("value_score"),
                user_count=r.get("user_count"),
                alpha_count=r.get("alpha_count"),
                field_count=r.get("field_count"),
                pyramid_multiplier=r.get("pyramid_multiplier"),
                account_multiplier=cell.get("multiplier") if cell else None,
                pyramid_lit=cell.get("lit") if cell else None,
                crowding=crowding.get(r["dataset_id"]),
                mine=_mine(usage.datasets.get(r["dataset_id"])),
            )
        )
    return out


#: Scope label -> (synced_at, crowding per field).
_field_crowding: dict[str, tuple[Any, dict[str, float | None]]] = {}


async def _fields_crowding(state: State, scope: Tuple4) -> dict[str, float | None]:
    """Crowding for every field in the scope, recomputed only after a re-sync."""
    synced = await state.catalog.scalar(
        f"SELECT max(synced_at) FROM data_field WHERE {Tuple4.WHERE}",  # noqa: S608
        list(scope.params),
    )
    hit = _field_crowding.get(scope.label)
    if hit is not None and hit[0] == synced:
        return hit[1]
    rows = await state.catalog.query(
        f"SELECT field_id, alpha_count, user_count FROM data_field WHERE {Tuple4.WHERE}",  # noqa: S608
        list(scope.params),
    )
    scores = crowding_scores(rows, key="field_id")
    _field_crowding[scope.label] = (synced, scores)
    return scores


def coverage_bins(values: list[float | None]) -> list[CoverageBin]:
    """Fields per 10-point coverage band; 100% falls in the last band."""
    counts = [0] * COVERAGE_BINS
    for v in values:
        if v is None or math.isnan(v):
            continue
        # Rounded first so a stored 0.7 (0.69999...) lands in the 70% band, not the 60% one.
        band = math.floor(round(max(v, 0.0) * COVERAGE_BINS, 9))
        counts[min(band, COVERAGE_BINS - 1)] += 1
    return [
        CoverageBin(low=i / COVERAGE_BINS, high=(i + 1) / COVERAGE_BINS, fields=n)
        for i, n in enumerate(counts)
    ]


def _history(h: DatasetHistory | None) -> DatasetHistoryOut:
    mine = _mine(h)
    return DatasetHistoryOut(
        **mine.model_dump(),
        structural_families=len(h.families) if h else 0,
        by_universe=[
            UniverseHistory(universe=u, **_mine(t).model_dump())
            for u, t in sorted((h.by_universe if h else {}).items())
        ],
    )


# --- routes -------------------------------------------------------------------


@router.get("/scopes")
async def scopes(state: State) -> ScopeTree:
    """Every scope the account can run, from BRAIN's own settings schema, with sync state."""
    schema = await _schema(state)
    synced = {
        (r["instrument_type"], r["region"], int(r["delay"]), r["universe"]): int(r["fields"])
        for r in await state.queries.synced_tuples()
    }
    tree = scope_tree(schema)
    for it in tree:
        for region in it["regions"]:
            for delay in region["delays"]:
                for universe in delay["universes"]:
                    key = (it["value"], region["value"], delay["value"], universe["value"])
                    universe["fields"] = synced.get(key, 0)
                    universe["synced"] = key in synced
    return ScopeTree.model_validate({"instrumentTypes": tree})


@router.get("/data/datasets")
async def datasets(scope: Scope, state: State) -> ForgeDatasets:
    """Every dataset in the scope with crowding, the account's multiplier, and my history."""
    rows = await state.queries.datasets(scope)
    usage = await load_usage(
        state.catalog, state.db, scope.instrument_type, scope.region, scope.delay
    )
    pyramids = await _pyramids(state)
    return ForgeDatasets(
        datasets=_datasets(rows, scope, usage, pyramids),
        unparsed=usage.unparsed,
        pyramids_live=pyramids is not None,
    )


@router.post("/data/fields/extras")
async def field_extras(scope: Scope, state: State, body: FieldExtrasRequest) -> FieldExtras:
    """Crowding and my history for a page of Data Explorer fields.

    Crowding is ranked against every field in the scope, not just the page.
    """
    crowding = await _fields_crowding(state, scope)
    usage = await load_usage(
        state.catalog, state.db, scope.instrument_type, scope.region, scope.delay
    )
    return FieldExtras(
        fields={
            f: FieldExtra(crowding=crowding.get(f), mine=_mine(usage.fields.get(f)))
            for f in body.field_ids
        }
    )


@router.get("/data/datasets/{dataset_id}")
async def dataset_page(dataset_id: str, scope: Scope, state: State) -> DatasetPage:
    """One dataset: its coverage, where it sits on crowding vs pay, and my history there."""
    rows = await state.queries.datasets(scope)
    if not any(r["dataset_id"] == dataset_id for r in rows):
        raise refuse(
            404,
            "not_found",
            f"{dataset_id} is not in the catalog for {scope.label}. Sync this market, or pick "
            "a scope that carries it.",
        )
    usage = await load_usage(
        state.catalog, state.db, scope.instrument_type, scope.region, scope.delay
    )
    pyramids = await _pyramids(state)
    all_rows = _datasets(rows, scope, usage, pyramids)
    current = next(d for d in all_rows if d.dataset_id == dataset_id)

    fields = await state.catalog.query(
        f"SELECT coverage, field_type FROM data_field WHERE {Tuple4.WHERE} AND dataset_id = ?",  # noqa: S608
        [*scope.params, dataset_id],
    )
    types: dict[str, int] = {}
    for f in fields:
        types[f["field_type"] or "?"] = types.get(f["field_type"] or "?", 0) + 1

    cell = (
        pyramids.get((current.category_id or "", scope.region, scope.delay)) if pyramids else None
    )
    return DatasetPage(
        scope=scope.label,
        dataset=current,
        coverage_bins=coverage_bins([f["coverage"] for f in fields]),
        field_types=[FieldTypeCount(field_type=t, fields=n) for t, n in sorted(types.items())],
        scatter=[
            ScatterPoint(
                dataset_id=d.dataset_id,
                name=d.name,
                category_id=d.category_id,
                crowding=d.crowding,
                multiplier=d.account_multiplier
                if d.account_multiplier is not None
                else d.pyramid_multiplier,
                field_count=d.field_count,
                current=d.dataset_id == dataset_id,
            )
            for d in all_rows
        ],
        history=_history(usage.datasets.get(dataset_id)),
        pyramid=PyramidStatus(
            category_id=current.category_id,
            multiplier=cell.get("multiplier") if cell else current.pyramid_multiplier,
            alpha_count=cell.get("alphaCount") if cell else None,
            needed=PYRAMID_MIN_ALPHAS,
            lit=cell.get("lit") if cell else None,
        ),
    )
