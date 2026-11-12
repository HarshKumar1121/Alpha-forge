"""Effective bets, the recommended-N rule, equal risk contribution, and the "Why" panel.

Offline: synthetic PnL and the small settings from ``test_forge_osmosis``. Nothing here calls BRAIN.
"""

import numpy as np
import pandas as pd
import pytest
from test_forge_osmosis import small_settings, synthetic_pnl

from alpha_harness.forge.osmosis import weights as weights_mod
from alpha_harness.forge.osmosis.effective_bets import (
    bets_curve,
    clean_correlation,
    effective_bets,
    recommended_size,
)
from alpha_harness.forge.osmosis.scope import ScopeAnalysis, analyse_scope
from alpha_harness.forge.osmosis.settings import Settings, from_mapping
from alpha_harness.forge.osmosis.weights import METHODS, allocate, ledoit_wolf_cov, risk_shares
from alpha_harness.forge.osmosis.why import OS_WINDOW_START, build_why


def _pnl(n_alphas: int, n_days: int = 700, seed: int = 3) -> pd.DataFrame:
    """Synthetic candidates, as the scope tests build them."""
    rng = np.random.default_rng(seed)
    means = list(rng.uniform(-0.05, 0.3, size=n_alphas))
    vols = list(rng.uniform(0.5, 2.0, size=n_alphas))
    names = [f"S{i:02d}" for i in range(n_alphas)]
    return synthetic_pnl(n_days, means, vols, rho=0.2, seed=seed, names=names)


def _run(pnl: pd.DataFrame, method: str | None = None) -> ScopeAnalysis:
    """One USA D1 scope under the small settings."""
    return analyse_scope(pnl, "USA", 1, small_settings(), {}, None, method)  # type: ignore[arg-type]


def _factor_returns(n_days: int, n_alphas: int, rho: float, seed: int) -> pd.DataFrame:
    """Alphas that share one common factor with correlation ``rho``, plus their own noise."""
    rng = np.random.default_rng(seed)
    common = rng.normal(size=n_days)
    noise = rng.normal(size=(n_days, n_alphas))
    data = np.sqrt(rho) * common[:, None] + np.sqrt(1 - rho) * noise
    return pd.DataFrame(data, columns=[f"A{i}" for i in range(n_alphas)])


# -- effective bets ------------------------------------------------------------


def test_uncorrelated_alphas_are_as_many_bets_as_alphas() -> None:
    assert effective_bets(np.eye(7)) == pytest.approx(7.0)


def test_perfectly_correlated_alphas_are_one_bet() -> None:
    assert effective_bets(np.ones((5, 5))) == pytest.approx(1.0)


def test_one_alpha_is_one_bet_and_none_is_zero() -> None:
    assert effective_bets(np.ones((1, 1))) == pytest.approx(1.0)
    assert effective_bets(np.zeros((0, 0))) == 0.0


def test_a_flat_alpha_is_uncorrelated_not_nan() -> None:
    frame = _factor_returns(300, 3, rho=0.0, seed=2)
    frame["flat"] = 0.0
    corr = clean_correlation(frame)
    assert np.isfinite(corr).all()
    assert corr[3, 0] == 0.0 and corr[3, 3] == 1.0


def test_bets_curve_grows_and_starts_at_one() -> None:
    frame = _factor_returns(800, 12, rho=0.3, seed=4)
    curve = bets_curve(frame)
    assert len(curve) == 12
    assert curve[0] == pytest.approx(1.0)
    assert curve[-1] > curve[0]  # more alphas, more bets ...
    assert curve[-1] < 12  # ... but correlated, so fewer bets than alphas


def test_recommended_size_is_the_floor_when_the_curve_is_already_flat() -> None:
    """A curve that adds nothing from the first step has its floor as the answer."""
    assert recommended_size([1.0] * 20, floor=10, gain=0.05) == 10


def test_recommended_size_finds_where_independent_bets_stop_growing_fast() -> None:
    """With N independent bets, adding alpha k+1 raises ENB by 1/k. That is under 5% from
    k = 21 on, so the smallest size at or above the floor of 10 is 21."""
    curve = [float(k) for k in range(1, 41)]
    assert recommended_size(curve, floor=10, gain=0.05) == 21


