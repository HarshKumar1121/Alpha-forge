"""Tear-sheet statistics on synthetic series with known answers (CLAUDE.md rule 4 and 10, and
BUILD_PLAYBOOK_V2's own Phase 8 testing instruction: never BRAIN, synthetic series only).
"""

import math

import numpy as np
import pandas as pd
import pytest

from alpha_harness.brain.platform import BOOK_SIZE
from alpha_harness.forge.research import tearsheet as ts


def _series(values: list[float], start: str = "2020-01-01") -> pd.Series:
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)))


def _noise(seed: int, mu: float = 0.0, sigma: float = 0.01, n: int = 800) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mu, sigma, n), index=pd.bdate_range("2018-01-01", periods=n))


def test_underwater_tracks_a_known_single_drawdown() -> None:
    # Up to 10, down to 4 (a drawdown of 6), back up to 12.
    daily = _series([10.0, -4.0, -2.0, 10.0])
    cum = [10.0, 6.0, 4.0, 14.0]
    expected = [(c - max(cum[: i + 1])) / (BOOK_SIZE / 2) for i, c in enumerate(cum)]
    got = ts.underwater(daily).to_numpy()
    assert got == pytest.approx(expected)
    assert got[2] < 0  # the trough is a real drawdown
    assert got[-1] == 0  # a new high owes nothing to the past


def test_monthly_returns_groups_by_calendar_month() -> None:
    daily = _series([1.0, 2.0, 3.0, 4.0], start="2021-01-28")
    out = ts.monthly_returns(daily)
    # Four business days from Jan 28 cross into February.
    assert set(out) <= {(2021, 1), (2021, 2)}
    assert math.isclose(sum(out.values()), 10.0)


def test_return_distribution_reports_the_worst_days_first() -> None:
    daily = _noise(1, mu=0.0003)
    out = ts.return_distribution(daily, worst_n=3)
    worst_values = [v for _, v in out["worst_days"]]  # type: ignore[union-attr]
    assert worst_values == sorted(daily.to_numpy())[:3]
    assert len(out["qq"]) == len(daily)  # type: ignore[arg-type]
    assert out["std"] == pytest.approx(float(daily.std(ddof=1)))  # type: ignore[arg-type]


def test_return_distribution_handles_a_too_short_series() -> None:
    out = ts.return_distribution(_series([1.0]))
    assert math.isnan(out["mean"])  # type: ignore[arg-type]
    assert out["qq"] == []


def test_bootstrap_sharpe_ci_contains_its_own_point_estimate() -> None:
    daily = _noise(2, mu=0.0006, n=1000)
    observed = float(daily.mean() / daily.std(ddof=1) * math.sqrt(250))
    low, high = ts.bootstrap_sharpe_ci(
        daily, block_days=21, samples=500, rng=np.random.default_rng(0)
    )
    # A block bootstrap of the series against itself should bracket its own Sharpe almost always.
    assert low < observed < high


def test_bootstrap_sharpe_ci_is_nan_on_too_short_a_series() -> None:
    low, high = ts.bootstrap_sharpe_ci(_series([1.0, -1.0]), block_days=21, samples=50)
    assert math.isnan(low)
    assert math.isnan(high)


def test_permutation_p_value_is_not_extreme_on_pure_noise() -> None:
    # Under the null (no real direction), the p-value is itself uniform on [0, 1]: a single
    # draw can land anywhere, so only "not near either tail" is a safe thing to assert.
    daily = _noise(3, mu=0.0, n=1000)
    p = ts.permutation_test(daily, samples=1000, rng=np.random.default_rng(1))
    assert 0.05 < p < 0.95


def test_permutation_p_value_is_small_on_a_strong_trend() -> None:
    # A steady mean far larger than its own (small but nonzero) noise: almost every sign-flip
    # wrecks the Sharpe, so the real series should rank in the extreme tail of its permutations.
    daily = _noise(2, mu=0.5, sigma=0.001, n=200)
    p = ts.permutation_test(daily, samples=500, rng=np.random.default_rng(2))
    assert p < 0.05


def test_prob_sharpe_above_rises_with_a_stronger_mean() -> None:
    weak = _noise(4, mu=0.0001, n=500)
    strong = _noise(5, mu=0.002, n=500)
    assert ts.prob_sharpe_above(weak) < ts.prob_sharpe_above(strong)


def test_prob_sharpe_above_is_nan_on_a_short_series() -> None:
    assert math.isnan(ts.prob_sharpe_above(_series([1.0, 2.0])))


def test_return_based_ic_is_high_when_yesterdays_edge_repeats() -> None:
    # Each day's PnL equals yesterday's plus a little noise: strongly persistent at lag 1,
    # decaying as the lag grows and the shared history between the two shrinks.
    rng = np.random.default_rng(7)
    noise = rng.normal(0.0, 0.01, 400)
    base = np.cumsum(rng.normal(0.0, 0.02, 400))
    daily = pd.Series(base + noise, index=pd.bdate_range("2019-01-01", periods=400))
    out = ts.return_based_ic(daily, horizons=(1, 21))
    assert out[1] > 0.5
    assert out[1] > out[21]


def test_return_based_ic_is_nan_on_too_short_a_series() -> None:
    out = ts.return_based_ic(_series([1.0, -1.0, 2.0]), horizons=(1,))
    assert math.isnan(out[1])


def test_sharpe_trend_detects_rising_and_falling() -> None:
    rising = pd.Series(np.linspace(-1.0, 2.0, 60))
    falling = pd.Series(np.linspace(2.0, -1.0, 60))
    flat = pd.Series(np.full(60, 1.0))
    assert ts.sharpe_trend(rising) == "rising"
    assert ts.sharpe_trend(falling) == "falling"
    assert ts.sharpe_trend(flat) == "flat"


def test_sharpe_trend_is_flat_on_too_short_a_series() -> None:
    assert ts.sharpe_trend(pd.Series([1.0, 2.0])) == "flat"


def test_plain_verdict_mentions_the_pool_and_the_dominant_factor() -> None:
    verdict = ts.plain_verdict(
        trend="rising",
        top_correlation=("ABC123", 0.42),
        dominant_factor=("momentum", 0.3),
        residual_sharpe=1.1,
    )
    assert "improving" in verdict
    assert "0.42" in verdict
    assert "ABC123" in verdict
    assert "momentum" in verdict


def test_plain_verdict_degrades_gracefully_with_nothing_to_report() -> None:
    verdict = ts.plain_verdict(
        trend="flat", top_correlation=None, dominant_factor=None, residual_sharpe=None
    )
    assert "no comparable alpha" in verdict
    assert "hasn't been checked" in verdict
