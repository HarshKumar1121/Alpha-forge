"""The typed-slot engine: schema validation, constrained expansion and computed alpha-type fit.

Fit is checked against hand-worked cases: if the linter and a human disagree about whether a
template fits Power Pool, one of them is wrong, and a wasted simulation is how you find out.
"""

import itertools
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from alpha_harness.forge.expr import analyse
from alpha_harness.forge.local.gym import make_market
from alpha_harness.forge.local.template_fill import GymExample, Unavailable, gym_example
from alpha_harness.forge.research.templates import (
    ConstraintError,
    FieldInfo,
    Template,
    bindings,
    expand,
    fits,
    get,
    library,
    load_library,
    parse_template,
)

if TYPE_CHECKING:
    from pathlib import Path

GOOD: dict[str, Any] = {
    "id": "good_one",
    "name": "Good",
    "category": "test/x",
    "complexity": "simple",
    "slots": {"X": {"kind": "field"}},
    "expr": "rank($X)",
    "rationale": "fine",
}


def _write(path: Path, *entries: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump({"templates": list(entries)}), encoding="utf-8")


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"rationale": None}, "bad: rationale: required"),
        ({"slots": {"X": {"kind": "fld"}}}, "bad: slots.X: kind 'fld' is not one of field"),
        ({"expr": "rank($X + $Z)"}, "bad: expr: $Z is not a declared slot"),
        ({"expr": "rank(ts_backfill($X, 5))"}, "bad: expr: ts_backfill($X…) wraps a slot"),
        (
            {"slots": {"X": {"kind": "field", "archtype": ["slow"]}}},
            "bad: slots.X: archtype: unknown key for a field slot",
        ),
        (
            {
                "slots": {"X": {"kind": "field"}, "W": {"kind": "window", "values": ["21"]}},
                "expr": "ts_mean($X, $W)",
            },
            "bad: slots.W: values: window values must be positive integers",
        ),
        ({"complexity": "hard"}, "bad: complexity: 'hard' is not one of simple, medium, complex"),
        ({"id": "good_one"}, "good_one: duplicate id (first in t.yaml)"),
    ],
    ids=[
        "missing-rationale",
        "unknown-kind",
        "undeclared-slot",
        "self-wrap",
        "typo-key",
        "string-window",
        "bad-complexity",
        "duplicate-id",
    ],
)
def test_bad_entries_become_clear_errors_not_crashes(
    tmp_path: Path, override: dict[str, Any], expected: str
) -> None:
    _write(tmp_path / "t.yaml", GOOD, {**GOOD, "id": "bad", **override})
    loaded = load_library(tmp_path)
    assert [t.id for t in loaded.templates] == ["good_one"], "siblings of a bad entry still load"
    errors = [str(e) for e in loaded.errors]
    assert any(expected in e for e in errors), errors
    assert all(e.startswith("t.yaml: ") for e in errors), "every error names its file"


