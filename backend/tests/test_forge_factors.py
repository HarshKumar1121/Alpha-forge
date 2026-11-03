"""Factor regression: pure OLS math (``regress``) and factor-series caching (``factor_series``)
against a fake simulator (CLAUDE.md rule 4: no real BRAIN quota in tests).
"""

import math
from datetime import date, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest

from alpha_harness.forge import config
from alpha_harness.forge.alpha_types import AlphaType
from alpha_harness.forge.pipelines.fake_engine import FakeSimulator, ScriptedResult
from alpha_harness.forge.research import factors as fc
from alpha_harness.forge.store import InMemoryResearchStore

if TYPE_CHECKING:
    from alpha_harness.brain.schemas import SimulationRequest

SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1}


def _series(seed: int, n: int = 400, mu: float = 0.0) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mu, 0.01, n), index=pd.bdate_range("2018-01-01", periods=n))


def test_regress_recovers_known_betas() -> None:
    f1, f2, noise = _series(1), _series(2), _series(3)
    target = 0.7 * f1 + 0.3 * f2 + 0.05 * noise
    reg = fc.regress(target, {"f1": f1, "f2": f2})
    assert reg.n_obs == 400
    assert reg.betas["f1"] == pytest.approx(0.7, abs=0.1)
    assert reg.betas["f2"] == pytest.approx(0.3, abs=0.1)
    assert reg.r_squared > 0.8


def test_regress_unrelated_series_has_low_r_squared() -> None:
    target = _series(10)
    reg = fc.regress(target, {"f1": _series(11), "f2": _series(12)})
    assert reg.r_squared < 0.3


def test_regress_drops_factor_with_insufficient_overlap() -> None:
    target = _series(20)
    short = pd.Series([1.0, 2.0], index=pd.bdate_range("2018-01-01", periods=2))
    good = _series(21)
    reg = fc.regress(target, {"short": short, "good": good})
    assert "short" not in reg.betas
    assert "good" in reg.betas


def test_regress_with_no_usable_factors_returns_empty() -> None:
    reg = fc.regress(_series(30), {})
    assert reg.betas == {}
    assert math.isnan(reg.r_squared)


def test_regress_contributions_sum_to_r_squared() -> None:
    f1, f2, noise = _series(1), _series(2), _series(3)
    target = 0.7 * f1 + 0.3 * f2 + 0.05 * noise
    reg = fc.regress(target, {"f1": f1, "f2": f2})
    assert sum(reg.contributions.values()) == pytest.approx(reg.r_squared, abs=1e-9)
    # f1 carries more than twice f2's beta on equal-scale noise, so it should dominate.
    assert reg.contributions["f1"] > reg.contributions["f2"]


def test_summarize_mentions_the_dominant_factor_and_residual_sharpe() -> None:
    f1, f2 = _series(1), _series(2)
    target = 0.7 * f1 + 0.1 * f2
    reg = fc.regress(target, {"f1": f1, "f2": f2})
    text = fc.summarize(reg)
    assert "f1" in text
    assert "residual Sharpe" in text


def test_summarize_on_empty_regression_says_idiosyncratic() -> None:
    reg = fc.regress(_series(30), {})
    assert "idiosyncratic" in fc.summarize(reg)


async def test_factor_series_simulates_once_then_reuses_the_cache() -> None:
    specs = config.factors()["factors"]
    expr_to_alpha_id = {s["expr"]: f"SIM-{i}" for i, s in enumerate(specs)}
    pnl = {
        aid: [(date(2020, 1, 1) + timedelta(days=i), 1.0) for i in range(150)]
        for aid in expr_to_alpha_id.values()
    }

    def script(request: SimulationRequest) -> ScriptedResult:
        return ScriptedResult(alpha_id=expr_to_alpha_id[request.regular], train={"sharpe": 1.0})

    sim = FakeSimulator(script=script, pnl_by_alpha=pnl)
    alpha_type = AlphaType("test", {"simulation": {"type": "REGULAR", "settings": SETTINGS}})
    store = InMemoryResearchStore()

    series = await fc.factor_series(sim, alpha_type, SETTINGS, store=store)
    assert set(series) == {s["id"] for s in specs}
    assert sim.spent == len(specs)

    series_again = await fc.factor_series(sim, alpha_type, SETTINGS, store=store)
    assert set(series_again) == {s["id"] for s in specs}
    assert sim.spent == len(specs)  # second call for the same scope spent nothing new
