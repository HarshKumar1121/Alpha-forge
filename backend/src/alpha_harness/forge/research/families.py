"""Families: group alphas that are really the same bet, so you never submit near-duplicates.

Two alphas are one family when their daily PnL correlation is above a threshold (default 0.6,
stricter than BRAIN's 0.7 self-correlation so you keep headroom). Leader clustering: walk the
alphas from best to worst; each joins the first family whose leader it correlates with, or
starts a new family. Deterministic and easy to explain. One submission per family, at most.

Ported from alpha-copilot ``research/families.py``.
"""

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import analytics as an

if TYPE_CHECKING:
    import pandas as pd


@dataclass(slots=True)
class Family:
    leader: str
    members: list[str] = field(default_factory=list[str])
    signature: str = ""
    collides_with_pool: tuple[str | None, float] | None = None


def cluster(
    series: dict[str, pd.Series],
    scores: dict[str, float],
    threshold: float = 0.6,
    signatures: dict[str, str] | None = None,
) -> list[Family]:
    def rank_key(k: str) -> float:
        s = scores.get(k, math.nan)
        return math.inf if math.isnan(s) else -s

    fams: list[Family] = []
    for aid in sorted(series, key=rank_key):
        for f in fams:
            c = an.correlation(series[aid], series[f.leader], min_overlap=120)
            if not math.isnan(c) and c >= threshold:
                f.members.append(aid)
                break
        else:
            fams.append(Family(aid, [aid], (signatures or {}).get(aid, "")))
    return fams


def against_pool(
    fams: list[Family],
    series: dict[str, pd.Series],
    pool: dict[str, pd.Series],
    ceiling: float = 0.7,
) -> None:
    """Mark families whose leader already collides with something you submitted."""
    if not pool:
        return
    for f in fams:
        best = an.max_correlation(series[f.leader], pool)
        f.collides_with_pool = best if best[1] >= ceiling else None


def signature(fields: list[str], template_family: str) -> str:
    """Structural fallback when PnL isn't available: same fields + same template family."""
    return f"{template_family}:{'+'.join(sorted(fields))}"


def report(fams: list[Family], meta: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, f in enumerate(fams, 1):
        m = meta.get(f.leader, {})
        rows.append(
            {
                "family": i,
                "leader": f.leader,
                "size": len(f.members),
                "expression": m.get("expression"),
                "train_sharpe": m.get("train_sharpe"),
                "pool_collision": f.collides_with_pool,
                "submit_candidate": f.collides_with_pool is None,
            }
        )
    return rows
