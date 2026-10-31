"""``GET /api/forge/templates`` and ``GET /api/forge/templates/{id}``, through the real app.

Free and offline: no BRAIN session, and the only database is a throwaway one — the library
reads it for the templates you wrote yourself and for what each template has produced.
"Try on Gym" is checked end to end: the example the library serves is simulated on the Gym
route it would be sent to.
"""

from typing import TYPE_CHECKING

import pytest
from forge_data_harness import CLIENT, make_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import httpx2


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx2.AsyncClient]:
    async for c, _ in make_client(tmp_path):
        yield c


async def test_the_library_lists_every_template_with_computed_fit(
    client: httpx2.AsyncClient,
) -> None:
    res = await client.get("/api/forge/templates")
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["templates"]) == 26
    assert body["errors"] == []
    assert {"regular", "power_pool", "atom"} <= set(body["alphaTypes"])
    blend = next(t for t in body["templates"] if t["id"] == "complex_decay_blend")
    assert blend["fits"]["power_pool"]["ok"] is False
    assert blend["operatorCount"] == 9
    assert blend["gymAvailable"] is True


async def test_detail_tries_on_the_gym(client: httpx2.AsyncClient) -> None:
    res = await client.get("/api/forge/templates/analyst_revision")
    assert res.status_code == 200, res.text
    d = res.json()
    assert d["rationale"]
    assert [s["name"] for s in d["slots"]] == ["X", "W"]
    assert d["slots"][0]["archetype"] == ["slow", "medium"]
    assert d["example"] == "rank(ts_delta(ts_backfill(x_field, 63), 63))"
    assert d["gym"]["fields"] == {"X": "anl_revision"}
    sim = await client.post(
        "/api/forge/gym/simulate", json={"expression": d["gym"]["expression"]}, headers=CLIENT
    )
    assert sim.status_code == 200, sim.text


async def test_a_template_the_gym_cannot_run_says_why(client: httpx2.AsyncClient) -> None:
    d = (await client.get("/api/forge/templates/pv_vwap_pressure")).json()
    assert d["gymAvailable"] is False
    assert d["gym"]["expression"] is None
    assert "vwap" in d["gym"]["unavailableReason"]


async def test_unknown_template_is_a_404(client: httpx2.AsyncClient) -> None:
    res = await client.get("/api/forge/templates/nope")
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "unknown_template"
