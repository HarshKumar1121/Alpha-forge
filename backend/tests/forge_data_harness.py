"""An app wired to throwaway databases, for the Forge data and field-set routes.

SQLite and DuckDB are real (in ``tmp_path``); the settings schema is the hand-written
``OPTIONS /simulations`` fixture; BRAIN's pyramid endpoints answer from a mock transport.
Nothing can reach BRAIN, and nothing here can simulate.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx2
from conftest import json_response, make_endpoints, route
from forge_options_fixture import OPTIONS_SIMULATIONS

from alpha_harness.catalog import pyramids
from alpha_harness.catalog.queries import CatalogQueries
from alpha_harness.db.duck import Catalog
from alpha_harness.db.sqlite import Database
from alpha_harness.forge.data import usage
from alpha_harness.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

CLIENT = {"x-harness-client": "1"}

#: (field, dataset, category, type, coverage, users, alphas) in USA / D1 / TOP3000.
USA_FIELDS = [
    ("close", "pv1", "pv", "MATRIX", 1.0, 9000, 90000),
    ("volume", "pv1", "pv", "MATRIX", 0.98, 7000, 50000),
    ("anl4_eps_mean", "analyst4", "analyst", "MATRIX", 0.72, 300, 2000),
    ("anl4_eps_low", "analyst4", "analyst", "MATRIX", 0.65, 120, 600),
    ("anl4_rev_mean", "analyst4", "analyst", "MATRIX", 0.70, 200, 900),
    ("anl4_rev_low", "analyst4", "analyst", "VECTOR", 0.41, 40, 100),
    ("anl4_dps_mean", "analyst4", "analyst", "MATRIX", 0.33, 15, 30),
]
GLB_FIELDS = [("close", "pv1", "pv", "MATRIX", 0.99, 5000, 20000)]

DATASETS = [
    # (dataset, category, users, alphas, fields, multiplier) in USA / D1 / TOP3000.
    ("pv1", "pv", 20000, 400000, 2, 1.0),
    ("analyst4", "analyst", 900, 6000, 5, 1.6),
]


class Metadata:
    """``state.metadata`` without a database or a session: the fixture schema, always fresh."""

    def __init__(self, schema: dict[str, Any] | None) -> None:
        self.schema = schema

    async def cached_settings_schema(self) -> dict[str, Any] | None:
        return self.schema


async def schema() -> dict[str, Any]:
    endpoints, _ = make_endpoints(
        route({("OPTIONS", "/simulations"): json_response(OPTIONS_SIMULATIONS)})
    )
    return await endpoints.settings_schema()


def _pyramid_endpoints() -> Any:
    cell = {"category": {"id": "analyst", "name": "Analyst"}, "region": "USA", "delay": 1}
    endpoints, _ = make_endpoints(
        route(
            {
                ("GET", "/users/self/activities/pyramid-multipliers"): json_response(
                    {"pyramids": [{**cell, "multiplier": 1.8}]}
                ),
                ("GET", "/users/self/activities/pyramid-alphas"): json_response(
                    {"pyramids": [{**cell, "alphaCount": 1}]}
                ),
            }
        )
    )
    return endpoints


#: Grouping fields and the hard-coded fields templates name directly. Seeded only when a test
#: asks for them, because the Data Explorer tests count the fields in this market.
SCOPE_FIELDS = [
    ("industry", "pv1", "pv", "GROUP", 1.0, 0, 0),
    ("subindustry", "pv1", "pv", "GROUP", 1.0, 0, 0),
    ("sector", "pv1", "pv", "GROUP", 1.0, 0, 0),
    ("adv20", "pv1", "pv", "MATRIX", 1.0, 100, 1000),
]


async def _seed(catalog: Catalog, extra: list[Any] | None = None) -> None:
    for region, rows in (("USA", [*USA_FIELDS, *(extra or [])]), ("GLB", GLB_FIELDS)):
        universe = "TOP3000"
        for f, d, cat, t, cov, users, alphas in rows:
            await catalog.execute(
                "INSERT INTO data_field (field_id, dataset_id, category_id, field_type, coverage,"
                " user_count, alpha_count, instrument_type, region, delay, universe, synced_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'EQUITY', ?, 1, ?, now())",
                [f, d, cat, t, cov, users, alphas, region, universe],
            )
    for d, cat, users, alphas, n, mult in DATASETS:
        await catalog.execute(
            "INSERT INTO data_set (dataset_id, name, category_id, user_count, alpha_count,"
            " field_count, pyramid_multiplier, instrument_type, region, delay, universe)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'EQUITY', 'USA', 1, 'TOP3000')",
            [d, d.upper(), cat, users, alphas, n, mult],
        )


async def make_client(
    tmp_path: Path, *, with_schema: bool = True, scope_fields: bool = False
) -> AsyncIterator[tuple[httpx2.AsyncClient, Any]]:
    """Yield ``(client, state)``; both databases are closed afterwards.

    ``scope_fields`` adds the grouping fields and ``adv20``, for the screens that bind a
    template to a market rather than just listing what it holds.
    """
    pyramids._cache.clear()  # pyright: ignore[reportPrivateUsage]
    usage._cache.clear()  # pyright: ignore[reportPrivateUsage]
    db = Database(tmp_path / "harness.db")
    await db.create_all()
    catalog = Catalog(tmp_path / "catalog.duckdb")
    await catalog.open()
    await _seed(catalog, SCOPE_FIELDS if scope_fields else None)
    state = SimpleNamespace(
        db=db,
        catalog=catalog,
        queries=CatalogQueries(catalog),
        metadata=Metadata(await schema() if with_schema else None),
        endpoints=_pyramid_endpoints(),
    )
    app = create_app()
    app.state.harness = state
    try:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as c:
            yield c, state
    finally:
        await catalog.close()
        await db.dispose()
