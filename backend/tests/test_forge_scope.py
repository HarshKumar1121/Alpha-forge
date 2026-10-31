"""Scope resolution from a recorded ``OPTIONS /simulations`` response.

The response goes through the real :meth:`BrainEndpoints.settings_schema` (which folds the
regular and region-agnostic trees together) over a mock transport, so this checks the same
path the app runs — only the socket is fake.
"""

from typing import Any

import pytest
from conftest import json_response, make_endpoints, route
from forge_options_fixture import OPTIONS_SIMULATIONS

from alpha_harness.forge.data.scope import Scope, check_scope, scope_tree


@pytest.fixture
async def schema() -> dict[str, Any]:
    endpoints, recorder = make_endpoints(
        route({("OPTIONS", "/simulations"): json_response(OPTIONS_SIMULATIONS)})
    )
    resolved = await endpoints.settings_schema()
    assert recorder.calls == [("OPTIONS", "/simulations")]
    return resolved


def _regions(tree: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    (equity,) = tree
    return {r["value"]: r for r in equity["regions"]}


def _universes(region: dict[str, Any], delay: int) -> list[str]:
    (node,) = [d for d in region["delays"] if d["value"] == delay]
    return [u["value"] for u in node["universes"]]


async def test_the_tree_holds_every_market_the_account_can_run(schema: dict[str, Any]) -> None:
    tree = scope_tree(schema)
    assert [t["value"] for t in tree] == ["EQUITY"]
    assert tree[0]["label"] == "Equity"
    regions = _regions(tree)
    assert list(regions) == ["USA", "GLB", "EUR", "ALL"]
    assert [d["value"] for d in regions["USA"]["delays"]] == [0, 1]
    assert _universes(regions["USA"], 1) == ["TOP3000", "TOP1000", "ILLIQUID_MINVOL1M"]
    assert _universes(regions["EUR"], 0) == ["TOP2500"]


async def test_glb_runs_only_at_delay_one_and_minvol_is_regional(schema: dict[str, Any]) -> None:
    regions = _regions(scope_tree(schema))
    assert [d["value"] for d in regions["GLB"]["delays"]] == [1]
    assert "MINVOL1M" in _universes(regions["GLB"], 1)
    assert "MINVOL1M" not in _universes(regions["USA"], 1)


async def test_region_all_comes_only_from_the_region_agnostic_tree(schema: dict[str, Any]) -> None:
    regions = _regions(scope_tree(schema))
    assert regions["ALL"]["regionAgnostic"] is True
    assert not any(r["regionAgnostic"] for k, r in regions.items() if k != "ALL")
    assert _universes(regions["ALL"], 1) == ["TOPDIV3000"]


async def test_neutralizations_follow_the_region(schema: dict[str, Any]) -> None:
    regions = _regions(scope_tree(schema))
    usa = regions["USA"]["delays"][1]["universes"][0]
    glb = regions["GLB"]["delays"][0]["universes"][1]
    assert [n["value"] for n in usa["neutralizations"]] == [
        "SUBINDUSTRY",
        "INDUSTRY",
        "STATISTICAL",
    ]
    assert usa["neutralizations"][0]["label"] == "Subindustry"
    assert [n["value"] for n in glb["neutralizations"]] == ["COUNTRY", "INDUSTRY"]
    assert [n["value"] for n in regions["ALL"]["delays"][0]["universes"][0]["neutralizations"]] == [
        "MARKET"
    ]


@pytest.mark.parametrize(
    ("region", "delay", "universe"),
    [
        ("USA", 1, "TOP3000"),
        ("GLB", 1, "MINVOL1M"),
        ("EUR", 0, "TOP2500"),
        ("ALL", 1, "TOPDIV3000"),
    ],
)
async def test_runnable_scopes_pass(
    schema: dict[str, Any], region: str, delay: int, universe: str
) -> None:
    assert check_scope(schema, Scope(region=region, delay=delay, universe=universe)) == []


@pytest.mark.parametrize(
    ("region", "delay", "universe", "says"),
    [
        ("GLB", 0, "TOP3000", "Delay 0"),
        ("USA", 1, "MINVOL1M", "Universe 'MINVOL1M'"),
        ("JPN", 1, "TOP1600", "Region 'JPN'"),
    ],
)
async def test_impossible_scopes_are_refused_with_a_reason(
    schema: dict[str, Any], region: str, delay: int, universe: str, says: str
) -> None:
    problems = check_scope(schema, Scope(region=region, delay=delay, universe=universe))
    assert problems
    assert says in problems[0]


async def test_an_unknown_instrument_type_is_refused(schema: dict[str, Any]) -> None:
    problems = check_scope(
        schema, Scope(instrument_type="CRYPTO", region="USA", delay=1, universe="TOP3000")
    )
    assert problems
    assert "CRYPTO" in problems[0]
