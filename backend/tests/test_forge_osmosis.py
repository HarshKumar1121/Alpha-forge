"""Offline tests for the Osmosis Allocator, Part A.

Synthetic PnL only. No BRAIN client, no simulator, no network: the package itself must not
import any of them (see ``test_package_never_imports_brain_or_simulator``).
"""

import ast
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from alpha_harness.brain.platform import IGNORED_CHECKS
from alpha_harness.forge.osmosis import scope as scope_mod
from alpha_harness.forge.osmosis.eligibility import OsmosisStatus, osmosis_status
from alpha_harness.forge.osmosis.plan import build_plan, plan_all, to_csv
from alpha_harness.forge.osmosis.points import bound_weights, to_points
from alpha_harness.forge.osmosis.selection import greedy_select, min_combination_size, sharpe
from alpha_harness.forge.osmosis.settings import Settings, from_mapping, load_settings
from alpha_harness.forge.osmosis.walkforward import (
    Judgement,
    WalkForward,
    fold_spans,
    judge,
    run_walkforward,
    stationary_bootstrap_indices,
)
from alpha_harness.forge.osmosis.weights import METHODS, Allocation, allocate, ledoit_wolf_cov
from alpha_harness.vault.yields import gating_results

REPO = Path(__file__).resolve().parents[2]


def small_settings(**overrides: object) -> Settings:
    """Settings small enough for fast tests; each test overrides what it needs."""
    base: dict[str, object] = {
        "train_days": 200,
        "test_days": 60,
        "step": 60,
        "bootstrap_samples": 200,
        "bootstrap_block": 10.0,
        # forge: fewer resamples keep the walk-forward fast; these tests check plumbing, not the
        # estimate. The production value (200) is tested in test_forge_osmosis_robust.py.
        "resample_count": 20,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def synthetic_pnl(
    n_days: int,
    means: list[float],
    vols: list[float],
    rho: float = 0.0,
    seed: int = 0,
    names: list[str] | None = None,
) -> pd.DataFrame:
    """Daily PnL with the given mean and volatility per Alpha, sharing a common factor ``rho``."""
    rng = np.random.default_rng(seed)
    common = rng.normal(size=n_days)
    cols = {}
    for j, (m, s) in enumerate(zip(means, vols, strict=True)):
        idio = rng.normal(size=n_days)
        cols[names[j] if names else f"A{j}"] = m + s * (rho * common + math.sqrt(1 - rho**2) * idio)
    index = pd.bdate_range(end="2026-06-30", periods=n_days)
    return pd.DataFrame(cols, index=index)


def statuses_for(ids: list[str], result: str = "PASS") -> dict[str, OsmosisStatus]:
    checks = [{"name": "OSMOSIS_ALLOCATION", "result": result}]
    return {a: osmosis_status(checks) for a in ids}


# --- eligibility ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("checks", "status", "eligible"),
    [
        ([{"name": "OSMOSIS_ALLOCATION", "result": "PASS"}], "eligible", True),
        ([{"name": "osmosis_allocation", "result": "pass"}], "eligible", True),
        ([{"name": "OSMOSIS_ALLOCATION", "result": "FAIL"}], "refused", False),
        ([{"name": "OSMOSIS_ALLOCATION", "result": "ERROR"}], "refused", False),
        ([{"name": "OSMOSIS_ALLOCATION", "result": "WARNING"}], "warning", False),
        ([{"name": "OSMOSIS_ALLOCATION", "result": "PENDING"}], "pending", False),
        ([{"name": "OSMOSIS_ALLOCATION", "result": None}], "pending", False),
        ([{"name": "OSMOSIS_ALLOCATION", "result": "SOMETHING_NEW"}], "unknown", False),
        ([], "absent", False),
        ([{"name": "PROD_CORRELATION", "result": "PASS"}], "absent", False),
        # Matched whole, so a longer name is a different check.
        ([{"name": "OSMOSIS_ALLOCATION_X", "result": "PASS"}], "absent", False),
        (None, "absent", False),
        # The last entry wins, as a re-check overwrites the earlier one.
        (
            [
                {"name": "OSMOSIS_ALLOCATION", "result": "FAIL"},
                {"name": "OSMOSIS_ALLOCATION", "result": "PASS"},
            ],
            "eligible",
            True,
        ),
    ],
)
def test_only_pass_is_eligible(
    checks: list[dict[str, object]] | None, status: str, eligible: bool
) -> None:
    got = osmosis_status(checks)  # type: ignore[arg-type]
    assert got.status == status
    assert got.eligible is eligible
    assert got.reason  # every status carries a plain reason for the screen


