"""The scope engine: candidates, ranking, recommended N, method choice and points.

Offline: synthetic PnL and the small settings from ``test_forge_osmosis``. Nothing here calls BRAIN.
"""

from typing import TYPE_CHECKING

import numpy as np
import pytest
from test_forge_osmosis import small_settings, synthetic_pnl

from alpha_harness.forge.osmosis import scope as scope_mod
from alpha_harness.forge.osmosis.scope import ScopeAnalysis, analyse_scope

if TYPE_CHECKING:
    import pandas as pd

    from alpha_harness.forge.osmosis.weights import Method


def _pnl(n_alphas: int, n_days: int = 700, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    means = list(rng.uniform(-0.05, 0.3, size=n_alphas))
    vols = list(rng.uniform(0.5, 2.0, size=n_alphas))
    names = [f"S{i:02d}" for i in range(n_alphas)]
    return synthetic_pnl(n_days, means, vols, rho=0.2, seed=seed, names=names)


def _run(
    pnl: pd.DataFrame,
    n: int | None = None,
    method: Method | None = None,
    turnover: dict[str, float] | None = None,
) -> ScopeAnalysis:
    """One USA D1 scope under the small settings; no turnover unless given."""
    return analyse_scope(pnl, "USA", 1, small_settings(), turnover or {}, n, method)


def test_the_check_never_gates_a_candidate() -> None:
    """Candidates come from stored PnL alone: no Osmosis result is read at all."""
    analysis = _run(_pnl(12))
    assert analysis.blocked is None
    assert analysis.alpha_count == 12
    assert analysis.points is not None and int(analysis.points.sum()) == 100_000


def test_fewer_than_the_cap_needs_is_blocked_with_the_cap_reason() -> None:
    analysis = _run(_pnl(3))
    assert analysis.blocked is not None
    assert "Too few alphas to allocate under the 25% cap" in analysis.blocked
    assert analysis.points is None


def test_short_history_defaults_to_hrp_and_says_it_is_not_validated() -> None:
    analysis = _run(_pnl(12, n_days=120))
    assert analysis.validated is False and analysis.default_method == "hrp"
    assert analysis.banner is not None and analysis.banner.startswith(
        "Not validated: not enough history"
    )
    assert analysis.need_days == 440  # train 200 + test 60 + 3 * step 60 under these settings


def test_a_default_method_is_validated_only_when_it_beat_equal_weight_on_holdout() -> None:
    analysis = _run(_pnl(12))
    judgement = analysis.judgement
    assert judgement is not None  # 700 days give enough folds for a judgement
    passed = any(v.method == judgement.winner and v.beats_equal_weight for v in judgement.verdicts)
    if passed:
        assert analysis.validated and analysis.default_method == judgement.winner
        assert analysis.banner is None
    else:
        assert not analysis.validated and analysis.default_method == "hrp"
        assert analysis.banner is not None


def test_recommended_n_is_at_least_the_floor_when_enough_alphas_rank() -> None:
    analysis = _run(_pnl(12))
    total = len(analysis.ranking)
    assert total >= 10
    assert analysis.recommended_n >= 10


def test_recommended_n_is_the_bets_flattening_point() -> None:
    """The recommendation is the smallest size, from the floor, after which one more Alpha
    raises effective bets by less than the threshold. One size below it, the next Alpha still
    added at least that much."""
    settings = small_settings()
    analysis = _run(_pnl(12))
    bets = analysis.bets
    total = len(analysis.ranking)
    assert len(bets) == total
    rec = analysis.recommended_n
    floor = min(settings.min_eligible, total)

    def step(size: int) -> float:
        """The share of gain from ``size`` Alphas to ``size + 1``."""
        return bets[size] / bets[size - 1] - 1.0

    if rec < total:
        assert rec >= floor
        assert step(rec) < settings.bets_min_gain
    if rec > floor:
        assert step(rec - 1) >= settings.bets_min_gain


def test_below_ten_recommends_every_alpha_available_and_warns() -> None:
    analysis = _run(_pnl(6))
    total = len(analysis.ranking)
    assert analysis.recommended_n == total and analysis.n == total
    assert analysis.below_minimum is True
    assert any("Below 10 alphas (unverified Osmosis minimum)" in w for w in analysis.warnings)


def test_n_is_clamped_to_the_cap_limits() -> None:
    analysis = _run(_pnl(12), n=2)
    assert analysis.n == 4  # ceil(1 / 0.25): the fewest Alphas whose caps reach 100%
    analysis = _run(_pnl(12), n=999)
    assert analysis.n == analysis.n_max == len(analysis.ranking)


def test_points_for_the_chosen_n_respect_the_cap_and_floor_and_sum_exactly() -> None:
    analysis = _run(_pnl(12), n=4)
    assert analysis.points is not None and len(analysis.chosen) == 4
    assert int(analysis.points.sum()) == 100_000
    assert (analysis.points == 25_000).all()  # four equal Alphas hit the cap exactly


def test_a_near_duplicate_is_excluded_and_named_against_its_partner() -> None:
    pnl = _pnl(8)
    noise = 1e-6 * np.random.default_rng(1).normal(size=len(pnl.index))
    pnl["S99"] = pnl["S00"] + noise
    analysis = _run(pnl)
    corr_out = [e for e in analysis.excluded if e.reason == "correlation"]
    dupes = {"S00", "S99"} & {e.alpha_id for e in corr_out}
    assert len(dupes) == 1  # exactly one of the pair is left out
    left_out = dupes.pop()
    entry = next(e for e in corr_out if e.alpha_id == left_out)
    assert entry.correlated_with == ({"S00", "S99"} - {left_out}).pop()
    assert entry.correlation is not None and entry.correlation > 0.7


def test_an_alpha_with_a_gap_in_the_window_is_excluded_as_a_gap() -> None:
    pnl = _pnl(10)
    pnl.loc[pnl.index[-50:], "S03"] = np.nan  # gap at the end: no complete window
    analysis = _run(pnl)
    gaps = [e.alpha_id for e in analysis.excluded if e.reason == "gap"]
    assert gaps == ["S03"]
    assert "S03" not in analysis.chosen


def test_a_flat_alpha_is_excluded_as_flat_not_ranked() -> None:
    pnl = _pnl(8)
    pnl["FLAT"] = 0.0
    analysis = _run(pnl)
    assert ("FLAT", "flat") in [(e.alpha_id, e.reason) for e in analysis.excluded]
    assert "FLAT" not in analysis.chosen


def test_combined_turnover_uses_each_alphas_own_figure() -> None:
    pnl = _pnl(12)
    turnover = dict.fromkeys(pnl.columns, 0.05)
    analysis = _run(pnl, turnover=turnover)
    assert analysis.combined["equal"]["turnover"] == pytest.approx(0.05)
    assert analysis.combined["allocation"]["turnover"] == pytest.approx(0.05)  # weights sum to 1


def test_the_judgement_cache_keys_on_values_not_only_shape() -> None:
    """Two frames of the same shape can hold different PnL: they must not share a walk."""
    pnl = _pnl(12)
    other = pnl.copy()
    other.iloc[:60, :11] = np.nan
    settings = small_settings()
    first, _ = scope_mod.judged_walk(pnl, settings)
    again, _ = scope_mod.judged_walk(pnl, settings)
    different, _ = scope_mod.judged_walk(other, settings)
    assert first is again
    assert different is not first and different.skipped_folds != first.skipped_folds


def test_a_requested_method_is_respected() -> None:
    analysis = _run(_pnl(12), method="max_sharpe")
    assert analysis.requested_method == "max_sharpe"
    assert analysis.method in {"max_sharpe", "equal_weight"}  # equal weight only on fallback
