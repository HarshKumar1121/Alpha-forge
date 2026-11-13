"""``/api/forge/osmosis``: the scope, summary, plan and CSV, read through a fake alphas store.

No BRAIN client, no database, no simulator: the store is a fake and ``touch_pnl`` is recorded
instead of written (CLAUDE.md rule 4). The app is the real one, so the router is wired as it
ships.
"""

import json
from typing import TYPE_CHECKING, Any

import httpx2
import numpy as np
import pandas as pd
import pytest
from test_forge_osmosis import synthetic_pnl
from test_forge_pipelines_api import CLIENT

from alpha_harness.forge.osmosis import api as osmosis_api
from alpha_harness.forge.osmosis.plan import CSV_COLUMNS
from alpha_harness.forge.osmosis.settings import Settings
from alpha_harness.forge.osmosis.weights import METHODS
from alpha_harness.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

PASS = json.dumps([{"name": "OSMOSIS_ALLOCATION", "result": "PASS"}])
WARN = json.dumps([{"name": "OSMOSIS_ALLOCATION", "result": "WARNING"}])
DAYS = 700
#: Every Alpha ID the route marked as read in the current test, in order.
TOUCHED: list[str] = []
PARENT = "PAR1"


def small_settings() -> Settings:
    """Settings small enough for fast tests, with the same fold layout logic as the defaults."""
    # forge: small resample_count keeps the walk-forward fast in tests (see test_forge_osmosis.py).
    return Settings(
        train_days=200,
        test_days=60,
        step=60,
        bootstrap_samples=200,
        bootstrap_block=10.0,
        resample_count=20,
    )


class FakeCatalog:
    """The one catalog read the route makes: the submitted parent alphas."""

    async def query(self, sql: str, _params: object = None) -> list[dict[str, Any]]:
        if "count(*)" in sql:  # the stored-data revision: row count and last PnL date
            return [{"n": 1, "last": "2024-01-01"}]
        return [{"alpha_id": PARENT}]


