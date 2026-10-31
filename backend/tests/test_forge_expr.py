"""The Fast Expression parser: Power Pool operator counting, locals, errors, templating.

Ported from alpha-copilot ``tests/test_core.py``.
"""

import pytest

from alpha_harness.forge.expr import ParseError, analyse, on_result, render_template


@pytest.mark.parametrize(
    ("expr", "count"),
    [
        ("rank(-ts_delta(close, 5))", 3),
        # ts_backfill is free under the Power Pool rule; the division counts.
        ("group_rank(ts_backfill(x, 60) / cap, industry)", 2),
        ("trade_when(volume > adv20, rank(vec_avg(z)), -1)", 5),
    ],
)
def test_power_pool_operator_count(expr: str, count: int) -> None:
    assert analyse(expr).operator_count == count


def test_multi_statement_and_locals() -> None:
    info = analyse("a = ts_rank(eps, 252); b = a > 0.5 ? a : -a; group_neutralize(b, subindustry)")
    assert info.fields == ("eps",)
    assert info.grouping == ("subindustry",)
    assert info.lookbacks == (252,)


def test_parse_error() -> None:
    with pytest.raises(ParseError):
        analyse("rank(close")


def test_comments_are_stripped() -> None:
    assert analyse("rank(close) # a note").fields == ("close",)


def test_depth_counts_calls_only() -> None:
    assert analyse("rank(a + b * c)").depth == 1
    assert analyse("rank(ts_mean(ts_delta(x, 5), 21))").depth == 3


def test_template() -> None:
    assert render_template("ts_mean($x1, $x)", {"x": "5", "x1": "close"}) == "ts_mean(close, 5)"
    with pytest.raises(ParseError):
        render_template("rank($f)", {})


def test_on_result_wraps_only_the_last_statement() -> None:
    def delay(x: str) -> str:
        return f"ts_delay({x}, 5)"

    assert on_result("rank(x)", delay) == "ts_delay(rank(x), 5)"
    assert on_result("a = rank(x); a * 2;", delay) == "a = rank(x); ts_delay(a * 2, 5)"
    assert analyse(on_result("a = rank(x); a * 2", delay)).fields == ("x",)
