"""PnL analytics: deflated Sharpe, correlation, the collision rule.

Ported from alpha-copilot ``tests/test_core.py``.
"""

import math

import numpy as np
import pandas as pd

from alpha_harness.forge import analytics as an


def _series(
    seed: int, mu: float = 0.0005, n: int = 1500, base: np.ndarray | None = None, rho: float = 0.0
) -> pd.Series:
    rng = np.random.default_rng(seed)
    x = rng.normal(mu, 0.01, n)
    if base is not None:
        x = rho * base + math.sqrt(1 - rho**2) * x
    return pd.Series(x, index=pd.bdate_range("2016-01-04", periods=n))


def test_deflated_sharpe_falls_with_trials() -> None:
    s = _series(1, mu=0.0008)
    assert an.deflated_sharpe(s, 1) > an.deflated_sharpe(s, 500)


def test_correlation_and_collision() -> None:
    a = _series(2)
    b = _series(3, base=a.to_numpy(), rho=0.9)
    assert an.correlation(a, b) > 0.8
    ok, _ = an.collision_verdict(1.0, a, {"b": b}, {"b": 1.5}, 0.7, 1.1)
    assert not ok
    ok, _ = an.collision_verdict(2.0, a, {"b": b}, {"b": 1.5}, 0.7, 1.1)
    assert ok


def test_sharpe_uses_brains_250_day_year() -> None:
    s = _series(4, mu=0.001)
    assert an.sharpe(s) == float(s.mean() / s.std() * math.sqrt(250))


def test_to_series_drops_idle_days_before_the_first_trade() -> None:
    rows = [("2020-01-01", 0.0), ("2020-01-02", 0.0), ("2020-01-03", 5.0), ("2020-01-06", -1.0)]
    assert list(an.to_series(rows)) == [5.0, -1.0]
