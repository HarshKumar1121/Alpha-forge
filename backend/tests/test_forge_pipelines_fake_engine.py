"""The scriptable ``Simulator`` double 6b/6c's orchestrator tests will drive against."""

from datetime import date
from typing import TYPE_CHECKING

from alpha_harness.forge.pipelines.fake_engine import FakeSimulator, ScriptedResult
from alpha_harness.forge.simulator import request_for

if TYPE_CHECKING:
    from alpha_harness.brain.schemas import SimulationRequest

SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1, "decay": 0}


def a_request(expr: str = "rank(close)") -> SimulationRequest:
    return request_for(expr, SETTINGS)


async def test_default_script_passes_every_request() -> None:
    sim = FakeSimulator()
    requests = [a_request("rank(close)"), a_request("rank(open)")]
    outcomes = await sim.simulate(requests)
    assert [o.request for o in outcomes] == requests
    assert all(o.ok for o in outcomes)
    assert sim.spent == 2


async def test_scripted_outcomes_are_index_aligned() -> None:
    def script(request: SimulationRequest) -> ScriptedResult:
        expr = request.regular or ""
        if "bad" in expr:
            return ScriptedResult(error="not valid")
        sharpe = 1.5 if "good" in expr else 0.2
        return ScriptedResult(alpha_id=f"A-{expr}", train={"sharpe": sharpe})

    sim = FakeSimulator(script=script)
    good, bad = a_request("good_one"), a_request("bad_one")
    outcomes = await sim.simulate([good, bad])

    assert outcomes[0].ok
    assert outcomes[0].alpha is not None
    assert outcomes[0].alpha["train"]["sharpe"] == 1.5
    assert not outcomes[1].ok and outcomes[1].error == "not valid"


async def test_cached_outcomes_do_not_count_as_spend() -> None:
    sim = FakeSimulator(script=lambda _request: ScriptedResult(alpha_id="A1", cached=True))
    await sim.simulate([a_request()])
    assert sim.spent == 0


async def test_costs_quota_like_brain_simulator() -> None:
    sim = FakeSimulator()
    assert (sim.costs_quota, sim.ephemeral, sim.batch_size) == (True, False, 10)


async def test_calls_are_recorded_for_stage_order_assertions() -> None:
    sim = FakeSimulator()
    batch_one = [a_request("rank(close)")]
    batch_two = [a_request("rank(open)"), a_request("rank(low)")]
    await sim.simulate(batch_one)
    await sim.simulate(batch_two)
    assert [len(call) for call in sim.calls] == [1, 2]


async def test_pnl_returns_scripted_series() -> None:
    sim = FakeSimulator(pnl_by_alpha={"A1": [(date(2026, 1, 1), 1.0)]})
    assert await sim.pnl("A1") == [(date(2026, 1, 1), 1.0)]
    assert await sim.pnl("UNKNOWN") == []
