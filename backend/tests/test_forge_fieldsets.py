"""``/api/forge/fieldsets``: create, list, read, update and delete, through the real app.

SQLite and the DuckDB catalog are throwaway files; the scope check runs against the
hand-written ``OPTIONS /simulations`` fixture. Nothing reaches BRAIN.
"""

from typing import TYPE_CHECKING, Any

import pytest
from forge_data_harness import CLIENT, make_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import httpx2

USA = {"region": "USA", "delay": 1, "universe": "TOP3000"}
GLB = {"region": "GLB", "delay": 1, "universe": "MINVOL1M"}
ANALYST_FIVE = ["anl4_eps_mean", "anl4_eps_low", "anl4_rev_mean", "anl4_rev_low", "anl4_dps_mean"]


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx2.AsyncClient]:
    async for c, _ in make_client(tmp_path):
        yield c


def _manual(name: str, scope: dict[str, Any], ids: list[str], **kw: Any) -> dict[str, Any]:
    return {"name": name, "scope": scope, "kind": "manual", "fieldIds": ids, **kw}


async def test_a_manual_set_keeps_its_scope_and_resolves_its_fields(
    client: httpx2.AsyncClient,
) -> None:
    res = await client.post(
        "/api/forge/fieldsets",
        json=_manual("USA TOP3000 analyst", USA, ANALYST_FIVE, notes="revisions idea"),
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    made = res.json()
    assert made["scope"] == {"instrumentType": "EQUITY", **USA}
    assert made["fieldIds"] == ANALYST_FIVE
    assert made["notes"] == "revisions idea"

    detail = (await client.get(f"/api/forge/fieldsets/{made['id']}")).json()
    assert detail["total"] == 5
    assert sorted(f["field_id"] for f in detail["fields"]) == sorted(ANALYST_FIVE)
    assert detail["missing"] == []


async def test_manual_picks_missing_from_the_scope_are_reported(
    client: httpx2.AsyncClient,
) -> None:
    res = await client.post(
        "/api/forge/fieldsets",
        json=_manual("GLB minvol", GLB, ["close", "anl4_eps_mean", "close"]),
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    assert res.json()["fieldIds"] == ["close", "anl4_eps_mean"]  # deduplicated, order kept
    detail = (await client.get(f"/api/forge/fieldsets/{res.json()['id']}")).json()
    # GLB/MINVOL1M is not synced in the fixture catalog, so both picks are reported missing.
    assert detail["missing"] == ["close", "anl4_eps_mean"]


async def test_a_dynamic_set_reruns_its_filter(client: httpx2.AsyncClient) -> None:
    body = {
        "name": "well covered analyst",
        "scope": USA,
        "kind": "dynamic",
        "filter": {"category_ids": ["analyst"], "coverage_min": 0.6, "limit": 3, "offset": 9},
    }
    res = await client.post("/api/forge/fieldsets", json=body, headers=CLIENT)
    assert res.status_code == 200, res.text
    saved = res.json()["filter"]
    assert "limit" not in saved
    assert "offset" not in saved
    detail = (await client.get(f"/api/forge/fieldsets/{res.json()['id']}")).json()
    assert sorted(f["field_id"] for f in detail["fields"]) == [
        "anl4_eps_low",
        "anl4_eps_mean",
        "anl4_rev_mean",
    ]


async def test_list_update_and_delete(client: httpx2.AsyncClient) -> None:
    a = (
        await client.post("/api/forge/fieldsets", json=_manual("a", USA, ["close"]), headers=CLIENT)
    ).json()
    await client.post("/api/forge/fieldsets", json=_manual("b", GLB, ["close"]), headers=CLIENT)

    every = (await client.get("/api/forge/fieldsets")).json()
    assert {s["name"] for s in every} == {"a", "b"}
    glb = (await client.get("/api/forge/fieldsets", params={"region": "GLB"})).json()
    assert [s["name"] for s in glb] == ["b"]

    res = await client.put(
        f"/api/forge/fieldsets/{a['id']}",
        json=_manual("a renamed", USA, ["close", "volume"], notes="pv"),
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "a renamed"
    assert res.json()["fieldIds"] == ["close", "volume"]

    res = await client.delete(f"/api/forge/fieldsets/{a['id']}", headers=CLIENT)
    assert res.json() == {"deleted": True}
    res = await client.get(f"/api/forge/fieldsets/{a['id']}")
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "not_found"


async def test_names_are_unique(client: httpx2.AsyncClient) -> None:
    await client.post("/api/forge/fieldsets", json=_manual("x", USA, ["close"]), headers=CLIENT)
    res = await client.post(
        "/api/forge/fieldsets", json=_manual("x", GLB, ["close"]), headers=CLIENT
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "name_taken"

    other = (
        await client.post("/api/forge/fieldsets", json=_manual("y", USA, ["close"]), headers=CLIENT)
    ).json()
    res = await client.put(
        f"/api/forge/fieldsets/{other['id']}", json=_manual("x", USA, ["close"]), headers=CLIENT
    )
    assert res.status_code == 409


@pytest.mark.parametrize(
    "scope",
    [
        {"region": "GLB", "delay": 0, "universe": "TOP3000"},
        {"region": "USA", "delay": 1, "universe": "MINVOL1M"},
    ],
)
async def test_a_scope_the_account_cannot_run_is_refused(
    client: httpx2.AsyncClient, scope: dict[str, Any]
) -> None:
    res = await client.post(
        "/api/forge/fieldsets", json=_manual("nope", scope, ["close"]), headers=CLIENT
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "scope_unavailable"


async def test_an_empty_manual_set_is_refused(client: httpx2.AsyncClient) -> None:
    res = await client.post(
        "/api/forge/fieldsets", json=_manual("empty", USA, ["  "]), headers=CLIENT
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "empty_selection"


async def test_writes_need_the_harness_client_header(client: httpx2.AsyncClient) -> None:
    res = await client.post("/api/forge/fieldsets", json=_manual("x", USA, ["close"]))
    assert res.status_code == 403


async def test_without_a_schema_the_scope_cannot_be_checked(tmp_path: Path) -> None:
    async for c, _ in make_client(tmp_path, with_schema=False):
        res = await c.post(
            "/api/forge/fieldsets", json=_manual("x", USA, ["close"]), headers=CLIENT
        )
        assert res.status_code == 503
        assert res.json()["detail"]["code"] == "no_schema"
