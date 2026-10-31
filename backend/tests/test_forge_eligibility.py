"""Per-alpha eligibility: grouping fields, pv1 operators, and the limits each type sets.

Every rule is checked against a hand-written alpha type so the expected answer can be read off
the YAML, and against the shipped ``power_pool`` / ``atom`` files so a change to those is not
silently fine.
"""

from typing import Any

import pytest

from alpha_harness.forge.alpha_types import AlphaType, load_all
from alpha_harness.forge.research.eligibility import (
    data_field_count,
    datasets_used,
    judge,
    judge_all,
    pv1_operators_used,
)

CATALOG: dict[str, dict[str, Any]] = {
    "close": {"dataset": "pv1", "type": "MATRIX", "coverage": 1.0},
    "volume": {"dataset": "pv1", "type": "MATRIX", "coverage": 1.0},
    "anl4_eps_mean": {"dataset": "analyst4", "type": "MATRIX", "coverage": 0.8},
    "anl4_rev_mean": {"dataset": "analyst4", "type": "MATRIX", "coverage": 0.8},
    "news_sent": {"dataset": "news12", "type": "MATRIX", "coverage": 0.6},
}


def a_type(name: str, **lint: Any) -> AlphaType:
    return AlphaType(name, {"simulation": {"type": "REGULAR", "settings": {}}, "lint": lint})


def test_grouping_fields_are_not_data() -> None:
    """``industry`` sets the peer set; BRAIN does not count it toward the field limit."""
    one_field = "group_rank(ts_delta(anl4_eps_mean, 21), industry)"
    verdict = judge(one_field, a_type("pp", max_fields=1), catalog=CATALOG)
    assert verdict.ok, verdict.reasons


def test_the_field_limit_counts_the_rest() -> None:
    two = "group_rank(anl4_eps_mean / anl4_rev_mean, subindustry)"
    verdict = judge(two, a_type("pp", max_fields=1), catalog=CATALOG)
    assert not verdict.ok
    assert "2 data fields > 1" in verdict.reasons[0]


def test_a_pv1_operator_counts_as_using_pv1() -> None:
    assert pv1_operators_used("rank(inst_pnl(anl4_eps_mean))") == ("inst_pnl",)
    used, pv1 = datasets_used("rank(inst_pnl(anl4_eps_mean))", CATALOG)
    assert used == {"analyst4", "pv1"}
    assert pv1 == ("inst_pnl",)


def test_a_pv1_operator_breaks_a_single_dataset_type() -> None:
    """ATOM takes one dataset; ``inst_pnl`` reads pv1 however it is called."""
    atom = a_type("atom", single_dataset=True)
    plain = judge("rank(ts_delta(anl4_eps_mean, 21))", atom, catalog=CATALOG)
    assert plain.ok, plain.reasons

    with_pv1 = judge("rank(inst_pnl(anl4_eps_mean))", atom, catalog=CATALOG)
    assert not with_pv1.ok
    assert "inst_pnl" in with_pv1.reasons[0]
    assert "pv1" in with_pv1.reasons[0]


def test_a_pv1_operator_on_pv1_data_is_still_one_dataset() -> None:
    verdict = judge("rank(inst_pnl(close))", a_type("atom", single_dataset=True), catalog=CATALOG)
    assert verdict.ok, verdict.reasons


def test_a_pv1_operator_takes_a_field_slot() -> None:
    """Three written fields plus the pv1 an operator reads is four: over Power Pool's limit."""
    three = "rank(anl4_eps_mean) + rank(anl4_rev_mean) + rank(news_sent)"
    assert data_field_count(three) == 3
    assert judge(three, a_type("pp", max_fields=3), catalog=CATALOG).ok

    with_pv1 = f"convert({three})"
    assert data_field_count(with_pv1) == 4
    verdict = judge(with_pv1, a_type("pp", max_fields=3), catalog=CATALOG)
    assert not verdict.ok
    assert "4 data fields > 3" in verdict.reasons[0]
    assert "convert" in verdict.reasons[0]


def test_pv1_usage_within_the_limit_is_only_a_warning() -> None:
    verdict = judge("rank(convert(anl4_eps_mean))", a_type("pp", max_fields=3), catalog=CATALOG)
    assert verdict.ok
    assert any("2 of 3 data fields" in w for w in verdict.warnings)


def test_operator_count_ignores_the_backfill_the_engine_adds() -> None:
    """``ts_backfill`` is BRAIN's own exception; a template never spends a slot on it."""
    expression = "rank(ts_delta(ts_backfill(anl4_eps_mean, 63), 21))"
    verdict = judge(expression, a_type("pp", max_operators=2), catalog=CATALOG)
    assert verdict.ok, verdict.reasons


def test_an_unknown_field_is_refused_rather_than_guessed() -> None:
    verdict = judge("rank(not_a_field)", a_type("pp", max_fields=3), catalog=CATALOG)
    assert not verdict.ok
    assert "Unknown fields" in verdict.reasons[0]


def test_a_vector_field_must_be_aggregated() -> None:
    catalog = {**CATALOG, "anl4_all": {"dataset": "analyst4", "type": "VECTOR"}}
    bad = judge("rank(anl4_all)", a_type("pp"), catalog=catalog)
    assert not bad.ok
    assert "VECTOR" in bad.reasons[0]
    good = judge("rank(vec_avg(anl4_all))", a_type("pp"), catalog=catalog)
    assert good.ok, good.reasons


def test_syntax_is_reported_as_a_reason_not_a_crash() -> None:
    verdict = judge("rank(close", a_type("pp"), catalog=CATALOG)
    assert not verdict.ok
    assert "Syntax" in verdict.reasons[0]


@pytest.mark.parametrize(
    ("expression", "fits_power_pool"),
    [
        ("group_rank(ts_delta(anl4_eps_mean, 21), industry)", True),
        # Four written fields is one more than Power Pool allows.
        (
            "rank(anl4_eps_mean) + rank(anl4_rev_mean) + rank(news_sent) + rank(close)",
            False,
        ),
    ],
)
def test_against_the_shipped_alpha_types(expression: str, fits_power_pool: bool) -> None:
    verdicts = judge_all(expression, load_all(), catalog=CATALOG, target_dataset="analyst4")
    assert verdicts["power_pool"].ok is fits_power_pool
    if not fits_power_pool:
        assert verdicts["power_pool"].reasons


def test_every_shipped_type_is_judged() -> None:
    verdicts = judge_all("rank(anl4_eps_mean)", load_all(), catalog=CATALOG)
    assert {"regular", "power_pool", "atom"} <= set(verdicts)
    assert all(v.alpha_type == name for name, v in verdicts.items())
