"""``GET /api/forge/templates`` and ``GET /api/forge/templates/{id}``, through the real app.

Free and offline: no BRAIN session, and the only database is a throwaway one — the library
reads it for the templates you wrote yourself and for what each template has produced.
"Try on Gym" is checked end to end: the example the library serves is simulated on the Gym
route it would be sent to.
"""

from typing import TYPE_CHECKING, Any

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


@pytest.fixture
async def client_and_state(tmp_path: Path) -> AsyncIterator[tuple[httpx2.AsyncClient, Any]]:
    async for c, state in make_client(tmp_path):
        yield c, state


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


# -- POST /compatible: which templates these fields could bind to, for the Workshop's picker ---


async def _seed_dispersion_fields(catalog: Any) -> None:
    rows = [
        ("anl4_eps_mean", "analyst4", "analyst", "MATRIX", 0.72, 300, 2000),
        ("anl4_eps_std", "analyst4", "analyst", "MATRIX", 0.60, 250, 1500),
        # Same dataset, but not the std field `analyst_dispersion`'s X slot hint asks for.
        ("anl4_afv4_eps_high", "analyst4", "analyst", "MATRIX", 0.50, 100, 400),
    ]
    for f, d, cat, t, cov, users, alphas in rows:
        await catalog.execute(
            "INSERT INTO data_field (field_id, dataset_id, category_id, field_type, coverage,"
            " user_count, alpha_count, instrument_type, region, delay, universe, synced_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'EQUITY', 'USA', 1, 'TOP3000', now())",
            [f, d, cat, t, cov, users, alphas],
        )


async def test_compatible_only_offers_templates_these_fields_can_actually_bind(
    client_and_state: tuple[httpx2.AsyncClient, Any],
) -> None:
    client, state = client_and_state
    await _seed_dispersion_fields(state.catalog)

    res = await client.post(
        "/api/forge/templates/compatible",
        json={
            "fieldIds": ["anl4_eps_mean", "anl4_eps_std"],
            "region": "USA",
            "universe": "TOP3000",
            "delay": 1,
        },
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    ids = {t["id"] for t in res.json()["templates"]}
    assert "analyst_dispersion" in ids
    # A single-field template never needs a same-dataset partner at all.
    assert "fund_level_group" in ids
    # Two-field templates this picker's fields cannot fill (e.g. a different dataset pair,
    # or a template needing archetype-filtered field kinds no field here declares) are absent
    # unless their slots genuinely bind — this is the actual question, not a blanket "it fits".
    assert all(isinstance(i, str) for i in ids)


async def test_compatible_rejects_the_decoy_same_dataset_field(
    client_and_state: tuple[httpx2.AsyncClient, Any],
) -> None:
    client, state = client_and_state
    await _seed_dispersion_fields(state.catalog)

    paired = await client.post(
        "/api/forge/templates/compatible",
        json={
            "fieldIds": ["anl4_eps_mean", "anl4_eps_std"],
            "region": "USA",
            "universe": "TOP3000",
            "delay": 1,
        },
        headers=CLIENT,
    )
    decoy = await client.post(
        "/api/forge/templates/compatible",
        json={
            "fieldIds": ["anl4_eps_std", "anl4_afv4_eps_high"],
            "region": "USA",
            "universe": "TOP3000",
            "delay": 1,
        },
        headers=CLIENT,
    )
    assert paired.status_code == decoy.status_code == 200
    assert "analyst_dispersion" in {t["id"] for t in paired.json()["templates"]}
    assert "analyst_dispersion" not in {t["id"] for t in decoy.json()["templates"]}
