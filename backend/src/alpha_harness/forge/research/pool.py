"""Pool view: correlation matrix, dendrogram, PCA and N-alpha compare across a set of alphas.

No scipy/sklearn: this backend has never depended on either (``families.py``'s leader
clustering was a deliberate "deterministic and easy to explain" choice over a library), so the
dendrogram and PCA here are hand-rolled on numpy/pandas, the same way ``analytics.py`` hand-rolls
``deflated_sharpe``. Family grouping itself is not reimplemented — :mod:`families` already does
exactly that; this module adds the matrix, the dendrogram and PCA around it.
"""

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .. import analytics as an
from . import families as fam


def top_correlated(
    candidate: pd.Series, pool: dict[str, pd.Series], n: int = 3
) -> list[tuple[str, float]]:
    """The ``n`` pool alphas whose PnL correlates most with ``candidate``'s, highest first.

    Pairs with too little overlap to correlate (see :func:`~..analytics.correlation`) are left
    out rather than sorted in as NaN.
    """
    pairs = ((aid, an.correlation(candidate, s)) for aid, s in pool.items())
    usable = [(aid, c) for aid, c in pairs if not math.isnan(c)]
    usable.sort(key=lambda p: p[1], reverse=True)
    return usable[:n]


def correlation_matrix(series: dict[str, pd.Series]) -> pd.DataFrame:
    """Pairwise Pearson correlation, NaN-safe per pair (pandas' own pairwise handling)."""
    ids = list(series)
    if not ids:
        return pd.DataFrame()
    df = pd.concat(series, axis=1)
    return df.corr()


@dataclass(slots=True)
class ClusterNode:
    """One merge of an average-linkage dendrogram, in the order they happened."""

    id: int
    left: int | str
    right: int | str
    distance: float
    members: list[str] = field(default_factory=list)


def hierarchical_clusters(corr: pd.DataFrame) -> list[ClusterNode]:
    """Average-linkage (UPGMA) agglomerative clustering on ``1 - correlation`` distance.

    Returns the merges in order, like ``scipy.cluster.hierarchy.linkage`` would, but hand-rolled:
    repeatedly merge the two closest clusters (Lance-Williams average-linkage update) until one
    remains. O(n^3), fine at pool scale (tens of alphas, not thousands).
    """
    ids = list(corr.index)
    n = len(ids)
    if n < 2:
        return []
    base = (1.0 - corr.to_numpy()).astype(float)
    members: dict[int, list[str]] = {i: [ids[i]] for i in range(n)}
    sizes: dict[int, int] = dict.fromkeys(range(n), 1)
    dist: dict[tuple[int, int], float] = {
        (i, j): base[i, j] for i in range(n) for j in range(i + 1, n)
    }
    active = set(range(n))
    next_id = n
    nodes: list[ClusterNode] = []
    while len(active) > 1:
        a, b = min(
            ((i, j) for (i, j) in dist if i in active and j in active),
            key=lambda k: dist[k],
        )
        d = dist[a, b]
        size_a, size_b = sizes[a], sizes[b]
        new_id = next_id
        next_id += 1
        for c in active - {a, b}:
            d_ac = dist[min(a, c), max(a, c)]
            d_bc = dist[min(b, c), max(b, c)]
            merged = (size_a * d_ac + size_b * d_bc) / (size_a + size_b)
            dist[min(c, new_id), max(c, new_id)] = merged
        active -= {a, b}
        active.add(new_id)
        members[new_id] = members[a] + members[b]
        sizes[new_id] = size_a + size_b
        nodes.append(
            ClusterNode(
                id=new_id,
                left=ids[a] if a < n else a,
                right=ids[b] if b < n else b,
                distance=float(d),
                members=list(members[new_id]),
            )
        )
    return nodes


def pca(series: dict[str, pd.Series]) -> dict[str, Any]:
    """Principal components of the pool's correlation matrix: which alphas share a common bet.

    Returns explained-variance ratios (one per component, descending) and each alpha's loading
    on every component — what a PC1-vs-PC2 scatter plots.
    """
    empty: dict[str, Any] = {"explained_variance_ratio": [], "components": [], "loadings": {}}
    if len(series) < 2:
        return empty
    aligned = pd.concat(series, axis=1, join="inner").dropna()
    if aligned.empty:
        return empty
    std = aligned.std(ddof=0)
    standardized = ((aligned - aligned.mean()) / std.replace(0, np.nan)).dropna(axis=1)
    if standardized.shape[1] < 2:
        return empty
    corr = standardized.corr().to_numpy()
    eigvals, eigvecs = np.linalg.eigh(corr)
    order = np.argsort(eigvals)[::-1]
    eigvals, eigvecs = eigvals[order], eigvecs[:, order]
    total = float(eigvals.sum())
    explained = [float(v / total) for v in eigvals] if total > 0 else []
    cols = list(standardized.columns)
    return {
        "explained_variance_ratio": explained,
        "components": eigvecs.T.tolist(),
        "loadings": {aid: eigvecs[i, :].tolist() for i, aid in enumerate(cols)},
    }


def compare(series: dict[str, pd.Series], scores: dict[str, float]) -> dict[str, Any]:
    """N-alpha compare: families (reusing :mod:`families` as-is) plus pool-wide correlation
    summary stats."""
    fams = fam.cluster(series, scores)
    meta = {aid: {"expression": None, "train_sharpe": scores.get(aid)} for aid in series}
    values = correlation_matrix(series).to_numpy(dtype=float)
    pairs = [
        float(values[i, j])
        for i in range(len(values))
        for j in range(i + 1, len(values))
        if not math.isnan(values[i, j])
    ]
    return {
        "families": fam.report(fams, meta),
        "mean_abs_correlation": float(np.mean(np.abs(pairs))) if pairs else math.nan,
        "max_correlation": float(np.max(pairs)) if pairs else math.nan,
    }
