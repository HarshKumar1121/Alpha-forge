"""The simulator Protocol, as the Gym implements it.

These pin the promises the planner and Court rely on — the same ones the Phase 6 engine
adapter must keep: per-request errors, index alignment, ids that mean one alpha, PnL saved
from an ephemeral simulator, and settings that keep BRAIN's held-out test period.
"""

import pytest

from alpha_harness.forge.local.client import GymSimulator, settings_from
from alpha_harness.forge.simulator import FieldSource, Simulator, request_for
from alpha_harness.forge.store import Experiment, InMemoryResearchStore

SETTINGS = {
    "region": "USA",
    "universe": "TOP3000",
    "delay": 1,
    "decay": 0,
    "neutralization": "INDUSTRY",
    "truncation": 0.08,
}


@pytest.fixture(scope="module")
def gym() -> GymSimulator:
    return GymSimulator()


def test_the_gym_satisfies_both_protocols(gym: GymSimulator) -> None:
    assert isinstance(gym, Simulator)
    assert isinstance(gym, FieldSource)
    assert gym.costs_quota is False
    assert "anl_revision" in gym.field_names()


def test_requests_hold_out_the_test_period() -> None:
    r = request_for("rank(close)", SETTINGS)
    assert r.settings.test_period == "P2Y0M0D"  # CLAUDE.md rule 6, enforced by the request
    assert r.regular == "rank(close)"


async def test_one_bad_expression_does_not_sink_the_batch(gym: GymSimulator) -> None:
    exprs = ["rank(close)", "rank(nope)", "rank(close", "rank(ts_backfill(anl_revision, 21))"]
    outcomes = await gym.simulate([request_for(e, SETTINGS) for e in exprs])
    assert [o.request.regular for o in outcomes] == exprs  # index-aligned
    assert [o.ok for o in outcomes] == [True, False, False, True]
    bad = outcomes[1]
    assert bad.alpha_id is None
    assert bad.error is not None
    assert "nope" in bad.error


async def test_ids_name_one_alpha_across_instances(gym: GymSimulator) -> None:
    """A counter would restart at GYM1 in every process and point a cached id at the wrong
    alpha's PnL. Content-addressed ids can't."""
    req = request_for("rank(close)", SETTINGS)
    other = request_for("rank(close)", {**SETTINGS, "decay": 4})
    assert gym.alpha_id(req) == GymSimulator().alpha_id(req)
    assert gym.alpha_id(req) != gym.alpha_id(other)


async def test_pnl_is_served_after_a_run_and_empty_before() -> None:
    fresh = GymSimulator()
    req = request_for("-ts_sum(returns, 5)", SETTINGS)
    assert await fresh.pnl(fresh.alpha_id(req)) == []
    (out,) = await fresh.simulate([req])
    assert out.alpha_id is not None
    rows = await fresh.pnl(out.alpha_id)
    assert len(rows) > 2000
    assert rows[0][0] < rows[-1][0]  # oldest first


async def test_turnover_and_yearly_stats_are_served_after_a_run_and_empty_before() -> None:
    fresh = GymSimulator()
    req = request_for("-ts_sum(returns, 5)", SETTINGS)
    aid = fresh.alpha_id(req)
    assert await fresh.turnover(aid) == []
    assert await fresh.yearly_stats(aid) == []

    (out,) = await fresh.simulate([req])
    assert out.alpha_id is not None

    turnover_rows = await fresh.turnover(out.alpha_id)
    assert len(turnover_rows) > 2000
    assert turnover_rows[0][0] < turnover_rows[-1][0]  # oldest first

    yearly_rows = await fresh.yearly_stats(out.alpha_id)
    assert yearly_rows
    assert all({"year", "sharpe"} <= row.keys() for row in yearly_rows)
    assert [row["year"] for row in yearly_rows] == sorted(row["year"] for row in yearly_rows)


def test_settings_are_approximated_for_400_stocks() -> None:
    s = request_for(
        "rank(close)", {**SETTINGS, "universe": "TOP1000", "neutralization": "SUBINDUSTRY"}
    )
    mapped = settings_from(s.settings, 400)
    assert mapped.universe == 133  # a third of the market
    assert mapped.neutralization == "INDUSTRY"  # the Gym has no finer level
    assert mapped.test_years == 2.0
    risk = request_for("rank(close)", {**SETTINGS, "neutralization": "SLOW_AND_FAST"})
    assert settings_from(risk.settings, 400).neutralization == "MARKET"


async def test_store_cache_keys_on_the_whole_request(gym: GymSimulator) -> None:
    store = InMemoryResearchStore()
    req = request_for("rank(close)", SETTINGS)
    (out,) = await gym.simulate([req])
    assert out.alpha_id is not None
    assert out.alpha is not None
    await store.record(Experiment(req, out.alpha_id, out.alpha, "regular", "variant", "r1"))
    hit = await store.find(request_for("rank(close)", SETTINGS))
    assert hit is not None
    assert hit.alpha_id == out.alpha_id
    assert await store.find(request_for("rank(close)", {**SETTINGS, "decay": 4})) is None