def test_recommended_size_never_goes_below_the_floor_or_above_the_ranking() -> None:
    curve = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7]
    rec = recommended_size(curve, floor=5, gain=0.05)
    assert 5 <= rec <= len(curve)
    assert recommended_size([], floor=10, gain=0.05) == 0
    assert recommended_size([1.0, 2.0, 3.0], floor=10, gain=0.05) == 3  # floor capped at total


def test_bets_min_gain_must_not_be_negative() -> None:
    with pytest.raises(ValueError, match="bets_min_gain"):
        from_mapping({"bets_min_gain": -0.1})


# -- equal risk contribution ---------------------------------------------------


def test_erc_is_a_registered_method() -> None:
    assert "equal_risk_contribution" in METHODS


def test_risk_shares_sum_to_one_and_follow_the_covariance() -> None:
    cov = np.diag([1.0, 4.0])
    shares = risk_shares(np.array([0.5, 0.5]), cov)
    assert shares.sum() == pytest.approx(1.0)
    assert shares[1] > shares[0]  # the more volatile Alpha carries more of the risk


def test_erc_equalises_risk_shares_on_correlated_alphas() -> None:
    train = _factor_returns(600, 4, rho=0.4, seed=8) * np.array([0.5, 1.0, 1.5, 2.0])
    result = allocate("equal_risk_contribution", train, small_settings(cap=0.6))
    assert result.fallback is False
    cov = ledoit_wolf_cov(train.to_numpy() / float(np.std(train.to_numpy(), ddof=1)))
    shares = risk_shares(result.weights.to_numpy(), cov)
    assert np.max(np.abs(shares - 0.25)) < 0.01


def test_erc_matches_inverse_volatility_when_alphas_are_uncorrelated() -> None:
    """With no correlation, equal risk shares mean each weight is proportional to 1/sigma."""
    rng = np.random.default_rng(5)
    scales = np.array([0.5, 1.0, 2.0])
    train = pd.DataFrame(rng.normal(size=(800, 3)) * scales, columns=["a", "b", "c"])
    erc = allocate("equal_risk_contribution", train, small_settings(cap=0.9, floor=0.01))
    inv = allocate("inverse_volatility", train, small_settings(cap=0.9, floor=0.01))
    assert erc.fallback is False
    # Sample correlations are never exactly zero, so allow for that noise.
    assert erc.weights.to_numpy() == pytest.approx(inv.weights.to_numpy(), abs=0.05)


def test_erc_falls_back_to_equal_weight_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(weights_mod, "_equal_risk_contribution", lambda _cov: None)
    train = _factor_returns(300, 4, rho=0.2, seed=9)
    result = allocate("equal_risk_contribution", train, small_settings(cap=0.6))
    assert result.fallback is True
    assert result.weights.to_numpy() == pytest.approx(np.full(4, 0.25))


# -- the "Why this allocation" panel -------------------------------------------


def test_the_panel_states_that_no_out_of_sample_data_exists() -> None:
    settings = small_settings()
    panel = build_why(_run(_pnl(12)), settings)
    assert "No out-of-sample data is available" in panel.evidence_line
    assert OS_WINDOW_START in panel.evidence_line
    assert "in-sample history only" in panel.evidence_line
    assert "Decay check not run" in panel.decay_line
    assert panel.method is not None


def test_the_panel_reports_the_size_rule_in_words() -> None:
    settings: Settings = small_settings()
    analysis = _run(_pnl(12))
    panel = build_why(analysis, settings)
    assert f"{analysis.n} alphas" in panel.size_line
    assert f"recommended {analysis.recommended_n}" in panel.size_line
    assert f"{settings.bets_min_gain:.0%}" in panel.size_line


def test_a_blocked_scope_gets_a_panel_with_no_method() -> None:
    panel = build_why(_run(_pnl(3)), small_settings())
    assert panel.method is None
    assert panel.validated is False
    assert "blocked" in panel.method_line


def test_erc_reaches_a_full_scope_allocation() -> None:
    """Chosen as a scope method, ERC gives the same bounded plan that sums to 100,000."""
    analysis = _run(_pnl(12), method="equal_risk_contribution")
    assert analysis.requested_method == "equal_risk_contribution"
    assert analysis.points is not None and int(analysis.points.sum()) == 100_000
