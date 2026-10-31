"""My history per dataset: fields read off expressions, BRAIN's verdict as the pass rate."""

import json
from typing import Any

from alpha_harness.forge.data.usage import dataset_usage

DATASETS_OF = {
    "close": {"pv1"},
    "volume": {"pv1"},
    "anl4_eps_mean": {"analyst4"},
    "revenue": {"fundamental6", "fundamental23"},
}

PASS = json.dumps([{"name": "LOW_SHARPE", "result": "PASS"}])
FAIL = json.dumps([{"name": "LOW_SHARPE", "result": "FAIL"}])
PENDING = json.dumps([{"name": "SELF_CORRELATION", "result": "PENDING"}])


def _alpha(expr: str, checks: str | None, universe: str = "TOP3000", **kw: Any) -> dict[str, Any]:
    return {"expression": expr, "universe": universe, "checks": checks, **kw}


def test_pass_rate_counts_only_alphas_brain_has_decided() -> None:
    usage = dataset_usage(
        [
            _alpha("rank(close)", PASS),
            _alpha("rank(-close)", FAIL),
            _alpha("ts_rank(close, 20)", PENDING),
            _alpha("zscore(close)", None),
        ],
        [],
        DATASETS_OF,
    )
    pv1 = usage.datasets["pv1"]
    assert (pv1.alphas, pv1.checked, pv1.passed) == (4, 2, 1)
    assert pv1.pass_rate == 0.5


def test_quick_mode_alphas_count_as_refused() -> None:
    usage = dataset_usage([_alpha("rank(close)", PASS, simulation_mode="QUICK")], [], DATASETS_OF)
    assert usage.datasets["pv1"].pass_rate == 0.0


def test_an_alpha_counts_once_per_dataset_it_reads() -> None:
    usage = dataset_usage([_alpha("rank(close) * rank(anl4_eps_mean)", PASS)], [], DATASETS_OF)
    assert usage.datasets["pv1"].alphas == 1
    assert usage.datasets["analyst4"].alphas == 1
    assert usage.fields["close"].passed == 1


def test_a_field_filed_under_two_datasets_counts_toward_both() -> None:
    usage = dataset_usage([_alpha("rank(revenue)", PASS)], [], DATASETS_OF)
    assert set(usage.datasets) == {"fundamental6", "fundamental23"}


def test_grouping_fields_and_locals_are_not_data() -> None:
    usage = dataset_usage(
        [_alpha("x = rank(close); group_neutralize(x, industry)", PASS)], [], DATASETS_OF
    )
    assert set(usage.fields) == {"close"}


def test_sims_are_counted_per_dataset_and_universe() -> None:
    usage = dataset_usage(
        [],
        [
            {"expression": "rank(close)", "universe": "TOP3000"},
            {"expression": "rank(volume)", "universe": "TOP1000"},
            {"expression": "rank(anl4_eps_mean)", "universe": "TOP3000"},
        ],
        DATASETS_OF,
    )
    pv1 = usage.datasets["pv1"]
    assert pv1.sims == 2
    assert {u: t.sims for u, t in pv1.by_universe.items()} == {"TOP3000": 1, "TOP1000": 1}
    assert pv1.alphas == 0
    assert pv1.pass_rate is None


def test_structural_families_are_distinct_field_combinations() -> None:
    usage = dataset_usage(
        [
            _alpha("rank(close)", PASS),
            _alpha("ts_rank(close, 60)", FAIL),  # same field: same structural family
            _alpha("rank(close / volume)", PASS),
        ],
        [],
        DATASETS_OF,
    )
    assert len(usage.datasets["pv1"].families) == 2


def test_unparseable_expressions_are_skipped_and_counted() -> None:
    usage = dataset_usage(
        [_alpha("rank(close", PASS), _alpha("", PASS)],
        [{"expression": "((", "universe": "TOP3000"}],
        DATASETS_OF,
    )
    assert usage.unparsed == 3
    assert usage.datasets == {}


def test_fields_not_in_the_catalog_touch_no_dataset() -> None:
    usage = dataset_usage([_alpha("rank(some_unsynced_field)", PASS)], [], DATASETS_OF)
    assert usage.datasets == {}
    assert usage.fields["some_unsynced_field"].alphas == 1
