"""Field profiler: how does this data behave, before we spend simulations writing alphas on it?

Two ways to learn a field's behaviour:

1. **Local statistics** (the Gym, free): coverage, how often values change, how sticky its
   cross-sectional ranks are, and whether it is just a proxy for size.

2. **Probes** (BRAIN, 3 simulations): BRAIN never hands out raw field values, but three small
   alphas reveal the behaviour. They are not wasted: each probe is itself a candidate alpha.

       P1 level   rank(B)                       turnover -> how fast the field changes
       P2 change  rank(ts_delta(B, 21))         does the CHANGE carry information?
       P3 history ts_rank(B, 252)               does deviation from OWN past matter?

   where B = ts_backfill(field, 63). The sign of each probe's Sharpe tells us the direction.

The output is an archetype (slow / medium / persistent / fast), flags (sparse, size_linked),
the best "angle" (level / change / history) and its sign. Templates are matched to that.
Thresholds live in ``config/forge/court.yaml`` under ``archetypes``.

Ported from alpha-copilot ``research/profiler.py``.
"""

import math
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from .. import analytics as an
from .. import config
from .templates import clean

if TYPE_CHECKING:
    from collections.abc import Mapping

type Profile = dict[str, Any]
#: How many stocks the update-frequency estimate samples: plenty for a median, and cheap.
_UPDATE_SAMPLE = 200


def archetype_config() -> dict[str, Any]:
    return config.court()["archetypes"]


def probes(field: str, vector: bool = False) -> dict[str, str]:
    b = clean(field, vector=vector)
    return {
        "level": f"rank({b})",
        "change": f"rank(ts_delta({b}, 21))",
        "history": f"ts_rank({b}, 252)",
    }


# --- local statistics ----------------------------------------------------------------------------


def _corr_on(a: pd.DataFrame, b: pd.DataFrame, days: pd.Index) -> float:
    """Mean cross-sectional correlation of two rank panels on the sampled days."""
    values = [float(a.loc[d].corr(b.loc[d])) for d in days]
    return float(np.nanmean(values)) if values and not all(map(math.isnan, values)) else math.nan


def profile_local(values: pd.DataFrame, cap: pd.DataFrame | None = None) -> Profile:
    """Behaviour from raw data. ``values``: dates x stocks."""
    cfg = archetype_config()
    # Coverage as an alpha sees it: has a value within the last 63 days (i.e. after backfill).
    coverage = float(values.ffill(limit=63).notna().to_numpy().mean())
    density = float(values.notna().to_numpy().mean())
    filled = values.ffill()
    changed = filled.diff().abs() > 1e-12
    # Median days between changes, per stock, then across stocks.
    gaps: list[float] = []
    for col in filled.columns[:_UPDATE_SAMPLE]:
        idx = np.flatnonzero(changed[col].to_numpy())
        if len(idx) > 2:
            gaps.append(float(np.median(np.diff(idx))))
    update_days = float(np.median(gaps)) if gaps else math.nan
    ranks = filled.rank(axis=1, pct=True)
    sample = ranks.index[252::21]
    rank_persistence = _corr_on(ranks, ranks.shift(21), sample)
    size_corr = math.nan
    if cap is not None:
        size_corr = _corr_on(ranks, cap.rank(axis=1, pct=True), sample)
    flat = pd.Series(values.to_numpy().ravel()).dropna()
    skew = an.moment(flat.skew()) if len(flat) > 10 else math.nan

    local = cfg["local"]
    if update_days >= local["slow_update_days"]:
        arch = "slow"
    elif rank_persistence >= local["persistent_rank_corr"]:
        arch = "persistent"
    elif rank_persistence < local["fast_rank_corr"]:
        arch = "fast"
    else:
        arch = "medium"
    return {
        "source": "local",
        "archetype": arch,
        "coverage": coverage,
        "raw_density": density,
        "update_days": update_days,
        "rank_persistence": rank_persistence,
        "size_corr": size_corr,
        "skew": skew,
        "sparse": coverage < cfg["sparse_coverage"],
        "size_linked": not math.isnan(size_corr) and abs(size_corr) > cfg["size_linked_corr"],
        "notes": _notes(arch, coverage, update_days, size_corr, skew),
    }


def _notes(arch: str, cov: float, upd: float, size: float, skew: float) -> list[str]:
    n = [
        {
            "slow": f"Changes about every {upd:.0f} days: think levels, changes and update events.",
            "persistent": "Daily but sticky ranks: compare to the stock's own history.",
            "medium": "Drifts over weeks: changes and surprises vs history.",
            "fast": "Noisy day to day: smooth it (ts_mean/decay) or it will churn the book.",
        }[arch]
    ]
    if cov < 0.6:
        n.append(f"Coverage {cov:.0%}: backfill, and expect a concentrated book.")
    if not math.isnan(size) and abs(size) > 0.3:
        n.append(
            f"Rank corr with size {size:+.2f}: partly a size bet — neutralize or scale "
            "by liquidity, and check the large-cap sub-universe."
        )
    if not math.isnan(skew) and abs(skew) > 3:
        n.append(f"Skew {skew:.1f}: heavy tails — rank() before anything else.")
    return n


# --- probe-based profile --------------------------------------------------------------------------


def profile_from_probes(results: Mapping[str, Mapping[str, Any] | None]) -> Profile:
    """``results``: probe name -> BRAIN-shaped alpha (``{'is': {...}, 'train': {...}}``)."""
    th = archetype_config()["probe_turnover"]

    def get(name: str, block: str, key: str) -> float:
        v = ((results.get(name) or {}).get(block) or {}).get(key)
        return float(v) if v is not None else math.nan

    to = get("level", "is", "turnover")
    if math.isnan(to):
        arch = "medium"
    elif to < th["slow"]:
        arch = "slow"
    elif to < th["medium"]:
        history, change = get("history", "train", "sharpe"), get("change", "train", "sharpe")
        arch = "persistent" if history**2 > change**2 else "medium"
    else:
        arch = "fast"
    # Choose the angle on TRAIN Sharpe only, so the test years stay unseen.
    angles = {k: get(k, "train", "sharpe") for k in ("level", "change", "history")}
    angles = {k: (v if not math.isnan(v) else get(k, "is", "sharpe")) for k, v in angles.items()}
    valid = {k: v for k, v in angles.items() if not math.isnan(v)}
    best = max(valid, key=lambda k: abs(valid[k])) if valid else "level"
    sign = 1 if valid.get(best, 0) >= 0 else -1
    long_short = get("level", "is", "longCount") + get("level", "is", "shortCount")
    return {
        "source": "probes",
        "archetype": arch,
        "level_turnover": to,
        "angles": angles,
        "best_angle": best,
        "sign": sign,
        "positions": None if math.isnan(long_short) else long_short,
        "sparse": False,
        "size_linked": False,
        "notes": [
            f"Level-probe turnover {to:.1%} -> {arch}.",
            f"Strongest angle: {best} (train Sharpe {valid.get(best, math.nan):+.2f}); "
            f"{'use as is' if sign > 0 else 'flip the sign'}.",
        ],
    }


def merge(local: Profile | None, probe: Profile | None) -> Profile:
    """Raw data measures behaviour directly; probes say which angle pays and its sign. So the
    archetype and flags come from local statistics when both exist."""
    if local and probe:
        return {
            **local,
            **probe,
            "source": "local+probes",
            "archetype": local["archetype"],
            "sparse": local["sparse"],
            "size_linked": local["size_linked"],
            "notes": local["notes"] + probe["notes"],
        }
    return dict(probe or local or {"archetype": "medium", "sign": 1, "notes": []})
