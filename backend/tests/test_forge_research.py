"""Profiler, template matching, families and the budgeted planner, on the Gym.

Ported from alpha-copilot ``tests/test_research.py``. The template ids are Forge's typed-slot
library; the agent-tools test stays behind with the agent, which this phase does not port.
"""

import numpy as np
import pandas as pd
import pytest

from alpha_harness.engine.lifecycle import request_hash
from alpha_harness.forge.alpha_types import load
from alpha_harness.forge.local.client import GymSimulator
from alpha_harness.forge.local.engine import LOCAL_OPERATORS
from alpha_harness.forge.research import families as fam
from alpha_harness.forge.research.planner import Researcher, estimate
from alpha_harness.forge.research.profiler import profile_from_probes, profile_local
from alpha_harness.forge.research.templates import library, match, render_single
from alpha_harness.forge.store import InMemoryResearchStore


@pytest.fixture(scope="module")
def gym() -> GymSimulator:
    return GymSimulator()


def test_profiles_from_raw_data(gym: GymSimulator) -> None:
    f = gym.market.fields
    assert profile_local(f["book_to_price"], f["cap"])["archetype"] == "slow"
    assert profile_local(f["anl_revision"], f["cap"])["archetype"] == "slow"
    assert profile_local(f["news_sentiment"], f["cap"])["archetype"] == "fast"


def test_probe_profile_sign_and_angle() -> None:
    alphas = {
        "level": {"is": {"turnover": 0.6}, "train": {"sharpe": -1.4}},
        "change": {"is": {"turnover": 0.6}, "train": {"sharpe": 0.3}},
        "history": {"is": {"turnover": 0.6}, "train": {"sharpe": -0.2}},
    }
    p = profile_from_probes(alphas)
    assert p["archetype"] == "fast"
    assert p["best_angle"] == "level"
    assert p["sign"] == -1


def test_template_matching_respects_type_rules() -> None:
    slow = {"archetype": "slow", "size_linked": False}
    ids = {t.id for t in match(slow)}
    assert {"fund_level_group", "fund_improvement", "event_update_trade"} <= ids
    assert "sentiment_smoothed" not in ids  # a fast-field template
    assert "combo_signal_x_quality" not in ids  # only when size-linked
    assert "combo_signal_x_quality" in {t.id for t in match({**slow, "size_linked": True})}
    assert "vector_intensity" not in {t.id for t in match(slow, vector=False)}
    for t in match(slow, alpha_type=load("power_pool")):
        assert t.id  # every match lints under Power Pool's 8-operator limit
    t = next(t for t in library() if t.id == "analyst_revision")
    assert (
        render_single(t, "eps", {"W": 21}, sign=-1) == "-(rank(ts_delta(ts_backfill(eps, 63), 21)))"
    )
    assert t.neighbours({"W": 21}) == [{"W": 63}]


def test_matching_skips_operators_the_simulator_lacks() -> None:
    ids = {t.id for t in match({"archetype": "slow"}, operators=set(LOCAL_OPERATORS))}
    assert "complex_residual_vs_size" not in ids  # regression_neut is not in the local engine
    assert "fund_level_group" in ids


def test_family_clustering() -> None:
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2018-01-01", periods=800)
    a = pd.Series(rng.normal(0, 1, 800), idx)
    b = 0.95 * a + 0.3 * pd.Series(rng.normal(0, 1, 800), idx)  # same bet
    c = pd.Series(rng.normal(0, 1, 800), idx)  # different bet
    fams = fam.cluster({"a": a, "b": b, "c": c}, {"a": 1.5, "b": 1.8, "c": 1.2}, 0.6)
    assert [(f.leader, sorted(f.members)) for f in fams] == [("b", ["a", "b"]), ("c", ["c"])]
    fam.against_pool(fams, {"b": b, "c": c}, {"old": a}, 0.7)
    assert fams[0].collides_with_pool
    assert fams[1].collides_with_pool is None


def test_estimate_respects_budget() -> None:
    e = estimate(4, 60)
    assert e["probes"] == 12
    assert sum(v for k, v in e.items() if k != "probes_per_field") <= 60
    assert estimate(10, 20)["probes_per_field"] == 0  # too many fields: skip probes


async def test_planner_end_to_end_and_cache(gym: GymSimulator) -> None:
    store = InMemoryResearchStore()
    fields = ["anl_revision", "alt_5"]
    mk = gym.market.fields

    def researcher() -> Researcher:
        return Researcher(
            store,
            gym,
            load("regular"),
            {"id": 1},
            fields,
            30,
            local_data={f: mk[f] for f in fields} | {"cap": mk["cap"]},
            available=set(mk),
            operators=set(LOCAL_OPERATORS),
            scope="gym",
        )

    rep = await researcher().run()
    assert rep.spent <= 30
    leaders = [f["expression"] for f in rep.families]
    assert leaders
    assert all("anl_revision" in e for e in leaders)  # the real effect is found
    assert not any("alt_5" in e for e in leaders)  # the noise field is screened out

    # The same research again reuses everything already paid for.
    rep2 = await researcher().run()
    assert rep2.cached > rep.spent // 2
    assert rep2.spent < rep.spent
    hashes = [request_hash(e.request) for e in store.experiments]
    assert len(hashes) == len(set(hashes))  # no simulation was ever paid for twice