def test_gate_is_separate_from_the_vault_verdict() -> None:
    """OSMOSIS_ALLOCATION is an ignored label, so the vault's gating never sees it. The
    allocator must not reuse that path: an Alpha BRAIN says failed Osmosis would otherwise look
    eligible."""
    assert "OSMOSIS_ALLOCATION" in IGNORED_CHECKS
    checks = [
        {"name": "OSMOSIS_ALLOCATION", "result": "FAIL"},
        {"name": "MAX_TRADE", "result": "PASS"},
    ]
    assert "FAIL" not in gating_results(checks)
    assert not osmosis_status(checks).eligible


# --- settings ---------------------------------------------------------------------------


def test_repo_config_loads_with_design_defaults() -> None:
    s = load_settings(REPO / "config" / "forge" / "osmosis.yaml")
    assert (s.min_eligible, s.max_selected, s.corr_cap) == (10, 10, 0.7)
    assert (s.train_days, s.test_days, s.step) == (504, 126, 126)
    assert (s.floor, s.cap, s.total_points) == (0.01, 0.25, 100_000)


def test_unknown_settings_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown keys"):
        from_mapping({"cap_typo": 0.5})


@pytest.mark.parametrize("raw", [{"cap": 0.0}, {"floor": 0.3, "cap": 0.2}, {"corr_cap": 1.5}])
def test_invalid_settings_are_rejected(raw: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        from_mapping(raw)


# --- selection --------------------------------------------------------------------------


def test_sharpe_is_scale_free_and_nan_when_flat() -> None:
    x = np.array([0.1, -0.2, 0.3, 0.05, 0.2])
    assert sharpe(x, 252) == pytest.approx(sharpe(x * 1000, 252))
    assert math.isnan(sharpe(np.ones(10), 252))
    assert math.isnan(sharpe(np.array([1.0]), 252))


def test_greedy_skips_a_near_duplicate_under_the_correlation_cap() -> None:
    base = synthetic_pnl(
        400, [0.5, 0.5, 0.2], [1.0, 1.0, 1.0], rho=0.0, seed=1, names=["A", "A2", "B"]
    )
    base["A2"] = base["A"] + 0.01 * np.random.default_rng(2).normal(size=len(base))
    chosen = greedy_select(base, small_settings(cap=0.5, max_selected=3, corr_cap=0.7))
    assert "A" in chosen
    assert not ({"A", "A2"} <= set(chosen))


def test_greedy_fills_to_the_minimum_size_even_without_gain() -> None:
    # Four Alphas with no edge at all: the cap needs four, so the minimum still applies.
    pnl = synthetic_pnl(300, [0.0] * 4, [1.0] * 4, seed=3, names=["p", "q", "r", "s"])
    settings = small_settings(cap=0.25, max_selected=10, min_sharpe_gain=1.0)
    assert min_combination_size(settings) == 4
    assert len(greedy_select(pnl, settings)) == 4


def test_greedy_stops_when_no_candidate_adds_sharpe() -> None:
    pnl = synthetic_pnl(300, [1.0, 0.0, 0.0, 0.0, 0.0], [1.0] * 5, seed=4)
    chosen = greedy_select(pnl, small_settings(cap=0.5, max_selected=5, min_sharpe_gain=0.5))
    assert chosen[0] == "A0"
    assert len(chosen) < 5


# --- weights ----------------------------------------------------------------------------


def _five_alpha_train() -> pd.DataFrame:
    return synthetic_pnl(
        600, [0.3, 0.2, 0.1, 0.25, 0.05], [0.5, 1.0, 1.5, 0.7, 2.0], rho=0.3, seed=5
    )


@pytest.mark.parametrize("method", METHODS)
def test_every_method_sums_to_one_and_respects_bounds(method: str) -> None:
    train = _five_alpha_train()
    result = allocate(method, train, small_settings(cap=0.4, floor=0.02))  # type: ignore[arg-type]
    w = result.weights
    assert list(w.index) == list(train.columns)
    assert w.sum() == pytest.approx(1.0, abs=1e-9)
    assert (w >= 0.02 - 1e-9).all() and (w <= 0.4 + 1e-9).all()


def test_equal_weight_is_one_over_n() -> None:
    w = allocate("equal_weight", _five_alpha_train(), small_settings(cap=0.5)).weights
    assert np.allclose(w.to_numpy(), 0.2)


def test_inverse_volatility_gives_steadier_alphas_more_weight() -> None:
    train = _five_alpha_train()
    w = allocate("inverse_volatility", train, small_settings(cap=0.5)).weights
    assert w["A0"] > w["A4"]  # A0 has the smallest volatility, A4 the largest


def test_min_variance_beats_equal_weight_on_its_training_variance() -> None:
    train = _five_alpha_train()
    settings = small_settings(cap=0.5)
    sample_cov = np.cov(train.to_numpy(), rowvar=False)
    mv = allocate("min_variance", train, settings).weights.to_numpy()
    ew = np.full(5, 0.2)
    assert mv @ sample_cov @ mv <= ew @ sample_cov @ ew


def test_ledoit_wolf_is_positive_semidefinite_and_shrinks_toward_target() -> None:
    rng = np.random.default_rng(6)
    x = rng.normal(size=(60, 12))  # few days, many Alphas: the case shrinkage exists for
    cov = ledoit_wolf_cov(x)
    assert np.allclose(cov, cov.T)
    assert np.linalg.eigvalsh(cov).min() > -1e-10
    many = rng.normal(size=(20000, 3))
    sample = np.cov(many, rowvar=False, bias=True)
    assert np.allclose(ledoit_wolf_cov(many), sample, atol=0.02)


def test_hrp_gives_positive_weights_summing_to_one() -> None:
    w = allocate("hrp", _five_alpha_train(), small_settings(cap=0.5)).weights
    assert w.sum() == pytest.approx(1.0)
    assert (w > 0).all()


def test_max_sharpe_falls_back_to_equal_weight_when_no_mean_is_positive() -> None:
    pnl = synthetic_pnl(400, [-0.5, -0.4, -0.3, -0.6], [1.0] * 4, seed=7)
    result = allocate("max_sharpe", pnl, small_settings(cap=0.5))
    assert result.fallback is True
    assert np.allclose(result.weights.to_numpy(), 0.25)


def test_unknown_method_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown weighting method"):
        allocate("magic", _five_alpha_train(), small_settings())  # type: ignore[arg-type]


# --- points -----------------------------------------------------------------------------


def test_bound_weights_refuses_infeasible_bounds() -> None:
    with pytest.raises(ValueError, match="cannot bound"):
        bound_weights(np.array([0.5, 0.5]), floor=0.01, cap=0.25)  # two Alphas cannot cap at 25%


def test_to_points_sums_exactly_over_many_random_weights() -> None:
    rng = np.random.default_rng(8)
    for _ in range(300):
        n = int(rng.integers(4, 11))
        raw = pd.Series(rng.random(n), index=[f"a{i}" for i in range(n)])
        points = to_points(raw, 100_000, floor=0.01, cap=0.25)
        assert int(points.sum()) == 100_000
        assert (points >= 1000).all() and (points <= 25_000).all()


def test_largest_remainder_is_deterministic_on_ties() -> None:
    points = to_points(pd.Series([1 / 3, 1 / 3, 1 / 3], index=["x", "y", "z"]), 100, 0.01, 0.5)
    assert points.tolist() == [34, 33, 33]


def test_equal_weights_of_four_get_twenty_five_thousand_each() -> None:
    points = to_points(pd.Series([0.25] * 4, index=list("wxyz")), 100_000, 0.01, 0.25)
    assert points.tolist() == [25_000] * 4


# --- walk-forward -----------------------------------------------------------------------


def test_fold_spans_roll_forward_without_overlapping_test_windows() -> None:
    spans = fold_spans(1000, Settings())  # defaults: 504 train, 126 test, 126 step
    assert len(spans) == 3
    tests = [(s.test.start, s.test.stop) for s in spans]
    assert tests == [(504, 630), (630, 756), (756, 882)]


def test_selection_does_not_look_at_its_own_test_window() -> None:
    """Change the PnL inside fold 0's test window and fold 0's selection must not move: it is
    chosen on the training window alone."""
    pnl = synthetic_pnl(700, [0.4, 0.3, 0.2, 0.1, 0.0, -0.1], [1.0] * 6, rho=0.2, seed=9)
    settings = small_settings(cap=0.25, max_selected=4)
    before = run_walkforward(pnl, settings)
    spoiled = pnl.copy()
    test_rows = slice(200, 260)  # fold 0's test window with the default fold layout
    spoiled.iloc[test_rows] = spoiled.iloc[test_rows] + 1000.0
    after = run_walkforward(spoiled, settings)
    assert before.selections[0] == after.selections[0]


def test_a_chosen_alpha_with_missing_test_days_is_counted_not_dropped() -> None:
    """Missing test-window data must not decide membership (that would keep only the Alphas that
    kept reporting). The missing day counts as zero and is reported in filled_test_days."""
    pnl = synthetic_pnl(700, [0.4, 0.3, 0.2, 0.1, 0.0, -0.1], [1.0] * 6, rho=0.2, seed=9)
    settings = small_settings(cap=0.25, max_selected=4)
    before = run_walkforward(pnl, settings)
    last = fold_spans(len(pnl.index), settings)[-1]
    spoiled = pnl.copy()
    # One test day missing for every Alpha. Only the last fold's test window contains this row,
    # so the other folds' training windows are unchanged.
    spoiled.iloc[last.test.start + 10] = np.nan
    after = run_walkforward(spoiled, settings)
    assert after.selections == before.selections
    assert after.skipped_folds == before.skipped_folds == 0
    assert after.filled_test_days == len(before.selections[-1])


def test_missing_training_data_removes_an_alpha_from_the_fold() -> None:
    pnl = synthetic_pnl(700, [0.4, 0.3, 0.2, 0.1, 0.0, -0.1], [1.0] * 6, rho=0.2, seed=9)
    holed = pnl.copy()
    holed.iloc[0:60, 0] = np.nan  # A0 has no training data in fold 0
    walk = run_walkforward(holed, small_settings(cap=0.25, max_selected=4))
    assert "A0" not in walk.selections[0]


def test_overlapping_windows_are_not_double_counted() -> None:
    pnl = synthetic_pnl(500, [0.2, 0.1, 0.0, 0.3, 0.1], [1.0] * 5, rho=0.1, seed=10)
    walk = run_walkforward(pnl, small_settings(step=30, cap=0.5))  # step < test window
    assert walk.oos["equal_weight"].index.is_unique


def test_stationary_bootstrap_keeps_blocks_contiguous() -> None:
    rng = np.random.default_rng(11)
    n, mean_block = 20_000, 21.0
    idx = stationary_bootstrap_indices(n, mean_block, rng)
    assert idx.min() >= 0 and idx.max() < n
    contiguous = np.mean((idx[1:] - idx[:-1]) % n == 1)
    assert 1 - 1 / mean_block - 0.02 < contiguous < 1 - 1 / mean_block + 0.02


def _walk_from(select: dict[str, pd.Series], holdout: dict[str, pd.Series]) -> WalkForward:
    """A WalkForward whose earlier days and holdout days are given directly, two holdout folds."""
    oos = {m: pd.concat([select[m], holdout[m]]) for m in METHODS}
    return WalkForward(
        folds=4,
        oos=oos,
        turnover=dict.fromkeys(METHODS, 0.0),
        holdout_folds=2,
        oos_select=select,
        oos_holdout=holdout,
    )


def test_judge_picks_a_method_that_clearly_beats_equal_weight() -> None:
    rng = np.random.default_rng(12)
    early = pd.Series(rng.normal(0, 1, 500), index=pd.RangeIndex(500))
    late = pd.Series(rng.normal(0, 1, 250), index=pd.RangeIndex(500, 750))
    select = {m: early.copy() for m in METHODS}
    holdout = {m: late.copy() for m in METHODS}
    select["hrp"] = early + 0.6  # a real, steady edge on the earlier folds...
    holdout["hrp"] = late + 0.6  # ...that also holds on the holdout
    verdict = judge(_walk_from(select, holdout), small_settings())
    assert verdict.winner == "hrp"
    assert "beat equal weight on the holdout" in verdict.reason


def test_judge_falls_back_to_equal_weight_when_nothing_beats_it() -> None:
    rng = np.random.default_rng(13)
    select = {m: pd.Series(rng.normal(0, 1, 500)) for m in METHODS}
    holdout = {m: pd.Series(rng.normal(0, 1, 250)) for m in METHODS}
    verdict = judge(_walk_from(select, holdout), small_settings())
    assert verdict.winner == "equal_weight"
    assert "using equal weight" in verdict.reason


def test_judge_never_reselects_on_the_holdout() -> None:
    """hrp is the only method that leads on the earlier folds. inverse_volatility is far better
    on the holdout, but the holdout must not change the choice: it only checks it. hrp has no
    holdout edge, so the answer is equal weight, not inverse_volatility."""
    rng = np.random.default_rng(14)
    early = pd.Series(rng.normal(0, 1, 500))
    late = pd.Series(rng.normal(0, 1, 250))
    select = {m: early.copy() for m in METHODS}
    holdout = {m: late.copy() for m in METHODS}
    select["hrp"] = early + 0.6
    holdout["inverse_volatility"] = late + 2.0
    verdict = judge(_walk_from(select, holdout), small_settings())
    assert verdict.winner == "equal_weight"
    assert verdict.winner != "inverse_volatility"


def test_judge_with_no_holdout_folds_gives_no_verdict() -> None:
    rng = np.random.default_rng(15)
    series = {m: pd.Series(rng.normal(0, 1, 300)) for m in METHODS}
    walk = WalkForward(folds=1, oos=series, turnover=dict.fromkeys(METHODS, 0.0))
    verdict = judge(walk, small_settings())
    assert verdict.winner == "equal_weight" and "No holdout folds" in verdict.reason


# --- plan -------------------------------------------------------------------------------


def _scope_pnl(n_alphas: int, n_days: int = 700, seed: int = 14) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    means = list(rng.uniform(-0.05, 0.3, size=n_alphas))
    vols = list(rng.uniform(0.5, 2.0, size=n_alphas))
    names = [f"X{i:02d}" for i in range(n_alphas)]
    return synthetic_pnl(n_days, means, vols, rho=0.2, seed=seed, names=names)


def test_plan_sums_to_100000_and_respects_caps() -> None:
    pnl = _scope_pnl(12)
    plan = build_plan(pnl, "USA", 1, small_settings())
    assert plan.blocked is None
    assert plan.points is not None
    assert int(plan.points.sum()) == 100_000
    assert (plan.points >= 1000).all() and (plan.points <= 25_000).all()
    assert plan.method in METHODS
    assert plan.decision


def test_plan_is_blocked_only_when_too_few_alphas_can_fill_the_cap() -> None:
    """The Osmosis check is no longer a gate: three candidates are blocked by the cap alone."""
    pnl = _scope_pnl(3)
    plan = build_plan(pnl, "USA", 1, small_settings())
    assert plan.blocked is not None and "Too few alphas" in plan.blocked
    assert plan.points is None


def test_short_history_plans_without_a_validated_label() -> None:
    """Too little history for walk-forward is not a block: the plan ships HRP, unvalidated."""
    pnl = _scope_pnl(12, n_days=120)  # train 200 + test 60 do not fit
    plan = build_plan(pnl, "USA", 1, small_settings())
    assert plan.blocked is None and plan.folds == 0
    assert plan.points is not None and int(plan.points.sum()) == 100_000
    assert plan.method == "hrp"


def test_skipped_folds_are_counted_and_named_in_the_decision() -> None:
    pnl = _scope_pnl(12)
    # Eleven of twelve Alphas have no PnL in the first 60 days. Only fold 0's training window
    # reaches those days, and it is left with one usable Alpha, so fold 0 is skipped.
    pnl.iloc[:60, :11] = np.nan
    walk = run_walkforward(pnl, small_settings())
    assert walk.skipped_folds == 1 and walk.folds == 7
    plan = build_plan(pnl, "USA", 1, small_settings())
    assert plan.blocked is None and plan.skipped_folds == 1


def test_scopes_on_different_calendars_keep_their_own_folds() -> None:
    """A day only the USA scope traded must not become an all-NaN gap in the EUR scope."""
    usa = _scope_pnl(11, seed=15)
    eur_clean = _scope_pnl(11, seed=16).add_prefix("E_")
    eur_closed = eur_clean.drop(eur_clean.index[-400:-300])  # EUR market shut on these days
    eur_on_usa_calendar = eur_clean.copy()
    eur_on_usa_calendar.iloc[-400:-300] = np.nan  # same days, kept on the shared calendar
    pnl = pd.concat([usa, eur_on_usa_calendar], axis=1)
    scope_of = dict.fromkeys(usa.columns, ("USA", 1)) | dict.fromkeys(eur_clean.columns, ("EUR", 1))
    settings = small_settings()
    eur_plan = {p.label: p for p in plan_all(pnl, scope_of, settings)}["EUR D1"]
    reference = build_plan(eur_closed, "EUR", 1, settings)
    assert eur_plan.blocked is None and eur_plan.folds == reference.folds
    assert eur_plan.skipped_folds == reference.skipped_folds == 0
    assert eur_plan.points is not None and reference.points is not None
    pd.testing.assert_series_equal(eur_plan.points, reference.points)


def test_fallback_weighting_is_labelled_in_the_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the solver has no answer, the plan ships equal weight. The CSV must say so, not
    report the judged method as if its weights had been used."""
    real_allocate = scope_mod.allocate

    def solver_fails(method: str, train: pd.DataFrame, settings: Settings) -> Allocation:
        allocation = real_allocate(method, train, settings)  # type: ignore[arg-type]
        if method == "equal_weight":
            return allocation
        return Allocation(allocation.weights, fallback=True)

    forced = Judgement("hrp", "forced for this test", ())
    monkeypatch.setattr(scope_mod, "_JUDGED", {})  # the walk is cached: judge it afresh
    monkeypatch.setattr(scope_mod, "_FITS", {})  # so is the refit: solve it afresh
    monkeypatch.setattr(scope_mod, "judge", lambda _walk, _settings: forced)
    monkeypatch.setattr(scope_mod, "allocate", solver_fails)
    pnl = _scope_pnl(12)
    statuses = statuses_for(list(pnl.columns))
    plan = build_plan(pnl, "USA", 1, small_settings())
    assert plan.fallback and plan.method == "equal_weight"
    text = to_csv([plan], statuses, generated_at="g", data_through="d")
    assert "equal_weight (fallback)" in text and ",hrp," not in text


def test_min_folds_and_holdout_must_leave_selection_folds() -> None:
    with pytest.raises(ValueError, match="holdout_folds"):
        from_mapping({"holdout_folds": 0})
    with pytest.raises(ValueError, match="holdout_folds"):
        from_mapping({"min_folds": 3, "holdout_folds": 2})


def test_plan_all_splits_scopes_and_csv_has_no_payment_field() -> None:
    usa = _scope_pnl(11, seed=15)
    eur = _scope_pnl(11, seed=16).add_prefix("E_")
    pnl = pd.concat([usa, eur], axis=1)
    scope_of = dict.fromkeys(usa.columns, ("USA", 1)) | dict.fromkeys(eur.columns, ("EUR", 1))
    statuses = statuses_for(list(pnl.columns))
    settings = small_settings()
    plans = plan_all(pnl, scope_of, settings)
    assert [p.label for p in plans] == ["EUR D1", "USA D1"]
    for p in plans:
        assert p.blocked is None and p.points is not None
        assert int(p.points.sum()) == 100_000

    text = to_csv(plans, statuses, generated_at="2026-11-12", data_through="2026-06-30")
    lines = text.strip().splitlines()
    assert lines[0] == (
        "generated_at,data_through,region,delay,alpha_id,points,weight,method,osmosis_status"
    )
    assert "payment" not in text.lower() and "rank," not in lines[0]
    rows = [line.split(",") for line in lines[1:]]
    assert sum(int(r[5]) for r in rows if r[2] == "USA") == 100_000
    assert all(r[8] == "eligible" for r in rows)


def test_package_never_imports_brain_or_simulator() -> None:
    """Part A is read-only by construction: no module here may import a BRAIN endpoint, client
    or simulator. Checked on the source, so it holds even for code paths a test never runs."""
    package = REPO / "backend" / "src" / "alpha_harness" / "forge" / "osmosis"
    banned = ("endpoints", "simulat", "httpx", "brain", "client")
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert not any(b in name.lower() for b in banned), f"{path.name} imports {name}"
