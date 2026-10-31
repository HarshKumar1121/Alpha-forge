"""Every template in ``config/forge/templates`` loads, fills and parses.

A broken template wastes a simulation per candidate it generates, every time the Forge runs
it — so the whole library is checked offline, slot by slot (BUILD_PLAYBOOK Phase 2).
"""

import pytest

from alpha_harness.forge.alpha_types import lint, load
from alpha_harness.forge.expr import analyse
from alpha_harness.forge.research.templates import (
    FieldInfo,
    FieldSlot,
    GroupSlot,
    Template,
    WindowSlot,
    bindings,
    expand,
    library,
    parse_template,
    render,
)

LIBRARY = library()
ALLOWED_GROUPS = {"market", "sector", "industry", "subindustry"}
ECONOMIC = set(load("regular").lint_rules["allowed_lookbacks"])


def test_library_is_not_empty_and_ids_are_unique() -> None:
    assert len(LIBRARY) >= 20
    assert len({t.id for t in LIBRARY}) == len(LIBRARY)


@pytest.mark.parametrize("t", LIBRARY, ids=[t.id for t in LIBRARY])
def test_every_fill_parses_and_lints(t: Template) -> None:
    fields = {k: f"field_{k.lower()}" for k in t.field_slots}
    for params in t.all_params():
        for sign in (1, -1):
            e = render(t, fields, params, sign=sign)
            info = analyse(e)  # raises if the template renders to something BRAIN would refuse
            assert set(fields.values()) <= set(info.fields)
            assert lint(e, load("regular")).ok, lint(e, load("regular")).errors
    assert t.rationale, "every template explains its economic idea"


@pytest.mark.parametrize("t", LIBRARY, ids=[t.id for t in LIBRARY])
def test_slot_values_are_sane(t: Template) -> None:
    for slot in t.slots.values():
        if isinstance(slot, GroupSlot):
            assert set(slot.values) <= ALLOWED_GROUPS
        elif isinstance(slot, WindowSlot):
            # Window slots hold economic values only (week, month, quarter, year…).
            assert set(slot.values) <= ECONOMIC, f"{t.id}: {slot.values}"


def test_flipping_a_multi_statement_template_negates_only_its_result() -> None:
    """Regression: wrapping the whole program in ``-( )`` does not parse, which silently
    dropped every multi-statement template from matching under an alpha type."""
    t = next(t for t in LIBRARY if t.id == "complex_residual_vs_size")
    e = render(t, {"X": "eps"}, t.default_params(), sign=-1)
    assert e.endswith("-(group_rank(r, industry))")
    assert analyse(e).fields == ("cap", "eps")


def test_vector_fields_are_aggregated_before_use() -> None:
    t = next(t for t in LIBRARY if t.id == "vector_intensity")
    e = render(t, {"X": "news_items"}, t.default_params())
    assert "ts_backfill(vec_sum(news_items), 63)" in e


def test_dataset_constraints_bind_fields() -> None:
    ratio = next(t for t in LIBRARY if t.id == "fund_ratio_two_fields")  # Y same_dataset_as X
    combo = next(t for t in LIBRARY if t.id == "combo_two_datasets")  # Y different from X
    fields = [
        FieldInfo("eps", "fundamental6", "slow"),
        FieldInfo("assets", "fundamental6", "slow"),
        FieldInfo("sent", "news1", "fast"),
    ]
    assert {(b["X"].id, b["Y"].id) for b in bindings(ratio, fields)} == {
        ("eps", "assets"),
        ("assets", "eps"),
    }
    pairs = {(b["X"].id, b["Y"].id) for b in bindings(combo, fields)}
    assert ("eps", "assets") not in pairs
    assert ("eps", "sent") in pairs
    assert all(analyse(e) for e in expand(combo, fields))


def test_archetype_filter_binds_fields() -> None:
    smoothed = next(t for t in LIBRARY if t.id == "sentiment_smoothed")  # fast fields only
    fields = [FieldInfo("eps", "f6", "slow"), FieldInfo("sent", "news1", "fast")]
    assert [b["X"].id for b in bindings(smoothed, fields)] == ["sent"]


def test_bad_templates_are_refused_with_their_id() -> None:
    with pytest.raises(ValueError, match="'t1'"):
        parse_template({"id": "t1", "expr": "rank($X", "slots": {"X": {"kind": "field"}}})
    with pytest.raises(ValueError, match="unknown kind"):
        parse_template({"id": "t2", "expr": "rank($X)", "slots": {"X": {"kind": "fld"}}})
    with pytest.raises(ValueError, match="not a field slot"):
        parse_template(
            {
                "id": "t3",
                "expr": "rank($X)",
                "slots": {"X": {"kind": "field", "same_dataset_as": "W"}},
            }
        )


def test_parse_derives_what_a_template_needs() -> None:
    t = parse_template(
        {
            "id": "t",
            "expr": "rank($X) * rank(adv20)",
            "slots": {"X": {"kind": "field", "archetype": ["slow"]}},
        }
    )
    assert t.needs == {"adv20"}
    assert isinstance(t.slots["X"], FieldSlot)
