"""The Power Pool description draft: BRAIN's template, filled from what the Forge knows."""

from typing import Any

from alpha_harness.brain.platform import POWER_POOL_DESCRIPTION_HEADINGS
from alpha_harness.forge.research.description import compose, draft
from alpha_harness.forge.research.templates import parse_template
from alpha_harness.tools.description import short

FIELDS: dict[str, dict[str, Any]] = {
    "anl4_eps_mean": {
        "dataset": "analyst4",
        "description": (
            "The mean of every broker's earnings-per-share estimate for the current "
            "fiscal year, refreshed as brokers publish revisions"
        ),
    },
    "close": {"dataset": "pv1", "description": "The day's closing price"},
}

OPERATORS: dict[str, dict[str, Any]] = {
    "group_rank": {
        "category": "Group",
        "description": "Ranks each instrument against the others in its group",
    },
    "ts_delta": {
        "category": "Time Series",
        "description": "The change in a value over the last d days",
    },
    "add": {"category": "Arithmetic", "description": "x + y"},
}

TEMPLATE = parse_template(
    {
        "id": "analyst_revision_group",
        "name": "Revision against peers",
        "category": "analyst/revision",
        "complexity": "simple",
        "angle": "change",
        "rationale": (
            "Brokers revise estimates in steps rather than all at once, so a company whose "
            "mean estimate has risen faster than its industry peers over the last quarter "
            "tends to keep being revised upward for several weeks"
        ),
        "slots": {
            "X": {"kind": "field"},
            "W": {"kind": "window", "values": [63]},
            "G": {"kind": "group", "values": ["industry"]},
        },
        "expr": "group_rank(ts_delta($X, $W), $G)",
    }
)

EXPRESSION = "group_rank(ts_delta(ts_backfill(anl4_eps_mean, 63), 63), industry)"


def test_the_draft_uses_brains_three_headings_in_order() -> None:
    d = draft(TEMPLATE, EXPRESSION, fields=FIELDS, operators=OPERATORS)
    lines = d.text.splitlines()
    assert len(lines) == 3
    for heading, line in zip(POWER_POOL_DESCRIPTION_HEADINGS, lines, strict=True):
        assert line.startswith(f"{heading}: ")
        assert line[len(heading) + 2 :].strip()


def test_brains_own_reader_accepts_the_draft() -> None:
    """``short`` parses with the same pattern BRAIN's client checks; nothing may be thin."""
    d = draft(TEMPLATE, EXPRESSION, fields=FIELDS, operators=OPERATORS)
    assert short(d.text) == []
    assert d.thin == ()


def test_the_idea_comes_from_the_templates_rationale() -> None:
    d = draft(TEMPLATE, EXPRESSION, fields=FIELDS, operators=OPERATORS)
    assert "Brokers revise estimates in steps" in d.idea
    assert "change angle" in d.idea


def test_the_data_part_describes_each_field_it_reads() -> None:
    d = draft(TEMPLATE, EXPRESSION, fields=FIELDS, operators=OPERATORS)
    assert "anl4_eps_mean" in d.data
    assert "dataset analyst4" in d.data
    assert "every broker's earnings-per-share estimate" in d.data
    assert "close" not in d.data  # not used by this expression


def test_grouping_fields_are_named_as_peers_not_data() -> None:
    d = draft(TEMPLATE, EXPRESSION, fields=FIELDS, operators=OPERATORS)
    assert "industry" in d.data
    assert "not data BRAIN counts" in d.data


def test_arithmetic_is_left_out_of_the_operator_part() -> None:
    d = draft(
        TEMPLATE,
        "group_rank(ts_delta(anl4_eps_mean, 63) + 1, industry)",
        fields=FIELDS,
        operators=OPERATORS,
    )
    assert "group_rank" in d.operators
    assert "ts_delta" in d.operators
    assert "add" not in d.operators


def test_an_operator_brain_does_not_describe_is_still_named() -> None:
    d = draft(TEMPLATE, EXPRESSION, fields=FIELDS, operators={})
    assert "group_rank" in d.operators
    assert "ts_delta" in d.operators


def test_a_field_without_a_description_says_so_rather_than_inventing_one() -> None:
    d = draft(
        TEMPLATE,
        "rank(mystery_field)",
        fields={"mystery_field": {"dataset": "x1"}},
        operators=OPERATORS,
    )
    assert "No description is published" in d.data


def test_a_thin_draft_reports_which_heading_needs_work() -> None:
    bare = parse_template(
        {
            "id": "bare",
            "name": "Bare",
            "category": "x",
            "complexity": "simple",
            "rationale": "Short.",
            "slots": {"X": {"kind": "field"}},
            "expr": "rank($X)",
        }
    )
    d = draft(bare, "rank(mystery)", fields={"mystery": {"dataset": "x1"}}, operators={})
    assert d.thin
    assert set(d.thin) <= set(POWER_POOL_DESCRIPTION_HEADINGS)


def test_an_idea_can_be_written_by_hand() -> None:
    mine = "I think estimate revisions drift, and this measures the drift against peers."
    d = draft(None, EXPRESSION, fields=FIELDS, operators=OPERATORS, idea=mine)
    assert d.idea.startswith("I think estimate revisions drift")


def test_compose_collapses_whitespace_so_the_template_stays_three_lines() -> None:
    text = compose("An   idea\nwith a break", "data", "operators")
    assert text.splitlines()[0] == "Idea: An idea with a break"
    assert len(text.splitlines()) == 3
