"""Offline tests: identical series are not a test; flat PnL is excluded, not a crash.

Covers the three findings from the real-data check: JPN (4 Alphas, the cap leaves one mix),
IND (holdout identical to equal weight), and zero-variance Alphas feeding the covariance.
Synthetic PnL only.
"""

import warnings
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from alpha_harness.forge.osmosis import walkforward as walk_mod
from alpha_harness.forge.osmosis import weights as weights_mod
from alpha_harness.forge.osmosis.scope import Exclusion, analyse_scope
from alpha_harness.forge.osmosis.settings import Settings, load_settings
from alpha_harness.forge.osmosis.walkforward import (
    WalkForward,
    has_variation,
    judge,
    run_walkforward,
)
from alpha_harness.forge.osmosis.weights import METHODS, Allocation, Method


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "train_days": 200,
        "test_days": 60,
        "step": 60,
        "min_folds": 4,
        "holdout_folds": 2,
        "cap": 0.25,
        "floor": 0.01,
        "min_eligible": 4,
        "resample_count": 20,
        "fold_resample_count": 10,
        "bootstrap_samples": 200,
        "bootstrap_block": 10.0,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _frame(days: int, p: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    common = rng.normal(size=(days, 1))
    x = 0.05 + 0.5 * common + rng.normal(size=(days, p))
    return pd.DataFrame(x, columns=[f"A{i}" for i in range(p)])


# --- zero-variance Alphas --------------------------------------------------------------------


def test_a_flat_alpha_is_left_out_of_the_fold_and_raises_no_warning() -> None:
    frame = _frame(500, 6, seed=1)
    frame["FLAT"] = 0.0
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)  # a divide-by-zero would now raise
        walk = run_walkforward(frame, _settings())
    assert walk.folds > 0
    assert "FLAT" in walk.no_variation
    assert all("FLAT" not in sel for sel in walk.selections)


def test_has_variation_is_false_for_a_constant_series() -> None:
    assert not has_variation(pd.Series([0.0] * 50))
    assert has_variation(pd.Series(np.arange(50, dtype=float)))


def test_scope_names_a_flat_alpha_as_excluded_and_never_blocks() -> None:
    frame = _frame(500, 6, seed=2)
    frame["FLAT"] = 0.0
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        out = analyse_scope(frame, "USA", 1, _settings(cap=0.5, floor=0.01), {})
    assert out.blocked is None
    assert Exclusion("FLAT", "flat") in out.excluded
    assert "FLAT" not in out.chosen


# --- identical series -------------------------------------------------------------------------


def test_four_alphas_at_a_quarter_cap_say_the_cap_forces_equal_weight() -> None:
    """With exactly four Alphas and a 25% cap, equal weight is the only mix every method can
    produce. The judge must say so, and must not show an interval."""
    frame = _frame(500, 4, seed=3)
    settings = _settings()
    walk = run_walkforward(frame, settings)
    verdict = judge(walk, settings)
    assert verdict.winner == "equal_weight"
    assert "the only mix" in verdict.reason
    assert "Identical series, not a test" in verdict.reason
    assert all(v.identical for v in verdict.verdicts)
    assert all(np.isnan(v.ci_low) and np.isnan(v.ci_high) for v in verdict.verdicts)


def _walk(
    select: dict[str, pd.Series],
    holdout: dict[str, pd.Series],
    flags: dict[str, list[tuple[bool, bool]]],
    sizes: list[int],
) -> WalkForward:
    oos = {m: pd.concat([select[m], holdout[m]], ignore_index=True) for m in METHODS}
    return WalkForward(
        folds=len(sizes),
        oos=oos,
        turnover=dict.fromkeys(METHODS, 0.0),
        selections=[[f"x{i}" for i in range(s)] for s in sizes],
        holdout_folds=2,
        oos_select=select,
        oos_holdout=holdout,
        fold_flags=flags,
    )


def test_a_candidate_that_fell_back_on_every_holdout_fold_is_named_as_such() -> None:
    rng = np.random.default_rng(4)
    early = pd.Series(rng.normal(0, 1, 500))
    late = pd.Series(rng.normal(0, 1, 250))
    select = {m: early.copy() for m in METHODS}
    holdout = {m: late.copy() for m in METHODS}
    select["max_sharpe"] = early + 0.8  # the candidate: it led on the earlier folds
    holdout["max_sharpe"] = late.copy()  # but on the holdout it is exactly equal weight
    holdout["hrp"] = late + 0.1  # another method differs, so not every method is identical
    flags = {m: [(False, False)] * 4 for m in METHODS}
    flags["max_sharpe"] = [(True, True)] * 4  # every fold fell back
    verdict = judge(_walk(select, holdout, flags, [6, 6, 6, 6]), _settings())
    assert verdict.winner == "equal_weight"
    assert (
        "max_sharpe fell back to equal weight on the holdout folds; identical series, "
        "not a test" in verdict.reason
    )
    ms = next(v for v in verdict.verdicts if v.method == "max_sharpe")
    assert ms.identical and np.isnan(ms.ci_low)


