"""Offline tests for the Osmosis robust methods, PnL basis and consistency (Part A).

Synthetic PnL and a fake engine only. No BRAIN client, no simulator, no network. The methods are
checked on synthetic data from known models, so a test can say what the right answer is.
"""

import numpy as np
import pandas as pd
import pytest

from alpha_harness.forge.osmosis import robust
from alpha_harness.forge.osmosis.inputs import (
    apply_basis,
    consistency_scores,
    resolve_basis,
    turnover_complete,
)
from alpha_harness.forge.osmosis.robust import (
    ENSEMBLE_INPUTS,
    ROBUST_METHODS,
    black_litterman_mean,
    max_diversification_weights,
    nco_weights,
)
from alpha_harness.forge.osmosis.settings import Settings, from_mapping, load_settings
from alpha_harness.forge.osmosis.weights import (
    Method,
    allocate,
    equal_risk_contribution_solution,
    ledoit_wolf_cov,
    risk_shares,
)
from alpha_harness.vault import metrics


def _settings(**overrides: object) -> Settings:
    """Bounds loose enough for 4 to 6 Alphas, and a small resample count to keep tests quick."""
    base: dict[str, object] = {"cap": 0.4, "floor": 0.02, "resample_count": 40}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _synthetic(days: int = 504, p: int = 6, seed: int = 0, corr: float = 0.3) -> pd.DataFrame:
    """Alphas with one common factor (correlation ``corr``), identical mean and volatility.

    Every Alpha has the same true Sharpe, so any difference between methods is estimation noise.
    """
    rng = np.random.default_rng(seed)
    common = rng.normal(size=(days, 1))
    idio = rng.normal(size=(days, p))
    x = 0.02 + np.sqrt(corr) * common + np.sqrt(1.0 - corr) * idio
    return pd.DataFrame(x, columns=[f"A{i}" for i in range(p)])


def _two_clusters(days: int = 2000, seed: int = 1) -> np.ndarray:
    """Six Alphas in two clean groups of three: each group shares one factor, the groups are
    independent, and every Alpha has the same volatility."""
    rng = np.random.default_rng(seed)
    f1 = rng.normal(size=days)
    f2 = rng.normal(size=days)
    cols = [f1 + 0.5 * rng.normal(size=days) for _ in range(3)]
    cols += [f2 + 0.5 * rng.normal(size=days) for _ in range(3)]
    return np.column_stack(cols)


# --- every method: sums to one, respects the floor and cap ----------------------------------


@pytest.mark.parametrize("method", ROBUST_METHODS)
@pytest.mark.parametrize("p", [4, 6])
def test_every_robust_method_sums_to_one_and_respects_bounds(method: Method, p: int) -> None:
    settings = _settings()
    result = allocate(method, _synthetic(p=p, seed=p), settings).weights.to_numpy()
    assert result.sum() == pytest.approx(1.0, abs=1e-9)
    assert result.min() >= settings.floor - 1e-9
    assert result.max() <= settings.cap + 1e-9


def test_nco_falls_back_to_equal_weight_with_fewer_than_three_alphas() -> None:
    """Nothing to cluster into: no k exists between 2 and p-1 when p is 2."""
    settings = _settings(cap=0.5)
    alloc = allocate("nco", _synthetic(p=2), settings)
    assert alloc.fallback
    assert np.allclose(alloc.weights.to_numpy(), 0.5)


def test_resampled_falls_back_when_fewer_than_half_the_draws_solve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(robust, "max_sharpe_solution", lambda *_a, **_k: None)
    alloc = allocate("resampled_max_sharpe", _synthetic(p=4), _settings())
    assert alloc.fallback
    assert np.allclose(alloc.weights.to_numpy(), 0.25)


def test_nco_falls_back_when_a_solver_level_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(robust, "min_variance_solution", lambda *_a, **_k: None)
    assert nco_weights(_two_clusters()) is None
    assert allocate("nco", _synthetic(p=6), _settings()).fallback