class FakeAlphas:
    """The store reads the route makes, served from memory."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        series: dict[str, dict[Any, tuple[float, float]]],
        meta: dict[str, dict[str, Any]],
    ) -> None:
        self._rows = rows
        self._series = series
        self._meta = meta
        self.catalog = FakeCatalog()

    async def submitted_members(self) -> list[dict[str, Any]]:
        return self._rows

    async def series(self, alpha_ids: list[str]) -> dict[str, dict[Any, tuple[float, float]]]:
        return {a: self._series[a] for a in alpha_ids if a in self._series}

    async def by_ids(self, alpha_ids: list[str]) -> dict[str, dict[str, Any]]:
        return {a: self._meta[a] for a in alpha_ids if a in self._meta}


class FakeState:
    def __init__(self, alphas: FakeAlphas) -> None:
        self.alphas = alphas
        self.db = None  # never used: touch_pnl is replaced in these tests


def _store() -> FakeAlphas:
    """Twelve USA Alphas (the last one WARNING, which must not gate it) and five EUR Alphas,
    all PASS. USA has enough to plan; EUR is below the unverified minimum, so it is shown with
    a warning rather than blocked."""
    rows: list[dict[str, Any]] = []
    series: dict[str, dict[Any, tuple[float, float]]] = {}
    meta: dict[str, dict[str, Any]] = {}
    days = pd.bdate_range(end="2026-06-30", periods=DAYS)
    for prefix, region, count, seed in (("USA", "USA", 12, 21), ("EUR", "EUR", 5, 22)):
        rng = np.random.default_rng(seed)
        names = [f"{prefix}{i:02d}" for i in range(count)]
        pnl = synthetic_pnl(
            DAYS,
            list(rng.uniform(-0.05, 0.3, count)),
            list(rng.uniform(0.5, 2.0, count)),
            rho=0.2,
            seed=seed,
            names=names,
        )
        for name in names:
            rows.append(
                {
                    "alpha_id": name,
                    "region": region,
                    "delay": 1,
                    "sharpe": 1.5,
                    "turnover": 0.05,
                    "fitness": 1.1,
                    "drawdown": 0.04,
                    "prod_correlation": 0.1,
                    "universe": "TOP3000",
                    "classifications": json.dumps(["Momentum"]),
                }
            )
            series[name] = {d.date(): (float(v), 0.1) for d, v in zip(days, pnl[name], strict=True)}
            meta[name] = {"checks": WARN if name == "USA11" else PASS}
    return FakeAlphas(rows, series, meta)


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx2.AsyncClient]:
    async def record(_db: object, alpha_ids: list[str]) -> None:
        TOUCHED.extend(alpha_ids)

    TOUCHED.clear()
    monkeypatch.setattr(osmosis_api, "touch_pnl", record)
    monkeypatch.setattr(osmosis_api, "load_settings", small_settings)
    app = create_app()
    app.state.harness = FakeState(_store())
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://localhost", headers=CLIENT
    ) as c:
        yield c


async def test_plan_lists_each_scope_with_points_and_every_method(
    client: httpx2.AsyncClient,
) -> None:
    body = (await client.get("/api/forge/osmosis/plan")).json()
    scopes = {s["region"]: s for s in body["scopes"]}
    usa = scopes["USA"]
    assert usa["blocked"] is None
    assert sum(p["points"] for p in usa["points"]) == 100_000
    assert {m["method"] for m in usa["methods"]} == set(METHODS)
    equal = next(m for m in usa["methods"] if m["method"] == "equal_weight")
    assert equal["gain"] is None and equal["passedHoldout"] is None
    for method in usa["methods"]:
        if method["method"] != "equal_weight":
            assert {"gain", "ciLow", "ciHigh", "passedHoldout", "foldWinRate"} <= method.keys()
    shipped = [m["method"] for m in usa["methods"] if m["isPlan"]]
    assert shipped == [usa["method"]]
    assert body["settings"]["minFolds"] == 4 and body["settings"]["holdoutFolds"] == 2


async def test_the_check_never_gates_a_candidate(client: httpx2.AsyncClient) -> None:
    """USA11 holds a WARNING check. It is still a candidate, so the scope counts all twelve."""
    body = (
        await client.get("/api/forge/osmosis/scope", params={"region": "USA", "delay": 1})
    ).json()
    assert body["alphaCount"] == 12
    assert body["blocked"] is None


async def test_a_scope_below_the_minimum_is_planned_with_a_warning(
    client: httpx2.AsyncClient,
) -> None:
    body = (
        await client.get("/api/forge/osmosis/scope", params={"region": "EUR", "delay": 1})
    ).json()
    assert body["blocked"] is None
    assert body["belowMinimum"] is True
    assert body["n"] == body["recommendedN"] == 5  # all available
    assert any("Below 10 alphas" in w for w in body["warnings"])


async def test_every_alpha_read_is_marked_as_read(client: httpx2.AsyncClient) -> None:
    await client.get("/api/forge/osmosis/plan")
    assert set(TOUCHED) == {f"USA{i:02d}" for i in range(12)} | {f"EUR{i:02d}" for i in range(5)}


async def test_csv_download_has_the_columns_and_points_that_sum_to_100000_per_scope(
    client: httpx2.AsyncClient,
) -> None:
    resp = await client.get("/api/forge/osmosis/plan.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    lines = resp.text.strip().splitlines()
    assert lines[0] == ",".join(CSV_COLUMNS)
    rows = [line.split(",") for line in lines[1:]]
    for region in ("USA", "EUR"):
        assert sum(int(r[5]) for r in rows if r[2] == region) == 100_000
    assert "payment" not in resp.text.lower()


def test_the_osmosis_router_has_only_read_routes() -> None:
    """Part B reads and exports. No route may submit, or write anything back to BRAIN."""
    methods = {m for route in osmosis_api.router.routes for m in getattr(route, "methods", ())}
    assert methods <= {"GET"}


async def test_scope_default_uses_the_recommended_n_and_sums_to_100000(
    client: httpx2.AsyncClient,
) -> None:
    body = (
        await client.get("/api/forge/osmosis/scope", params={"region": "USA", "delay": 1})
    ).json()
    assert body["n"] == body["recommendedN"]
    assert len(body["rows"]) == body["n"]
    assert body["totalPoints"] == 100_000
    assert sum(r["points"] for r in body["rows"]) == 100_000
    assert [c["n"] for c in body["curve"]] == list(range(1, len(body["curve"]) + 1))
    assert body["nMin"] == 4 and body["nMax"] >= body["n"] >= 4
    assert body["rankedBeyondN"] == len(body["curve"]) - body["n"]


async def test_scope_carries_the_effective_bets_curve_and_the_why_panel(
    client: httpx2.AsyncClient,
) -> None:
    body = (
        await client.get("/api/forge/osmosis/scope", params={"region": "USA", "delay": 1})
    ).json()
    bets = body["betsCurve"]
    assert [b["n"] for b in bets] == [c["n"] for c in body["curve"]]
    assert bets[0]["gain"] is None and bets[0]["effectiveBets"] == 1.0
    assert all(b["effectiveBets"] <= b["n"] + 1e-9 for b in bets)
    why = body["why"]
    assert why["method"] == body["method"]
    assert "No out-of-sample data is available" in why["evidenceLine"]
    assert "Decay check not run" in why["decayLine"]


async def test_scope_honours_n_and_method_and_clamps_n(client: httpx2.AsyncClient) -> None:
    chosen = await client.get(
        "/api/forge/osmosis/scope",
        params={"region": "USA", "delay": 1, "n": 6, "method": "inverse_volatility"},
    )
    body = chosen.json()
    assert body["n"] == 6 and len(body["rows"]) == 6
    assert body["requestedMethod"] == "inverse_volatility"
    assert body["totalPoints"] == 100_000
    assert body["combined"] is not None and body["combined"]["equal"]["turnover"] is not None

    clamped = (
        await client.get("/api/forge/osmosis/scope", params={"region": "USA", "delay": 1, "n": 999})
    ).json()
    assert clamped["n"] == clamped["nMax"]


async def test_scope_view_has_the_chart_and_the_excluded_reasons(
    client: httpx2.AsyncClient,
) -> None:
    body = (
        await client.get("/api/forge/osmosis/scope", params={"region": "USA", "delay": 1})
    ).json()
    assert body["chart"] and {"date", "allocation", "equal"} <= body["chart"][0].keys()
    for excluded in body["excluded"]:
        assert excluded["reason"] in {"correlation", "gap", "flat"}
        assert excluded["detail"]


async def test_unknown_scope_is_blocked_with_a_plain_reason(client: httpx2.AsyncClient) -> None:
    body = (
        await client.get("/api/forge/osmosis/scope", params={"region": "ZZZ", "delay": 1})
    ).json()
    assert body["blocked"] is not None and "No submitted Alphas" in body["blocked"]
    assert body["rows"] == [] and body["combined"] is None


async def test_summary_lists_every_scope_and_names_the_parent_once(
    client: httpx2.AsyncClient,
) -> None:
    body = (await client.get("/api/forge/osmosis/summary")).json()
    scopes = {(s["region"], s["delay"]): s for s in body["scopes"]}
    assert set(scopes) == {("USA", 1), ("EUR", 1)}
    assert scopes[("USA", 1)]["recommendedN"] >= 4
    assert scopes[("EUR", 1)]["belowMinimum"] is True
    assert body["parentsExcluded"] == [PARENT]


async def test_scope_method_must_be_one_of_the_five(client: httpx2.AsyncClient) -> None:
    res = await client.get(
        "/api/forge/osmosis/scope", params={"region": "USA", "delay": 1, "method": "magic"}
    )
    assert res.status_code == 422


async def test_the_retired_topn_route_is_gone(client: httpx2.AsyncClient) -> None:
    res = await client.get("/api/forge/osmosis/topn", params={"region": "USA", "delay": 1})
    assert res.status_code == 404
