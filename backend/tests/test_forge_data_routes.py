"""``/api/forge/scopes`` and ``/api/forge/data/...`` through the real app, offline."""

import json
from typing import TYPE_CHECKING, Any

import pytest
from forge_data_harness import CLIENT, make_client

from alpha_harness.db.models import SimulationRecord

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import httpx2

USA = {"region": "USA", "delay": 1, "universe": "TOP3000"}
PASS = json.dumps([{"name": "LOW_SHARPE", "result": "PASS"}])
FAIL = json.dumps([{"name": "LOW_SHARPE", "result": "FAIL"}])


async def _history(state: Any) -> None:
    """Two analyst alphas in the vault (one passes), one pv alpha, one simulation sent."""
    for alpha_id, expr, universe, checks in (
        ("a1", "rank(anl4_eps_mean)", "TOP3000", PASS),
        ("a2", "rank(anl4_eps_mean - anl4_eps_low)", "TOP1000", FAIL),
        ("a3", "rank(close)", "TOP3000", PASS),
    ):
        await state.catalog.execute(
            "INSERT INTO alpha (alpha_id, expression, instrument_type, region, delay, universe,"
            " checks, fetched_at) VALUES (?, ?, 'EQUITY', 'USA', 1, ?, ?, now())",
            [alpha_id, expr, universe, checks],
        )
    async with state.db.session() as s:
        s.add(
            SimulationRecord(
                platform_id="sim1",
                request_hash="h1",
                payload={},
                expression="rank(anl4_rev_mean)",
                region="USA",
                delay=1,
                universe="TOP3000",
            )
        )


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx2.AsyncClient]:
    async for c, state in make_client(tmp_path):
        await _history(state)
        yield c


async def test_scopes_come_from_the_schema_and_show_what_is_synced(
    client: httpx2.AsyncClient,
) -> None:
    res = await client.get("/api/forge/scopes")
    assert res.status_code == 200, res.text
    (equity,) = res.json()["instrumentTypes"]
    regions = {r["value"]: r for r in equity["regions"]}
    assert set(regions) == {"USA", "GLB", "EUR", "ALL"}
    assert regions["ALL"]["regionAgnostic"] is True
    usa_d1 = next(d for d in regions["USA"]["delays"] if d["value"] == 1)
    universes = {u["value"]: u for u in usa_d1["universes"]}
    assert universes["TOP3000"]["synced"] is True
    assert universes["TOP3000"]["fields"] == 7
    assert universes["TOP1000"]["synced"] is False
    glb = {u["value"]: u for u in regions["GLB"]["delays"][0]["universes"]}
    assert glb["TOP3000"]["synced"] is True
    assert glb["MINVOL1M"]["synced"] is False


async def test_datasets_carry_crowding_pay_and_my_history(client: httpx2.AsyncClient) -> None:
    res = await client.get("/api/forge/data/datasets", params=USA)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["pyramidsLive"] is True
    rows = {d["datasetId"]: d for d in body["datasets"]}
    assert rows["pv1"]["crowding"] > rows["analyst4"]["crowding"]
    analyst = rows["analyst4"]
    assert analyst["pyramidMultiplier"] == 1.6  # what BRAIN lists on the dataset
    assert analyst["accountMultiplier"] == 1.8  # the account's pyramid
    assert analyst["pyramidLit"] is False  # 1 of 3 alphas this quarter
    assert analyst["mine"] == {
        "sims": 1,
        "alphas": 2,
        "checked": 2,
        "passed": 1,
        "passRate": 0.5,
    }
    assert rows["pv1"]["accountMultiplier"] is None  # no pyramid cell for pv


async def test_field_extras_rank_against_the_whole_scope(client: httpx2.AsyncClient) -> None:
    res = await client.post(
        "/api/forge/data/fields/extras",
        params=USA,
        json={"fieldIds": ["anl4_dps_mean", "close", "nope"]},
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    fields = res.json()["fields"]
    # Ranked among all 7 USA fields, not the 3 asked about.
    assert fields["anl4_dps_mean"]["crowding"] == pytest.approx(1 / 14, abs=1e-4)
    assert fields["close"]["crowding"] == pytest.approx(13 / 14, abs=1e-4)
    assert fields["nope"]["crowding"] is None
    assert fields["close"]["mine"]["alphas"] == 1


async def test_the_dataset_page(client: httpx2.AsyncClient) -> None:
    res = await client.get("/api/forge/data/datasets/analyst4", params=USA)
    assert res.status_code == 200, res.text
    page = res.json()
    assert page["scope"] == "EQUITY/USA/D1/TOP3000"
    bins = {round(b["low"], 1): b["fields"] for b in page["coverageBins"]}
    assert len(bins) == 10
    assert sum(bins.values()) == 5
    assert bins[0.7] == 2  # 0.70 and 0.72: a band includes its lower edge
    assert bins[0.6] == 1  # 0.65
    assert bins[0.3] == 1  # 0.33
    assert {t["fieldType"]: t["fields"] for t in page["fieldTypes"]} == {"MATRIX": 4, "VECTOR": 1}
    scatter = {p["datasetId"]: p for p in page["scatter"]}
    assert scatter["analyst4"]["current"] is True
    assert scatter["analyst4"]["multiplier"] == 1.8
    assert scatter["pv1"]["multiplier"] == 1.0  # falls back to the dataset's own
    history = page["history"]
    assert history["structuralFamilies"] == 2
    assert {u["universe"]: u["alphas"] for u in history["byUniverse"]} == {
        "TOP1000": 1,
        "TOP3000": 1,
    }
    assert page["pyramid"] == {
        "categoryId": "analyst",
        "multiplier": 1.8,
        "alphaCount": 1,
        "needed": 3,
        "lit": False,
    }


async def test_a_dataset_outside_the_scope_is_not_found(client: httpx2.AsyncClient) -> None:
    res = await client.get(
        "/api/forge/data/datasets/analyst4",
        params={"region": "GLB", "delay": 1, "universe": "TOP3000"},
    )
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "not_found"