def test_a_method_that_differs_on_the_holdout_still_gets_an_interval() -> None:
    rng = np.random.default_rng(5)
    early = pd.Series(rng.normal(0, 1, 500))
    late = pd.Series(rng.normal(0, 1, 250))
    select = {m: early.copy() for m in METHODS}
    holdout = {m: late.copy() for m in METHODS}
    select["hrp"] = early + 0.6
    holdout["hrp"] = late + 0.1  # different from equal weight: this is a real test
    flags = {m: [(False, False)] * 4 for m in METHODS}
    verdict = judge(_walk(select, holdout, flags, [6, 6, 6, 6]), _settings())
    hrp = next(v for v in verdict.verdicts if v.method == "hrp")
    assert not hrp.identical
    assert np.isfinite(hrp.ci_low) and np.isfinite(hrp.ci_high)


# --- resample split: folds use fold_resample_count, the final fit uses resample_count ----------


def test_folds_run_at_fold_resample_count_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    real = walk_mod.allocate

    def spy(method: Method, train: pd.DataFrame, settings: Settings) -> Allocation:
        seen.append(settings.resample_count)
        return real(method, train, settings)

    monkeypatch.setattr(walk_mod, "allocate", spy)
    settings = _settings(resample_count=40, fold_resample_count=10)
    run_walkforward(_frame(500, 6, seed=6), settings)
    assert seen and set(seen) == {10}


def test_shipped_yaml_sets_the_fold_resample_count_below_the_final_count() -> None:
    settings = load_settings()
    assert settings.fold_resample_count == 50
    assert settings.resample_count == 200
    assert settings.fold_resample_count < settings.resample_count


def test_fold_resample_count_below_ten_is_rejected() -> None:
    from alpha_harness.forge.osmosis.settings import from_mapping

    with pytest.raises(ValueError, match="fold_resample_count"):
        from_mapping({"fold_resample_count": 3})


def test_a_flat_alpha_cannot_reach_the_covariance() -> None:
    """Regression: a zero-variance column used to reach Ledoit-Wolf and divide by zero."""
    frame = _frame(300, 5, seed=7)
    frame["FLAT"] = 0.0
    settings = replace(_settings(), cap=0.5)
    keep = [c for c in frame.columns if has_variation(frame[c])]
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        allocation = weights_mod.allocate("hrp", frame[keep], settings)
    assert np.isfinite(allocation.weights.to_numpy()).all()


def test_the_fold_ensemble_reuses_its_inputs_and_matches_the_standalone_ensemble() -> None:
    """The walk-forward builds the ensemble from the fold's own allocations, so it must equal
    the ensemble that allocate() computes from scratch on the same window."""
    from alpha_harness.forge.osmosis.robust import ensemble_of

    settings = _settings(cap=0.4, floor=0.02)
    train = _frame(300, 6, seed=9)
    cols = list(train.columns)
    fold = {m: weights_mod.allocate(m, train, settings) for m in METHODS if m != "ensemble"}
    reused = ensemble_of(fold, cols)
    standalone = weights_mod.allocate("ensemble", train, settings)
    assert not reused.fallback and not standalone.fallback
    assert np.allclose(reused.weights.to_numpy(), standalone.weights.to_numpy(), atol=1e-9)


def test_the_vectorised_bootstrap_gives_the_same_interval_as_the_plain_loop() -> None:
    """The judge's interval is computed in one matrix pass now. It must give the same numbers as
    the loop it replaced, from the same seed, so no verdict moves."""
    from alpha_harness.forge.osmosis.selection import sharpe
    from alpha_harness.forge.osmosis.walkforward import (
        sharpe_gap_interval,
        stationary_bootstrap_indices,
    )

    settings = _settings(bootstrap_samples=300)
    rng_data = np.random.default_rng(10)
    a = rng_data.normal(0.02, 1.0, 252)
    b = rng_data.normal(0.01, 1.0, 252)
    gain, low, high = sharpe_gap_interval(a, b, settings)
    rng = np.random.default_rng(settings.seed)
    gaps = []
    for _ in range(settings.bootstrap_samples):
        idx = stationary_bootstrap_indices(a.size, settings.bootstrap_block, rng)
        gaps.append(
            sharpe(a[idx], settings.periods_per_year) - sharpe(b[idx], settings.periods_per_year)
        )
    expected_low, expected_high = np.percentile(np.array(gaps), [2.5, 97.5])
    assert gain == pytest.approx(sharpe(a, 252) - sharpe(b, 252))
    assert low == pytest.approx(expected_low)
    assert high == pytest.approx(expected_high)


def test_the_analytic_gradient_matches_finite_differences() -> None:
    """The max-Sharpe and diversification objectives now pass an analytic gradient to the
    optimiser. It must equal the numerical one, or the optimiser solves a different problem."""
    from alpha_harness.forge.osmosis.weights import ledoit_wolf_cov, negative_ratio_gradient

    rng = np.random.default_rng(11)
    cov = ledoit_wolf_cov(rng.normal(size=(300, 5)))
    vector = rng.normal(0.02, 0.01, size=5)
    w = np.full(5, 0.2) + rng.normal(0, 0.02, size=5)

    def objective(x: np.ndarray) -> float:
        variance = max(float(x @ cov @ x), 1e-18)
        return -float(x @ vector) / np.sqrt(variance)

    numeric = np.empty(5)
    step = 1e-6
    for i in range(5):
        bump = np.zeros(5)
        bump[i] = step
        numeric[i] = (objective(w + bump) - objective(w - bump)) / (2 * step)
    assert np.allclose(negative_ratio_gradient(w, cov, vector), numeric, rtol=1e-5, atol=1e-7)
