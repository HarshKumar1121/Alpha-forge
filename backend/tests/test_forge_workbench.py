"""``/api/forge/workbench/tray`` through the real app, offline: a scope-keyed field tray."""

from typing import TYPE_CHECKING

import pytest
from forge_data_harness import CLIENT, make_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import httpx2

USA = {"region": "USA", "delay": 1, "universe": "TOP3000"}
GLB = {"region": "GLB", "delay": 1, "universe": "TOP3000"}


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx2.AsyncClient]:
    async for c, _ in make_client(tmp_path):
        yield c


async def test_a_new_scope_starts_with_an_empty_tray(client: httpx2.AsyncClient) -> None:
    res = await client.get("/api/forge/workbench/tray", params=USA, headers=CLIENT)
    assert res.status_code == 200, res.text
    assert res.json() == {"fieldIds": []}


async def test_saving_a_tray_round_trips_it(client: httpx2.AsyncClient) -> None:
    res = await client.put(
        "/api/forge/workbench/tray",
        params=USA,
        json={"fieldIds": ["anl4_eps_mean", "anl4_rev_mean"]},
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"fieldIds": ["anl4_eps_mean", "anl4_rev_mean"]}

    res = await client.get("/api/forge/workbench/tray", params=USA, headers=CLIENT)
    assert res.json() == {"fieldIds": ["anl4_eps_mean", "anl4_rev_mean"]}


async def test_saving_again_replaces_rather_than_appends(client: httpx2.AsyncClient) -> None:
    await client.put(
        "/api/forge/workbench/tray", params=USA, json={"fieldIds": ["close"]}, headers=CLIENT
    )
    res = await client.put(
        "/api/forge/workbench/tray", params=USA, json={"fieldIds": ["volume"]}, headers=CLIENT
    )
    assert res.json() == {"fieldIds": ["volume"]}


async def test_duplicates_and_blanks_are_dropped(client: httpx2.AsyncClient) -> None:
    res = await client.put(
        "/api/forge/workbench/tray",
        params=USA,
        json={"fieldIds": ["close", "close", "  ", "volume"]},
        headers=CLIENT,
    )
    assert res.json() == {"fieldIds": ["close", "volume"]}


async def test_each_scope_keeps_its_own_tray(client: httpx2.AsyncClient) -> None:
    await client.put(
        "/api/forge/workbench/tray",
        params=USA,
        json={"fieldIds": ["anl4_eps_mean"]},
        headers=CLIENT,
    )
    await client.put(
        "/api/forge/workbench/tray", params=GLB, json={"fieldIds": ["close"]}, headers=CLIENT
    )

    usa = await client.get("/api/forge/workbench/tray", params=USA, headers=CLIENT)
    glb = await client.get("/api/forge/workbench/tray", params=GLB, headers=CLIENT)
    assert usa.json() == {"fieldIds": ["anl4_eps_mean"]}
    assert glb.json() == {"fieldIds": ["close"]}
