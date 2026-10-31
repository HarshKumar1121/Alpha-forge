"""The crowding score: percentile ranks of alphaCount and userCount within one scope."""

from typing import TYPE_CHECKING, Any

import pytest
import yaml

from alpha_harness.forge import config as forge_config
from alpha_harness.forge.data.crowding import CrowdingWeights, crowding_scores, weights

if TYPE_CHECKING:
    from pathlib import Path

EVEN = CrowdingWeights(alpha_count=0.5, user_count=0.5)


def _rows(*counts: tuple[int | None, int | None]) -> list[dict[str, Any]]:
    return [{"id": f"f{i}", "alpha_count": a, "user_count": u} for i, (a, u) in enumerate(counts)]


def test_more_alphas_and_more_users_mean_more_crowded() -> None:
    scores = crowding_scores(_rows((0, 0), (10, 5), (5000, 900)), key="id", w=EVEN)
    assert scores["f0"] < scores["f1"] < scores["f2"]  # type: ignore[operator]


def test_mid_rank_percentiles_are_exact() -> None:
    # Four distinct values: ranks (0.5, 1.5, 2.5, 3.5) / 4.
    scores = crowding_scores(_rows((1, 1), (2, 2), (3, 3), (4, 4)), key="id", w=EVEN)
    assert scores == {"f0": 0.125, "f1": 0.375, "f2": 0.625, "f3": 0.875}


def test_heavy_tails_do_not_squash_ordinary_fields() -> None:
    # One giant count must not push everything else to ~0, as a share of the max would.
    scores = crowding_scores(_rows((1, 1), (2, 2), (1_000_000, 1_000_000)), key="id", w=EVEN)
    assert scores["f1"] == pytest.approx(0.5)


def test_ties_share_one_rank() -> None:
    scores = crowding_scores(_rows((7, 7), (7, 7), (7, 7)), key="id", w=EVEN)
    assert set(scores.values()) == {0.5}


def test_a_single_row_is_neither_crowded_nor_empty() -> None:
    assert crowding_scores(_rows((42, 3)), key="id", w=EVEN) == {"f0": 0.5}


def test_a_missing_count_lets_the_other_carry_the_weight() -> None:
    scores = crowding_scores(_rows((1, 1), (2, None), (3, 3)), key="id", w=EVEN)
    assert scores["f1"] == pytest.approx(0.5)  # alpha rank of 2 among (1, 2, 3)


def test_no_counts_at_all_is_unknown_not_uncrowded() -> None:
    scores = crowding_scores(_rows((None, None), (3, 3)), key="id", w=EVEN)
    assert scores["f0"] is None


def test_scores_stay_between_zero_and_one() -> None:
    rows = _rows(*((a, u) for a in range(0, 50, 7) for u in range(0, 30, 4)))
    scores = crowding_scores(rows, key="id", w=CrowdingWeights(alpha_count=0.8, user_count=0.2))
    assert all(0 < s < 1 for s in scores.values() if s is not None)


def test_weights_come_from_learning_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "learning.yaml").write_text(
        yaml.safe_dump({"crowding": {"alpha_count_weight": 3, "user_count_weight": 1}})
    )
    monkeypatch.setenv("AH_FORGE_CONFIG_DIR", str(tmp_path))
    forge_config.reload()
    try:
        assert weights() == CrowdingWeights(alpha_count=0.75, user_count=0.25)
        # Users alone decide it when the alphas tie: 0.75 * 0.5 + 0.25 * rank(user).
        scores = crowding_scores(_rows((5, 1), (5, 9)), key="id")
        assert scores == {"f0": 0.4375, "f1": 0.5625}
    finally:
        monkeypatch.delenv("AH_FORGE_CONFIG_DIR")
        forge_config.reload()


def test_the_shipped_weights_are_even() -> None:
    forge_config.reload()
    assert weights() == EVEN