def test_a_broken_file_does_not_stop_the_others(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("templates: [unclosed", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("other: 1", encoding="utf-8")
    _write(tmp_path / "c.yaml", GOOD)
    loaded = load_library(tmp_path)
    assert [t.id for t in loaded.templates] == ["good_one"]
    assert [(e.file, e.template_id) for e in loaded.errors] == [("a.yaml", None), ("b.yaml", None)]
    assert "not valid YAML" in loaded.errors[0].message
    assert "templates:" in loaded.errors[1].message


# --- expand with choices -----------------------------------------------------------------------

FIELDS = [
    FieldInfo("eps", "fundamental6", "slow"),
    FieldInfo("assets", "fundamental6", "slow"),
    FieldInfo("est_eps", "analyst4", "slow"),
    FieldInfo("sent", "news1", "fast"),
    FieldInfo("news_items", "news1", "fast", "VECTOR"),
]


def _t(template_id: str) -> Template:
    t = get(template_id)
    assert t is not None
    return t


def test_choices_narrow_the_expansion() -> None:
    t = _t("analyst_revision_group")  # X slow|medium, W 21|63, G industry|subindustry
    out = expand(t, FIELDS, {"X": "eps", "W": 21, "G": ["industry", "subindustry"]})
    assert out == [
        "group_rank(ts_delta(ts_backfill(eps, 63), 21), industry)",
        "group_rank(ts_delta(ts_backfill(eps, 63), 21), subindustry)",
    ]
    assert len(expand(t, FIELDS)) == 3 * 2 * 2  # 3 slow fields x 2 windows x 2 groups


@pytest.mark.parametrize(
    ("template_id", "choices", "match"),
    [
        ("analyst_revision_group", {"W": 10}, r"\[10\] not among the allowed 21, 63"),
        ("analyst_revision_group", {"G": "sector"}, r"\['sector'\] not among the allowed"),
        ("analyst_revision_group", {"X": "sent"}, "behaves fast; this slot takes slow, medium"),
        ("analyst_revision_group", {"X": "nope"}, "not in the field set"),
        ("analyst_revision_group", {"Q": 1}, "no slot named Q"),
        ("fund_ratio_two_fields", {"X": "eps", "Y": "est_eps"}, "must share a dataset"),
        ("combo_two_datasets", {"X": "eps", "Y": "assets"}, "must come from a different dataset"),
        ("vector_intensity", {"X": "sent"}, "is MATRIX; this slot needs a VECTOR field"),
    ],
)
def test_constraint_violations_are_rejected(
    template_id: str, choices: dict[str, object], match: str
) -> None:
    with pytest.raises(ConstraintError, match=match):
        expand(_t(template_id), FIELDS, choices)  # pyright: ignore[reportArgumentType]


def test_vector_slots_are_cleaned_with_their_aggregate() -> None:
    out = expand(_t("vector_intensity"), FIELDS, {"W": 21})
    assert out == ["rank(ts_zscore(ts_backfill(vec_sum(news_items), 63), 21))"]


def test_a_vector_field_in_an_untyped_slot_uses_the_default_aggregate() -> None:
    t = parse_template({"id": "t", "expr": "rank($X)", "slots": {"X": {"kind": "field"}}})
    assert "ts_backfill(vec_avg(news_items), 63)" in expand(t, FIELDS, {"X": "news_items"})[0]


# --- fits --------------------------------------------------------------------------------------


def test_complex_decay_blend_does_not_fit_power_pool() -> None:
    f = fits(_t("complex_decay_blend"))
    assert not f["power_pool"].ok
    assert "9 operators > 8" in f["power_pool"].errors[0]
    assert f["regular"].ok


def test_volume_conditioned_reversal_has_too_many_fields_for_power_pool() -> None:
    f = fits(_t("pv_reversal_volume_conditioned"))["power_pool"]
    assert not f.ok
    assert "4 data fields > 3" in f.errors[0]


def test_simple_templates_fit_power_pool() -> None:
    for tid in ("analyst_revision", "pv_reversal_group", "vector_intensity"):
        assert fits(_t(tid))["power_pool"].ok, tid


def test_two_dataset_templates_cannot_be_atoms() -> None:
    f = fits(_t("combo_two_datasets"))
    assert not f["atom"].ok
    assert "Single-dataset" in f["atom"].errors[0]
    # ...while a same-dataset ratio can be.
    assert fits(_t("fund_ratio_two_fields"))["atom"].ok


@pytest.mark.parametrize("t", library(), ids=[t.id for t in library()])
def test_every_template_fits_regular(t: Template) -> None:
    assert fits(t)["regular"].ok


# --- the Gym fill ------------------------------------------------------------------------------


def test_gym_examples_use_only_gym_fields_and_respect_slot_rules() -> None:
    names = set(make_market().fields) | set(make_market().groups)
    available = 0
    for t in library():
        g = gym_example(t)
        if isinstance(g, Unavailable):
            assert g.reason
            continue
        available += 1
        assert isinstance(g, GymExample)
        assert set(analyse(g.expression).fields) <= names, t.id
    assert available >= 20


def test_gym_example_reasons() -> None:
    assert "VECTOR" in str(gym_example(_t("vector_intensity")))
    assert "vwap" in str(gym_example(_t("pv_vwap_pressure")))
    assert "regression_neut" in str(gym_example(_t("complex_residual_vs_size")))
    g = gym_example(_t("sentiment_smoothed"))  # fast fields only
    assert isinstance(g, GymExample)
    assert g.fields == {"X": "news_sentiment"}


# --- semantic pairing on real-shaped field names (analyst4 / fundamental6 families) ------------
#
# Reproduces the Nov 3 incident: on a real field set, ``analyst_dispersion`` (X: "estimate std /
# dispersion field", Y: "estimate mean field", same_dataset_as: X) was filled with
# anl4_afv4_eps_high and actual_eps_value_quarterly — a stray field paired with one from an
# unrelated dataset, not a dispersion/mean pair at all.

REAL_SHAPED_FIELDS = [
    FieldInfo("anl4_eps_mean", "analyst4"),
    FieldInfo("anl4_eps_std", "analyst4"),
    FieldInfo("anl4_rev_mean", "analyst4"),
    FieldInfo("anl4_rev_std", "analyst4"),
    # A real analyst4 field that is not a mean/std pair at all.
    FieldInfo("anl4_afv4_eps_high", "analyst4"),
    # Same (or unsynced/unknown) dataset in a naive id-only reading, but not an estimate field.
    FieldInfo("actual_eps_value_quarterly", "analyst4"),
    FieldInfo("assets", "fundamental6"),
    FieldInfo("liabilities", "fundamental6"),
]


def _old_bindings(t: Template, fields: list[FieldInfo]) -> list[dict[str, FieldInfo]]:
    """The pre-fix rule: only archetype/type/dataset-equality, no semantic pairing, and an
    unknown dataset ("") compares equal to another unknown dataset instead of being rejected."""
    names = t.field_slots
    slots = {n: t.field_slot(n) for n in names}
    out: list[dict[str, FieldInfo]] = []
    for combo in itertools.permutations(fields, len(names)):
        b = dict(zip(names, combo, strict=True))
        ok = True
        for n, s in slots.items():
            f = b[n]
            same = s.same_dataset_as and b[s.same_dataset_as].dataset != f.dataset
            diff = s.different_dataset_from and b[s.different_dataset_from].dataset == f.dataset
            if same or diff:
                ok = False
                break
        if ok:
            out.append(b)
    return out


def test_analyst_dispersion_old_logic_produces_nonsense_pairs() -> None:
    """Characterizes the bug: dataset-only matching lets ANY two analyst4 fields pair up."""
    t = _t("analyst_dispersion")
    old = _old_bindings(t, REAL_SHAPED_FIELDS)
    assert len(old) == 32
    assert {"X": "anl4_afv4_eps_high", "Y": "actual_eps_value_quarterly"} in [
        {k: v.id for k, v in b.items()} for b in old
    ]


def test_analyst_dispersion_new_logic_only_pairs_mean_with_std() -> None:
    """The fix: only std/mean pairs sharing a name stem survive, within the same dataset."""
    t = _t("analyst_dispersion")
    new = bindings(t, REAL_SHAPED_FIELDS, require_archetype=False)
    got = [{k: v.id for k, v in b.items()} for b in new]
    assert got == [
        {"X": "anl4_eps_std", "Y": "anl4_eps_mean"},
        {"X": "anl4_rev_std", "Y": "anl4_rev_mean"},
    ]
    # The reported nonsense pair must be gone.
    assert {"X": "anl4_afv4_eps_high", "Y": "actual_eps_value_quarterly"} not in got
    assert {"X": "anl4_eps_std", "Y": "anl4_rev_mean"} not in got  # cross-field: stems differ


def test_unknown_dataset_never_silently_matches() -> None:
    """Two fields the catalog never synced a dataset for (dataset="") must not be treated as
    'same dataset' just because both are blank — same_dataset_as should reject them."""
    t = _t("fund_ratio_two_fields")  # X, Y: same_dataset_as, no semantic hint
    unsynced = [FieldInfo("eps_mean", ""), FieldInfo("eps_std", "")]
    assert bindings(t, unsynced, require_archetype=False) == []
