"""``POST /api/forge/gym/simulate`` and ``GET /api/forge/gym/catalog``, through the real app.

Driven over ASGI without the lifespan: the Gym needs no app state, so nothing here opens the
database, the DuckDB catalog or a BRAIN session. The app's own middleware still runs, which is
why every write carries the ``x-harness-client`` header the UI sends.
"""

from typing import TYPE_CHECKING

import httpx2
import pytest

from alpha_harness.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

CLIENT = {"x-harness-client": "1"}


@pytest.fixture
async def client() -> AsyncIterator[httpx2.AsyncClient]:
    transport = httpx2.ASGITransport(app=create_app())
    async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


async def test_tear_sheet_for_the_acceptance_expression(client: httpx2.AsyncClient) -> None:
    res = await client.post(
        "/api/forge/gym/simulate",
        json={"expression": "rank(ts_backfill(anl_revision, 21))"},
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # camelCase on the wire, like every other alpha-harness response.
    assert {"inSample", "train", "test", "icDecay", "quintiles", "yearly", "checks", "pnl"} <= set(
        body
    )
    assert body["train"]["sharpe"] > 1.0  # a real planted effect
    assert body["ic"]["mean"] > 0
    assert [p["horizon"] for p in body["icDecay"]] == [1, 2, 5, 10, 21]
    assert [q["quintile"] for q in body["quintiles"]] == [1, 2, 3, 4, 5]
    assert body["yearly"][-1]["inTest"] is True
    assert body["yearly"][0]["inTest"] is False
    # The year the split falls in is mostly research data, so it counts as train.
    for y in body["yearly"]:
        assert y["inTest"] == (f"{y['year']}-01-01" >= body["testStart"])
    assert not next(y for y in body["yearly"] if y["year"] == int(body["testStart"][:4]))["inTest"]
    assert {c["name"] for c in body["checks"]} >= {"LOW_SHARPE", "LOW_SUB_UNIVERSE_SHARPE"}
    assert body["applied"] == {
        "stocks": 400,
        "decay": 0,
        "neutralization": "INDUSTRY",
        "truncation": 0.08,
        "testYears": 2.0,
    }
    assert len(body["pnl"]) > 2000


async def test_settings_change_the_result(client: httpx2.AsyncClient) -> None:
    base = {"expression": "-ts_sum(returns, 5)", "neutralization": "MARKET"}
    plain = (await client.post("/api/forge/gym/simulate", json=base, headers=CLIENT)).json()
    slowed = (
        await client.post("/api/forge/gym/simulate", json={**base, "decay": 10}, headers=CLIENT)
    ).json()
    # Decay averages the signal over days: the book trades far less.
    assert slowed["inSample"]["turnover"] < plain["inSample"]["turnover"] / 2


async def test_a_bad_expression_is_a_422_with_the_reason(client: httpx2.AsyncClient) -> None:
    res = await client.post(
        "/api/forge/gym/simulate", json={"expression": "rank(nope)"}, headers=CLIENT
    )
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "bad_expression"
    assert "nope" in detail["message"]
    res = await client.post(
        "/api/forge/gym/simulate", json={"expression": "rank(close"}, headers=CLIENT
    )
    assert res.status_code == 422


async def test_settings_the_gym_cannot_honour_are_refused(client: httpx2.AsyncClient) -> None:
    res = await client.post(
        "/api/forge/gym/simulate",
        json={"expression": "rank(close)", "neutralization": "SLOW_AND_FAST"},
        headers=CLIENT,
    )
    assert res.status_code == 422


async def test_writes_without_the_ui_header_are_refused(client: httpx2.AsyncClient) -> None:
    res = await client.post("/api/forge/gym/simulate", json={"expression": "rank(close)"})
    assert res.status_code == 403


async def test_catalog_lists_fields_but_not_the_answer_key(client: httpx2.AsyncClient) -> None:
    res = await client.get("/api/forge/gym/catalog")
    assert res.status_code == 200
    body = res.json()
    names = {f["name"] for f in body["fields"]}
    assert {"anl_revision", "returns", "industry"} <= names
    assert "rank" in body["operators"]
    assert "planted" not in res.text.lower()
    assert "answer" not in res.text.lower()
