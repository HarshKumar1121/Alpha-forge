"""Pool analytics: correlation matrix, hand-rolled dendrogram and PCA, N-alpha compare — all
pure functions over synthetic PnL series, no simulator needed.
"""

import math

import numpy as np
import pandas as pd
import pytest

from alpha_harness.forge.research import families as fam
from alpha_harness.forge.research import pool as pl

INDEX = pd.bdate_range("2018-01-01", periods=400)


def _series(
    seed: int, mu: float = 0.0, base: np.ndarray | None = None, rho: float = 0.0
) -> pd.Series:
    rng = np.random.default_rng(seed)
    x = rng.normal(mu, 0.01, len(INDEX))
    if base is not None:
        x = rho * base + math.sqrt(1 - rho**2) * x
    return pd.Series(x, index=INDEX)


def test_top_correlated_ranks_the_most_correlated_first() -> None:
    candidate = _series(1)
    close = _series(2, base=candidate.to_numpy(), rho=0.9)
    medium = _series(3, base=candidate.to_numpy(), rho=0.4)
    unrelated = _series(4)
    top = pl.top_correlated(
        candidate, {"close": close, "medium": medium, "unrelated": unrelated}, n=2
    )
    assert [aid for aid, _ in top] == ["close", "medium"]
    assert top[0][1] > top[1][1]


def test_top_correlated_drops_pairs_with_too_little_overlap() -> None:
    candidate = _series(1)
    short = pd.Series([1.0, 2.0], index=INDEX[:2])
    top = pl.top_correlated(candidate, {"short": short}, n=3)
    assert top == []


def test_correlation_matrix_is_symmetric_with_unit_diagonal() -> None:
    a, b, c = _series(1), _series(2), _series(3)
    corr = pl.correlation_matrix({"a": a, "b": b, "c": c})
    assert list(corr.index) == ["a", "b", "c"]
    values = corr.to_numpy(dtype=float)
    assert values[0, 0] == 1.0
    assert values[0, 1] == values[1, 0]


def test_hierarchical_clusters_merges_correlated_pair_first() -> None:
    a = _series(1)
    b = _series(2, base=a.to_numpy(), rho=0.95)  # a and b are nearly the same bet
    c = _series(3)  # unrelated
    corr = pl.correlation_matrix({"a": a, "b": b, "c": c})
    nodes = pl.hierarchical_clusters(corr)
    assert len(nodes) == 2  # two merges for three leaves
    first = nodes[0]
    assert {first.left, first.right} == {"a", "b"}
    assert first.distance < nodes[1].distance


def test_hierarchical_clusters_handles_fewer_than_two() -> None:
    assert pl.hierarchical_clusters(pl.correlation_matrix({"a": _series(1)})) == []
    assert pl.hierarchical_clusters(pl.correlation_matrix({})) == []


def test_pca_explained_variance_sums_to_one_and_favours_shared_factor() -> None:
    common = _series(1).to_numpy()
    series = {
        "a": _series(2, base=common, rho=0.9),
        "b": _series(3, base=common, rho=0.9),
        "c": _series(4, base=common, rho=0.9),
    }
    out = pl.pca(series)
    assert sum(out["explained_variance_ratio"]) == pytest.approx(1.0)
    assert out["explained_variance_ratio"][0] > 0.6  # one dominant shared bet
    assert set(out["loadings"]) == set(series)


def test_pca_too_few_series_is_empty() -> None:
    out = pl.pca({"a": _series(1)})
    assert out == {"explained_variance_ratio": [], "components": [], "loadings": {}}


def test_compare_matches_families_cluster_on_the_same_inputs() -> None:
    a = _series(1)
    b = _series(2, base=a.to_numpy(), rho=0.9)
    c = _series(3)
    series = {"a": a, "b": b, "c": c}
    scores = {"a": 1.5, "b": 1.2, "c": 0.9}
    out = pl.compare(series, scores)
    expected = fam.cluster(series, scores)
    assert len(out["families"]) == len(expected)
    assert not math.isnan(out["mean_abs_correlation"])
    assert 0.0 <= out["max_correlation"] <= 1.0
