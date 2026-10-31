"""Binding a template to real-shaped fields: slot rules, scope limits, and provenance."""

from typing import Any

import pytest

from alpha_harness.forge.research.binding import (
    ScopeData,
    available_groups,
    bind,
    field_infos,
    narrow_to_scope,
    scope_catalog,
)
from alpha_harness.forge.research.templates import parse_template

#: Catalog rows as the Data Explorer serves them.
ROWS: list[dict[str, Any]] = [
    {
        "field_id": "anl4_eps_mean",
        "dataset_id": "analyst4",
        "field_type": "MATRIX",
        "coverage": 0.8,
        "description": "Mean EPS estimate",
    },
    {
        "field_id": "anl4_rev_mean",
        "dataset_id": "analyst4",
        "field_type": "MATRIX",
        "coverage": 0.7,
        "description": "Mean revenue estimate",
    },
    {
        "field_id": "fnd6_assets",
        "dataset_id": "fundamental6",
        "field_type": "MATRIX",
        "coverage": 0.9,
        "description": "Total assets",
    },
    {
        "field_id": "anl4_all",
        "dataset_id": "analyst4",
        "field_type": "VECTOR",
        "coverage": 0.5,
        "description": "Every estimate",
    },
]

#: What the market carries beyond the field set: grouping fields and the template's `needs`.
SCOPE_ROWS: list[dict[str, Any]] = [
    {"field_id": g, "dataset_id": "pv1", "field_type": "GROUP"}
    for g in ("industry", "subindustry", "sector")
] + [{"field_id": "adv20", "dataset_id": "pv1", "field_type": "MATRIX"}]


def scope(rows: list[dict[str, Any]] = SCOPE_ROWS) -> ScopeData:
    catalog = scope_catalog(ROWS, rows)
    return ScopeData(catalog=catalog, groups=available_groups(catalog))


def template(**overrides: Any) -> Any:
    raw: dict[str, Any] = {
        "id": "t1",
        "name": "Revision vs peers",
        "category": "analyst/revision",
        "complexity": "simple",
        "angle": "change",
        "rationale": "An estimate revised up ahead of its peers tends to keep going.",
        "slots": {
            "X": {"kind": "field"},
            "W": {"kind": "window", "values": [21, 63]},
            "G": {"kind": "group", "values": ["industry", "subindustry"]},
        },
        "expr": "group_rank(ts_delta($X, $W), $G)",
    }
    raw.update(overrides)
    return parse_template(raw)


def test_every_field_window_and_group_combination_renders() -> None:
    matrix = [r for r in ROWS if r["field_type"] == "MATRIX"]
    result = bind(template(), field_infos(matrix), scope())
    # 3 fields x 2 windows x 2 groups.
    assert result.total == 12
    assert not result.blockers
    assert len(result.rendered) == 12


def test_each_alpha_says_which_field_and_window_made_it() -> None:
    one = [r for r in ROWS if r["field_id"] == "anl4_eps_mean"]
    result = bind(template(), field_infos(one), scope(), choices={"W": 21, "G": "industry"})
    (alpha,) = result.rendered
    assert alpha.fields == {"X": "anl4_eps_mean"}
    assert alpha.params == {"W": 21, "G": "industry"}
    assert "ts_delta" in alpha.expression
    assert "industry" in alpha.expression
    assert alpha.data_fields == ("anl4_eps_mean",)
    assert alpha.datasets == ("analyst4",)


def test_a_field_is_cleaned_before_the_template_sees_it() -> None:
    result = bind(template(), field_infos(ROWS[:1]), scope())
    assert "ts_backfill(anl4_eps_mean, 63)" in result.rendered[0].expression


def test_a_vector_field_is_aggregated_first() -> None:
    vector = [r for r in ROWS if r["field_type"] == "VECTOR"]
    result = bind(template(), field_infos(vector), scope())
    assert "vec_avg(anl4_all)" in result.rendered[0].expression


def test_a_matrix_slot_refuses_a_vector_field() -> None:
    t = parse_template(
        {
            "id": "t2",
            "name": "Matrix only",
            "category": "x",
            "complexity": "simple",
            "rationale": "Needs a matrix field.",
            "slots": {
                "X": {"kind": "field", "type": "MATRIX"},
                "W": {"kind": "window", "values": [21]},
            },
            "expr": "ts_delta($X, $W)",
        }
    )
    vector = [r for r in ROWS if r["field_type"] == "VECTOR"]
    result = bind(t, field_infos(vector), scope())
    assert result.blockers
    assert "slot rules" in result.blockers[0]


def test_an_unprofiled_field_can_still_fill_an_archetype_slot() -> None:
    """Real BRAIN fields are not profiled; an archetype slot must not empty the Studio."""
    t = parse_template(
        {
            "id": "t3",
            "name": "Slow fields",
            "category": "x",
            "complexity": "simple",
            "rationale": "Slow data moves in steps.",
            "slots": {
                "X": {"kind": "field", "archetype": ["slow"]},
                "W": {"kind": "window", "values": [63]},
            },
            "expr": "ts_delta($X, $W)",
        }
    )
    result = bind(t, field_infos(ROWS[:1]), scope())
    assert result.rendered, result.blockers


