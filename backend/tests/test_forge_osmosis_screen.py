"""Offline tests for Part B's backend: the basis per scope, the revision cache, the progress
counter and the Why panel's headline. Synthetic data and fakes only, no BRAIN."""

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from alpha_harness.forge.osmosis import api as api_mod
from alpha_harness.forge.osmosis import scope as scope_mod
from alpha_harness.forge.osmosis.api import apply_scope_bases
from alpha_harness.forge.osmosis.inputs import apply_basis
from alpha_harness.forge.osmosis.scope import ScopeAnalysis
from alpha_harness.forge.osmosis.settings import Settings
from alpha_harness.forge.osmosis.walkforward import Judgement, MethodVerdict
from alpha_harness.forge.osmosis.weights import METHODS
from alpha_harness.forge.osmosis.why import build_why

if TYPE_CHECKING:
    import pytest


def _settings() -> Settings:
    return Settings(pnl_basis="after_cost_5bps")


def _days_index(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2020-01-01", periods=n, freq="B")


def test_a_scope_with_complete_turnover_is_net_of_cost_and_a_scope_missing_it_is_gross() -> None:
    index = _days_index(40)
    rng = np.random.default_rng(1)
    gross = pd.DataFrame(rng.normal(1000, 500, size=(40, 3)), index=index, columns=["A", "B", "C"])
    turnover = pd.DataFrame(rng.uniform(0.1, 0.4, size=(40, 3)), index=index, columns=gross.columns)
    turnover.loc[index[5], "C"] = np.nan  # C lacks turnover on a day it has PnL: its scope is gross
    scope_of = {"A": ("USA", 1), "B": ("USA", 1), "C": ("EUR", 1)}
    # Give each scope exactly its own members, so the expectations below are unambiguous.
    net, bases = apply_scope_bases(gross, turnover, scope_of, _settings())

    assert bases[("USA", 1)].name == "after_cost_5bps"
    assert bases[("EUR", 1)].name == "gross"
    assert "C" in bases[("EUR", 1)].reason
    expected_a = apply_basis(gross["A"].to_numpy(), turnover["A"].to_numpy(), 5.0)
    assert np.allclose(net["A"].to_numpy(), expected_a)
    assert np.allclose(net["C"].to_numpy(), gross["C"].to_numpy())  # gross, unchanged


def test_summary_reuses_cached_analyses_for_the_same_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []

    def fake_analyse(
        _data: object, region: str, delay: int, _n: object, _method: object
    ) -> ScopeAnalysis:
        calls.append((region, delay))
        return ScopeAnalysis(region=region, delay=delay, alpha_count=3)

    monkeypatch.setattr(api_mod, "_analyse", fake_analyse)
    monkeypatch.setattr(api_mod, "_ANALYSES", {})
    monkeypatch.setattr(api_mod, "_analyses_revision", None)
    key = ("USA", 1)
    import asyncio

    async def run() -> None:
        await api_mod._scope_analysis(object(), "rev-1", key, None, None)  # type: ignore[arg-type]
        await api_mod._scope_analysis(object(), "rev-1", key, None, None)  # type: ignore[arg-type]

    asyncio.run(run())
    assert calls == [key]  # the second read is a warm hit: no maths

    async def newer() -> None:
        await api_mod._scope_analysis(object(), "rev-2", key, None, None)  # type: ignore[arg-type]

    asyncio.run(newer())
    assert calls == [key, key]  # a new revision clears the cache and recomputes


def test_the_refit_cache_serves_the_same_fit_and_recomputes_on_new_data() -> None:
    rng = np.random.default_rng(2)
    frame = pd.DataFrame(rng.normal(0, 1, size=(300, 4)), columns=list("ABCD"))
    settings = Settings(cap=0.5, floor=0.01, resample_count=20)
    scope_mod.clear_caches()
    first = scope_mod.cached_fit("max_sharpe", frame, settings)
    again = scope_mod.cached_fit("max_sharpe", frame, settings)
    assert first is again  # served from the cache, not refitted
    values = frame.to_numpy().copy()
    values[0, 0] += 1.0
    changed = pd.DataFrame(values, columns=frame.columns)
    other = scope_mod.cached_fit("max_sharpe", changed, settings)
    assert other is not first


def _verdicts(identical: bool) -> tuple[MethodVerdict, ...]:
    return tuple(
        MethodVerdict(m, 0.0, np.nan, np.nan, False, identical=identical) for m in METHODS[1:]
    )


def test_equal_weight_winning_leads_the_why_panel_with_the_headline() -> None:
    analysis = ScopeAnalysis(region="USA", delay=1, alpha_count=11)
    analysis.judgement = Judgement("equal_weight", "no method beat it", _verdicts(identical=False))
    analysis.method = "equal_weight"
    analysis.default_method = "hrp"
    analysis.requested_method = "hrp"
    analysis.n = 6
    analysis.recommended_n = 6
    analysis.folds = 15
    analysis.window_days = 504
    panel = build_why(analysis, _settings())
    assert panel.method_line == (
        "Equal weight: no method beat it out of sample (11 tested). "
        "Selection and diversification matter more than weighting here."
    )


def test_identical_series_are_named_as_not_tested_in_the_why_panel() -> None:
    analysis = ScopeAnalysis(region="JPN", delay=1, alpha_count=4)
    analysis.judgement = Judgement("equal_weight", "identical", _verdicts(identical=True))
    analysis.method = "equal_weight"
    analysis.default_method = "hrp"
    analysis.requested_method = "hrp"
    analysis.n = 4
    analysis.recommended_n = 4
    analysis.folds = 16
    analysis.window_days = 504
    panel = build_why(analysis, _settings())
    assert "none was tested" in panel.method_line
