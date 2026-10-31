"""The Gym: engine semantics, the planted effects it teaches, and no look-ahead.

Ported from alpha-copilot ``tests/test_learning.py`` (the curriculum and spaced-repetition
tests stay behind with ``learn.py``, which this phase does not port).
"""

import numpy as np
import pandas as pd
import pytest

from alpha_harness.forge.alpha_types import load
from alpha_harness.forge.local.engine import Engine, EvalError
from alpha_harness.forge.local.gym import Market, make_market
from alpha_harness.forge.local.sim import CheckLimits, SimSettings, simulate


@pytest.fixture(scope="module")
def m() -> Market:
    return make_market()


# --- engine semantics -----------------------------------------------------------------------


def _tiny() -> tuple[Engine, pd.DataFrame]:
    idx = pd.bdate_range("2020-01-01", periods=6)
    x = pd.DataFrame(
        {"A": [1, 2, 3, 4, 5, 6], "B": [6, 5, 4, 3, 2, 1], "C": [1, 1, 1, 9, 1, 1.0]}, index=idx
    )
    return Engine({"x": x}, {"grp": pd.Series({"A": 0, "B": 0, "C": 1})}), x


def test_engine_axes() -> None:
    e, _ = _tiny()
    assert e("rank(x)").iloc[0].tolist() == pytest.approx([0.5, 1.0, 0.5])  # A,C tie
    assert e("ts_delta(x, 1)")["A"].iloc[1] == 1
    assert e("ts_mean(x, 2)")["B"].iloc[1] == 5.5
    gn = e("group_neutralize(x, grp)")
    assert gn["C"].abs().max() == 0  # alone in its group
    assert gn["A"].iloc[0] == -2.5


def test_engine_decay_and_errors() -> None:
    e, _ = _tiny()
    d = e("ts_decay_linear(x, 3)")["A"].iloc[2]
    assert d == pytest.approx((1 * 1 + 2 * 2 + 3 * 3) / 6)
    with pytest.raises(EvalError):
        e("rank(nope)")
    with pytest.raises(EvalError):
        e("grp + 1")
    with pytest.raises(EvalError, match="wrong arguments"):
        e("rank(x, x, x)")


def test_trade_when() -> None:
    e, _ = _tiny()
    out = e("trade_when(x > 4, x, -1)")
    assert np.isnan(out["A"].iloc[0])
    assert out["A"].iloc[4] == 5
    assert out["C"].iloc[4] == 9  # held from day 3 when the condition was true


# --- the Gym teaches what it claims ------------------------------------------------------------


def test_planted_effects(m: Market) -> None:
    def s(e: str, **k: object):  # noqa: ANN202 - local shorthand
        return simulate(e, m.fields, m.groups, SimSettings(**k))  # pyright: ignore[reportArgumentType]

    rev = s("-ts_sum(returns, 5)")
    rev_ind = s("-ts_sum(returns, 5)", neutralization="INDUSTRY")
    assert rev_ind.stats["is"]["sharpe"] > rev.stats["is"]["sharpe"] > 1.0
    assert s("rank(ts_backfill(anl_revision, 21))").stats["is"]["sharpe"] > 1.3
    value = s("rank(book_to_price)")
    assert value.stats["is"]["sharpe"] > 0.9
    assert value.sub_universe_sharpe < 0.5  # small caps only
    sent = s("rank(news_sentiment)")
    assert sent.stats["train"]["sharpe"] > 1.5
    assert sent.stats["test"]["sharpe"] < 0.3  # died out of sample
    assert abs(s("rank(alt_3)").stats["is"]["sharpe"]) < 0.8


def test_no_lookahead(m: Market) -> None:
    """The engine can't reach the future: negative delays are refused, and today's return
    (known at the close) carries no free lunch about tomorrow's."""
    with pytest.raises(EvalError):
        simulate("ts_delay(returns, -1)", m.fields, m.groups)
    today = simulate("rank(returns)", m.fields, m.groups, diagnostics=False)
    assert abs(today.stats["is"]["sharpe"]) < 3


def test_tear_sheet_diagnostics(m: Market) -> None:
    r = simulate("rank(ts_backfill(anl_revision, 21))", m.fields, m.groups)
    assert r.ic["mean"] > 0
    assert list(r.ic_decay) == [1, 2, 5, 10, 21]
    assert r.ic_decay[1] > r.ic_decay[21]  # a slow signal still fades with the horizon
    assert len(r.quintiles) == 5
    assert r.quintiles[4] > r.quintiles[0]  # top fifth beats bottom fifth
    assert {c["name"] for c in r.checks} >= {"LOW_SHARPE", "HIGH_TURNOVER", "CONCENTRATED_WEIGHT"}


def test_check_limits_come_from_yaml() -> None:
    """CLAUDE.md rule 7: the Gym's checks read the regular type's gates, not Python constants."""
    limits = CheckLimits.from_alpha_type(load("regular"))
    assert limits == CheckLimits(1.25, 1.0, 0.01, 0.70, 0.10, 0.75)