def test_bayesian_blend_falls_back_when_weights_are_not_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A posterior that points the wrong way gives negative weights: no long-only answer."""
    monkeypatch.setattr(robust, "black_litterman_mean", lambda prior, *_a, **_k: -prior)
    alloc = allocate("bayesian_blend", _synthetic(p=4), _settings())
    assert alloc.fallback
    assert np.allclose(alloc.weights.to_numpy(), 0.25)


def test_ensemble_falls_back_when_it_has_no_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(robust, "ENSEMBLE_INPUTS", ())
    alloc = allocate("ensemble", _synthetic(p=4), _settings())
    assert alloc.fallback
    assert np.allclose(alloc.weights.to_numpy(), 0.25)


# --- nco and max diversification ------------------------------------------------------------


def test_nco_splits_risk_about_evenly_between_two_clean_clusters() -> None:
    x = _two_clusters()
    w = nco_weights(x)
    assert w is not None
    shares = risk_shares(w, ledoit_wolf_cov(x))
    assert shares[:3].sum() == pytest.approx(0.5, abs=0.05)
    assert shares[3:].sum() == pytest.approx(0.5, abs=0.05)


def test_max_diversification_beats_equal_weight_on_its_own_ratio() -> None:
    rng = np.random.default_rng(3)
    base = _synthetic(p=6, seed=3).to_numpy()
    x = base * np.array([1.0, 1.0, 1.0, 3.0, 3.0, 3.0]) + 0.1 * rng.normal(size=base.shape)
    cov = ledoit_wolf_cov(x)
    sigma = np.sqrt(np.diag(cov))

    def ratio(w: np.ndarray) -> float:
        return float(w @ sigma) / float(np.sqrt(w @ cov @ w))

    w = max_diversification_weights(x)
    assert w is not None
    assert ratio(w) >= ratio(np.full(6, 1 / 6)) - 1e-9


# --- ensemble --------------------------------------------------------------------------------


def test_ensemble_equals_the_mean_of_its_non_fallback_inputs_and_excludes_equal_weight() -> None:
    train = _synthetic(p=6, seed=5)
    settings = _settings()
    inputs = []
    for method in ENSEMBLE_INPUTS:
        alloc = allocate(method, train, settings)
        if not alloc.fallback:
            inputs.append(alloc.weights.to_numpy())
    assert inputs, "the synthetic case should give at least one real input"
    ensemble = allocate("ensemble", train, settings)
    assert not ensemble.fallback
    assert np.allclose(ensemble.weights.to_numpy(), np.mean(inputs, axis=0), atol=1e-9)


def test_ensemble_inputs_do_not_include_equal_weight_or_the_ensemble_itself() -> None:
    assert "equal_weight" not in ENSEMBLE_INPUTS
    assert "ensemble" not in ENSEMBLE_INPUTS


# --- Black-Litterman -------------------------------------------------------------------------


def test_bayesian_blend_returns_the_prior_when_views_carry_no_weight() -> None:
    x = _synthetic(p=5, seed=8).to_numpy()
    cov = ledoit_wolf_cov(x)
    w_erc = equal_risk_contribution_solution(cov)
    assert w_erc is not None
    lam, tau = 2.5, 0.05
    prior = lam * cov @ w_erc
    views = np.full(5, 123.0)  # arbitrary: the huge uncertainty should make them irrelevant
    mu = black_litterman_mean(prior, views, cov, omega=np.full(5, 1e12), tau=tau)
    assert np.allclose(mu, prior, rtol=1e-6, atol=1e-9)
    weights = np.linalg.solve(lam * cov, mu)
    assert np.allclose(weights / weights.sum(), w_erc, atol=1e-6)


def test_bayesian_views_dominate_with_a_long_history() -> None:
    x = _synthetic(p=5, seed=8).to_numpy()
    cov = ledoit_wolf_cov(x)
    prior = np.full(5, 0.0)
    views = np.array([0.001, 0.002, 0.003, 0.004, 0.005])
    mu = black_litterman_mean(prior, views, cov, omega=np.full(5, 1e-14), tau=0.05)
    assert np.allclose(mu, views, rtol=1e-4, atol=1e-7)


# --- resampled stability ---------------------------------------------------------------------


def test_resampled_max_sharpe_is_steadier_than_plain_max_sharpe() -> None:
    """Across independent datasets from one model, the averaged-resample weights should vary
    less than a single max-Sharpe fit does. If this ever fails, report it; do not tune until it
    passes."""
    settings = _settings(cap=0.4, floor=0.02, resample_count=200)
    plain, resampled = [], []
    for seed in range(8):
        data = _synthetic(p=6, seed=100 + seed)
        plain.append(allocate("max_sharpe", data, settings).weights.to_numpy())
        resampled.append(allocate("resampled_max_sharpe", data, settings).weights.to_numpy())
    plain_var = np.var(np.array(plain), axis=0, ddof=1).mean()
    resampled_var = np.var(np.array(resampled), axis=0, ddof=1).mean()
    assert resampled_var < plain_var


# --- consistency -----------------------------------------------------------------------------


def test_consistency_is_the_std_of_half_year_sharpes() -> None:
    settings = Settings()
    x = _synthetic(days=504, p=2, seed=11).to_numpy()
    halves = [x[k * 126 : (k + 1) * 126, 0] for k in range(4)]
    sharpes = [h.mean() / h.std(ddof=1) * np.sqrt(252) for h in halves]
    assert consistency_scores(x, settings)[0] == pytest.approx(np.std(sharpes, ddof=1))


def test_consistency_is_neutral_below_four_half_years() -> None:
    settings = Settings()
    x = _synthetic(days=300, p=3, seed=12).to_numpy()  # two complete half-years only
    assert np.all(consistency_scores(x, settings) == 0.0)


def test_consistency_reads_only_the_window_it_is_given() -> None:
    """The walk-forward passes allocate() only the training rows. Rows after the window must not
    change the score: the same window with and without a poisoned future gives the same answer."""
    settings = Settings()
    full = _synthetic(days=900, p=3, seed=13).to_numpy()
    train = full[:504]
    poisoned = full.copy()
    poisoned[504:] = 1e6
    assert np.array_equal(
        consistency_scores(train, settings), consistency_scores(poisoned[:504], settings)
    )


def test_allocate_for_robust_methods_uses_only_the_training_frame() -> None:
    """A frame is the whole input: the same training rows give the same weights, however the
    surrounding data looked. Checked for the two methods that read consistency or Sharpe."""
    settings = _settings()
    train = _synthetic(days=504, p=5, seed=14)
    for method in ("bayesian_blend", "resampled_max_sharpe"):
        first = allocate(method, train, settings).weights.to_numpy()
        second = allocate(method, train.copy(), settings).weights.to_numpy()
        assert np.array_equal(first, second)


# --- PnL basis -------------------------------------------------------------------------------


def test_basis_is_after_cost_when_every_candidate_has_turnover() -> None:
    basis = resolve_basis("after_cost_5bps", {"A": True, "B": True})
    assert basis.name == "after_cost_5bps"
    assert basis.cost_bps == 5.0


def test_basis_falls_back_to_gross_for_the_whole_scope_when_one_candidate_lacks_turnover() -> None:
    basis = resolve_basis("after_cost_5bps", {"A": True, "B": False})
    assert basis.name == "gross"
    assert basis.cost_bps == 0.0
    assert "B" in basis.reason


def test_gross_when_requested_and_empty_scope_gives_gross() -> None:
    assert resolve_basis("gross", {"A": True}).name == "gross"
    assert resolve_basis("after_cost_5bps", {}).name == "gross"


def test_unknown_basis_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown PnL basis"):
        resolve_basis("net_of_everything", {"A": True})


def test_apply_basis_uses_the_vault_after_cost_formula() -> None:
    pnl = np.array([100.0, -50.0, 20.0])
    turnover = np.array([0.1, 0.2, 0.3])
    assert np.allclose(apply_basis(pnl, turnover, 5.0), metrics.after_cost(pnl, turnover, 5.0))
    assert np.array_equal(apply_basis(pnl, turnover, 0.0), pnl)


def test_turnover_complete_ignores_days_without_pnl() -> None:
    pnl = np.array([[1.0, 1.0], [2.0, np.nan], [3.0, 3.0]])
    turnover = np.array([[0.1, np.nan], [0.2, np.nan], [0.3, 0.4]])
    # Column B has no turnover on day 0 or 2 where it has PnL: incomplete. Day 1 has no PnL in B,
    # so its missing turnover is irrelevant. Column A is complete.
    assert turnover_complete(pnl, turnover).tolist() == [True, False]


# --- settings --------------------------------------------------------------------------------


def test_settings_reject_an_unknown_pnl_basis() -> None:
    with pytest.raises(ValueError, match="pnl_basis"):
        from_mapping({"pnl_basis": "net_of_everything"})


def test_shipped_yaml_loads_the_robust_settings() -> None:
    settings = load_settings()
    assert settings.pnl_basis == "after_cost_5bps"
    assert settings.resample_count == 200
    assert settings.min_half_years == 4
