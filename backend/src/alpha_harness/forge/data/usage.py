"""My own history per dataset and field: simulations, alphas, BRAIN check pass rate, families.

Nothing stores which dataset an alpha uses, so it is read off the expression: the data fields
it names (grouping fields excluded — BRAIN does not count them either), then each field's
dataset from the catalog. A field some market files under two datasets counts toward both.

History is matched at **instrumentType + region + delay**: the pyramid's granularity, and the
level at which "have I already worked this data?" is a useful question. The universe is kept
per row so the dataset page can break it down.

Pass rate is BRAIN's verdict, not a local guess (:func:`alpha_harness.vault.yields.verdict`):

- *checked*: BRAIN has decided — every gating check passed (``submittable``) or one failed
  (``refused``, which includes quick-mode alphas BRAIN will never take);
- *passed*: ``submittable``;
- pass rate = passed / checked. Alphas still ``PENDING`` or with no checks stay out of both,
  so an unchecked alpha never counts as a failure or a success.

"Families started" is structural for now: distinct field combinations among my alphas in the
dataset (:func:`alpha_harness.forge.research.families.signature`). Phase 7 replaces it with
PnL-correlation families.
"""

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from ...db.models import SimulationRecord
from ...labs.fastexpr import ParseError, data_fields, parse
from ...vault.yields import checks_of, verdict
from ..research.families import signature

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ...db.duck import Catalog
    from ...db.sqlite import Database


@dataclass(slots=True)
class Tally:
    """Counts for one dataset, field or universe."""

    sims: int = 0
    alphas: int = 0
    checked: int = 0
    passed: int = 0

    @property
    def pass_rate(self) -> float | None:
        return round(self.passed / self.checked, 4) if self.checked else None


@dataclass(slots=True)
class DatasetHistory(Tally):
    families: set[str] = field(default_factory=set)
    by_universe: dict[str, Tally] = field(default_factory=dict)

    def universe(self, name: str | None) -> Tally:
        return self.by_universe.setdefault(name or "?", Tally())


@dataclass(slots=True)
class Usage:
    datasets: dict[str, DatasetHistory] = field(default_factory=dict)
    fields: dict[str, Tally] = field(default_factory=dict)
    #: Alphas and sims whose expression this parser could not read; left out of every count.
    unparsed: int = 0
    alphas: int = 0
    sims: int = 0


def fields_of(expression: str | None) -> list[str] | None:
    """The data fields an expression reads, or ``None`` when it cannot be parsed."""
    if not expression:
        return None
    try:
        return data_fields(parse(expression), grouping=False)
    except ParseError:
        return None


def dataset_usage(
    alphas: Iterable[Mapping[str, Any]],
    sims: Iterable[Mapping[str, Any]],
    datasets_of: Mapping[str, set[str]],
) -> Usage:
    """Fold alpha rows (``expression, universe, checks, simulation_mode``) and simulation rows
    (``expression, universe``) into per-dataset and per-field history.

    ``datasets_of`` maps a field id to the datasets the catalog files it under in this market.
    """
    usage = Usage()

    def touched(fields: list[str]) -> set[str]:
        return {d for f in fields for d in datasets_of.get(f, ())}

    for row in sims:
        fields = fields_of(row.get("expression"))
        if fields is None:
            usage.unparsed += 1
            continue
        usage.sims += 1
        for f in fields:
            usage.fields.setdefault(f, Tally()).sims += 1
        for d in touched(fields):
            history = usage.datasets.setdefault(d, DatasetHistory())
            history.sims += 1
            history.universe(row.get("universe")).sims += 1

    for row in alphas:
        fields = fields_of(row.get("expression"))
        if fields is None:
            usage.unparsed += 1
            continue
        usage.alphas += 1
        decided = verdict(checks_of(row.get("checks")), row.get("simulation_mode"))
        checked = decided in ("submittable", "refused")
        passed = decided == "submittable"
        tallies = [usage.fields.setdefault(f, Tally()) for f in fields]
        for d in touched(fields):
            history = usage.datasets.setdefault(d, DatasetHistory())
            history.families.add(signature(fields, "expr"))
            tallies += [history, history.universe(row.get("universe"))]
        for t in tallies:
            t.alphas += 1
            t.checked += checked
            t.passed += passed
    return usage


# --- loading ---------------------------------------------------------------

#: Market -> (fingerprint, usage). In-process: a restart costs one recompute.
_cache: dict[tuple[str, str, int], tuple[tuple[Any, ...], Usage]] = {}
#: How long a fingerprint is trusted before it is read again.
FINGERPRINT_TTL = 30.0
_checked_at: dict[tuple[str, str, int], float] = {}


async def _fingerprint(
    catalog: Catalog, db: Database, market: tuple[str, str, int]
) -> tuple[Any, ...]:
    """What changes when the history would: alphas fetched, sims recorded, catalog synced."""
    alpha = await catalog.query(
        "SELECT count(*) AS n, max(fetched_at) AS t FROM alpha "
        "WHERE coalesce(instrument_type, 'EQUITY') = ? AND region = ? AND delay = ?",
        list(market),
    )
    synced = await catalog.scalar(
        "SELECT max(synced_at) FROM data_field "
        "WHERE instrument_type = ? AND region = ? AND delay = ?",
        list(market),
    )
    async with db.session() as s:
        sims = (
            await s.execute(
                select(func.count(), func.max(SimulationRecord.id)).where(*_sim_filter(market))
            )
        ).one()
    return (alpha[0]["n"], alpha[0]["t"], synced, sims[0], sims[1])


def _sim_filter(market: tuple[str, str, int]) -> list[Any]:
    """Simulations that reached BRAIN in this market: a platform id, and not a batch parent."""
    instrument_type, region, delay = market
    return [
        SimulationRecord.instrument_type == instrument_type,
        SimulationRecord.region == region,
        SimulationRecord.delay == delay,
        SimulationRecord.platform_id.is_not(None),
        SimulationRecord.is_batch.is_(False),
        SimulationRecord.expression.is_not(None),
    ]


async def load_usage(
    catalog: Catalog, db: Database, instrument_type: str, region: str, delay: int
) -> Usage:
    """History for one market, recomputed only when its fingerprint moves."""
    market = (instrument_type, region, delay)
    hit = _cache.get(market)
    if hit is not None and time.monotonic() - _checked_at.get(market, 0) < FINGERPRINT_TTL:
        return hit[1]
    fingerprint = await _fingerprint(catalog, db, market)
    _checked_at[market] = time.monotonic()
    if hit is not None and hit[0] == fingerprint:
        return hit[1]

    mapping = await catalog.query(
        "SELECT DISTINCT field_id, dataset_id FROM data_field "
        "WHERE instrument_type = ? AND region = ? AND delay = ? AND dataset_id IS NOT NULL",
        list(market),
    )
    datasets_of: dict[str, set[str]] = {}
    for r in mapping:
        datasets_of.setdefault(str(r["field_id"]), set()).add(str(r["dataset_id"]))
    alphas = await catalog.query(
        "SELECT expression, universe, checks, simulation_mode FROM alpha "
        "WHERE coalesce(instrument_type, 'EQUITY') = ? AND region = ? AND delay = ?",
        list(market),
    )
    async with db.session() as s:
        sims = [
            {"expression": e, "universe": u}
            for e, u in (
                await s.execute(
                    select(SimulationRecord.expression, SimulationRecord.universe).where(
                        *_sim_filter(market)
                    )
                )
            ).all()
        ]
    usage = dataset_usage(alphas, sims, datasets_of)
    _cache[market] = (fingerprint, usage)
    return usage
