"""``/api/forge/nuggets``: profile, suggest, field-search and CRUD+versioning.

Runs against throwaway databases seeded by ``forge_data_harness``; nothing here reaches BRAIN
and nothing simulates (CLAUDE.md rules 1 and 4).
"""

from typing import TYPE_CHECKING, Any

import pytest
from forge_data_harness import CLIENT, make_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import httpx2

USA = {"instrument_type": "EQUITY", "region": "USA", "delay": 1, "universe": "TOP3000"}

#: From ``forge_data_harness.USA_FIELDS``, both in the ``analyst4`` dataset.
EPS_MEAN = "anl4_eps_mean"
REV_MEAN = "anl4_rev_mean"


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx2.AsyncClient]:
    async for c, _ in make_client(tmp_path):
        yield c


@pytest.fixture
async def client_state(tmp_path: Path) -> AsyncIterator[tuple[httpx2.AsyncClient, Any]]:
    async for pair in make_client(tmp_path):
        yield pair


async def _create(
    client: httpx2.AsyncClient, *, field_ids: list[str] | None = None, **over: Any
) -> dict[str, Any]:
    body = {
        "name": "EPS and revenue co-move",
        "scope": USA,
        "fieldIds": field_ids or [EPS_MEAN, REV_MEAN],
        "dataTreatment": {},
        "rationale": "Both are analyst consensus measures for the same company.",
        "claim": "They predict returns together; wrong if they diverge persistently.",
        "tags": ["analyst"],
        **over,
    }
    r = await client.post("/api/forge/nuggets", json=body, headers=CLIENT)
    return r.json() if r.status_code < 400 else {"status": r.status_code, **r.json()}


async def test_profile_reads_type_and_category_without_simulating(
    client: httpx2.AsyncClient,
) -> None:
    body = {"scope": USA, "fieldIds": [EPS_MEAN, REV_MEAN, "does_not_exist"]}
    r = await client.post("/api/forge/nuggets/profile", json=body, headers=CLIENT)
    assert r.status_code == 200, r.text
    out = r.json()
    assert {f["fieldId"] for f in out["fields"]} == {EPS_MEAN, REV_MEAN}
    assert out["missing"] == ["does_not_exist"]


async def test_suggest_pairs_same_dataset_fields(client: httpx2.AsyncClient) -> None:
    body = {"scope": USA, "fieldIds": [EPS_MEAN, REV_MEAN]}
    r = await client.post("/api/forge/nuggets/suggest", json=body, headers=CLIENT)
    assert r.status_code == 200, r.text
    suggestions = r.json()["suggestions"]
    assert len(suggestions) == 1
    assert suggestions[0]["valid"] is True


async def test_create_nugget_round_trips(client: httpx2.AsyncClient) -> None:
    created = await _create(client)
    assert created["nuggetId"] == "eps_and_revenue_co_move"
    assert created["version"] == 1
    assert set(created["fieldIds"]) == {EPS_MEAN, REV_MEAN}

    r = await client.get(f"/api/forge/nuggets/{created['nuggetId']}", headers=CLIENT)
    detail = r.json()
    assert {f["fieldId"] for f in detail["fields"]} == {EPS_MEAN, REV_MEAN}
    assert detail["resolvedTreatment"][EPS_MEAN] == f"ts_backfill({EPS_MEAN}, 63)"


async def test_create_nugget_needs_at_least_two_fields(client: httpx2.AsyncClient) -> None:
    r = await client.post(
        "/api/forge/nuggets",
        json={
            "name": "Just one",
            "scope": USA,
            "fieldIds": [EPS_MEAN],
            "rationale": "x",
            "claim": "y",
        },
        headers=CLIENT,
    )
    assert r.status_code == 422


async def test_create_nugget_rejects_duplicate_signal_pair(
    client_state: tuple[httpx2.AsyncClient, Any],
) -> None:
    client, state = client_state
    insert = (
        "INSERT INTO data_field (field_id, dataset_id, category_id, field_type, description,"
        " coverage, user_count, alpha_count, instrument_type, region, delay, universe,"
        " synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'EQUITY', 'USA', 1, 'TOP3000', now())"
    )
    await state.catalog.execute(
        insert,
        [
            "anl4_eps_std",
            "analyst4",
            "analyst",
            "MATRIX",
            "Std dev analyst EPS estimate",
            0.5,
            10,
            10,
        ],
    )
    await state.catalog.execute(
        insert,
        [
            "anl4_eps_avg",
            "analyst4",
            "analyst",
            "MATRIX",
            "Average analyst EPS estimate",
            0.5,
            10,
            10,
        ],
    )
    await state.catalog.execute(
        "UPDATE data_field SET description = 'Mean analyst EPS estimate' "
        "WHERE field_id = ? AND region = 'USA'",
        [EPS_MEAN],
    )

    out = await _create(client, field_ids=[EPS_MEAN, "anl4_eps_avg"])
    assert out.get("status") == 422
    assert "same measure" in out["detail"]["message"]

    # The mean/std pair across the same dataset is a legitimate pairing, not a duplicate.
    ok = await _create(client, field_ids=[EPS_MEAN, "anl4_eps_std"], name="EPS mean and std")
    assert ok["nuggetId"] == "eps_mean_and_std"


async def test_update_nugget_bumps_version_and_keeps_history(client: httpx2.AsyncClient) -> None:
    created = await _create(client)
    nid = created["nuggetId"]
    updated = await client.put(
        f"/api/forge/nuggets/{nid}",
        json={
            "name": created["name"],
            "scope": USA,
            "fieldIds": [EPS_MEAN, REV_MEAN],
            "dataTreatment": {},
            "rationale": "Refined rationale with more detail.",
            "claim": created["claim"],
            "tags": [],
            "note": "Tightened the rationale",
        },
        headers=CLIENT,
    )
    body = updated.json()
    assert body["version"] == 2
    versions = await client.get(f"/api/forge/nuggets/{nid}/versions", headers=CLIENT)
    assert [v["version"] for v in versions.json()] == [2, 1]


async def test_delete_nugget(client: httpx2.AsyncClient) -> None:
    created = await _create(client)
    r = await client.delete(f"/api/forge/nuggets/{created['nuggetId']}", headers=CLIENT)
    assert r.json()["deleted"] is True
    missing = await client.get(f"/api/forge/nuggets/{created['nuggetId']}", headers=CLIENT)
    assert missing.status_code == 404


async def test_field_search_low_crowding_only_narrows_results(client: httpx2.AsyncClient) -> None:
    body = {"scope": USA, "filter": {}, "lowCrowdingOnly": True, "crowdingThreshold": 0.3}
    r = await client.post("/api/forge/nuggets/field-search", json=body, headers=CLIENT)
    assert r.status_code == 200, r.text
    rows = r.json()
    # "close"/"volume" are the heaviest-used fields in the fixture pool: under a strict
    # threshold they drop out of a "low crowding only" page.
    assert "close" not in {row["fieldId"] for row in rows}