def test_a_profiled_field_is_still_held_to_the_archetype() -> None:
    t = parse_template(
        {
            "id": "t4",
            "name": "Slow fields",
            "category": "x",
            "complexity": "simple",
            "rationale": "Slow data moves in steps.",
            "slots": {
                "X": {"kind": "field", "archetype": ["slow"]},
                "W": {"kind": "window", "values": [63]},
            },
            "expr": "ts_delta($X, $W)",
        }
    )
    fast = field_infos(ROWS[:1], {"anl4_eps_mean": "fast"})
    assert bind(t, fast, scope()).blockers
    slow = field_infos(ROWS[:1], {"anl4_eps_mean": "slow"})
    assert bind(t, slow, scope()).rendered


def test_a_group_the_market_lacks_is_dropped_with_a_note() -> None:
    thin = [r for r in SCOPE_ROWS if r["field_id"] != "subindustry"]
    result = bind(template(), field_infos(ROWS[:1]), scope(thin))
    assert result.offered["G"] == ["industry"]
    assert any("subindustry" in n for n in result.notes)
    assert all("subindustry" not in a.expression for a in result.rendered)


def test_a_template_needing_a_group_the_market_lacks_is_blocked() -> None:
    t = parse_template(
        {
            "id": "t5",
            "name": "Subindustry only",
            "category": "x",
            "complexity": "simple",
            "rationale": "Compares inside a subindustry.",
            "slots": {
                "X": {"kind": "field"},
                "G": {"kind": "group", "values": ["subindustry"]},
            },
            "expr": "group_rank($X, $G)",
        }
    )
    thin = [r for r in SCOPE_ROWS if r["field_id"] != "subindustry"]
    result = bind(t, field_infos(ROWS[:1]), scope(thin))
    assert result.blockers
    assert "$G" in result.blockers[0]


def test_a_field_the_expression_names_must_exist_in_the_market() -> None:
    t = parse_template(
        {
            "id": "t6",
            "name": "Needs adv20",
            "category": "x",
            "complexity": "simple",
            "rationale": "Scales by liquidity.",
            "slots": {"X": {"kind": "field"}},
            "expr": "$X / adv20",
        }
    )
    assert not bind(t, field_infos(ROWS[:1]), scope()).blockers
    without = [r for r in SCOPE_ROWS if r["field_id"] != "adv20"]
    result = bind(t, field_infos(ROWS[:1]), scope(without))
    assert result.blockers
    assert "adv20" in result.blockers[0]


def test_the_same_dataset_rule_is_honoured() -> None:
    t = parse_template(
        {
            "id": "t7",
            "name": "Two from one dataset",
            "category": "x",
            "complexity": "medium",
            "rationale": "Two measures of the same thing.",
            "slots": {
                "X": {"kind": "field"},
                "Y": {"kind": "field", "same_dataset_as": "X"},
            },
            "expr": "rank($X) - rank($Y)",
        }
    )
    matrix = [r for r in ROWS if r["field_type"] == "MATRIX"]
    result = bind(t, field_infos(matrix), scope())
    for alpha in result.rendered:
        assert alpha.datasets == ("analyst4",), alpha.expression


def test_the_different_dataset_rule_is_honoured() -> None:
    t = parse_template(
        {
            "id": "t8",
            "name": "Across datasets",
            "category": "x",
            "complexity": "medium",
            "rationale": "One dataset confirms the other.",
            "slots": {
                "X": {"kind": "field"},
                "Y": {"kind": "field", "different_dataset_from": "X"},
            },
            "expr": "rank($X) - rank($Y)",
        }
    )
    matrix = [r for r in ROWS if r["field_type"] == "MATRIX"]
    result = bind(t, field_infos(matrix), scope())
    assert result.rendered
    for alpha in result.rendered:
        assert len(alpha.datasets) == 2, alpha.expression


def test_an_empty_field_set_is_a_blocker_not_an_empty_list() -> None:
    result = bind(template(), [], scope())
    assert result.blockers
    assert "no fields" in result.blockers[0]


def test_an_illegal_pin_says_which_rule_it_breaks() -> None:
    result = bind(template(), field_infos(ROWS[:1]), scope(), choices={"W": 999})
    assert result.blockers
    assert "999" in result.blockers[0]


def test_the_expansion_is_capped_and_says_so() -> None:
    matrix = [r for r in ROWS if r["field_type"] == "MATRIX"]
    result = bind(template(), field_infos(matrix), scope(), limit=5)
    assert result.total == 12
    assert len(result.rendered) == 5
    assert any("showing the first 5" in n for n in result.notes)


def test_market_is_always_a_group() -> None:
    assert "market" in available_groups({})


@pytest.mark.parametrize("missing", ["industry", "sector"])
def test_narrowing_reports_each_dropped_group(missing: str) -> None:
    rows = [r for r in SCOPE_ROWS if r["field_id"] != missing]
    t = parse_template(
        {
            "id": "t9",
            "name": "Any group",
            "category": "x",
            "complexity": "simple",
            "rationale": "Compares within a peer set.",
            "slots": {
                "X": {"kind": "field"},
                "G": {"kind": "group", "values": ["industry", "sector"]},
            },
            "expr": "group_rank($X, $G)",
        }
    )
    offered, notes = narrow_to_scope(t, scope(rows))
    assert missing not in offered["G"]
    assert any(missing in n for n in notes)
