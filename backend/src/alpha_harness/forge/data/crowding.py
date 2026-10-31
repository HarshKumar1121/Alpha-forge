"""The crowding score: how much competition a field or dataset already has, from 0 to 1.

Why it matters. BRAIN reports two counts for every field and dataset:

- ``alphaCount`` — alphas already built on it. A high count means the obvious signals are
  mined; a new alpha there is more likely to correlate with existing ones and fail the
  self-correlation check, and it adds less to the pool.
- ``userCount`` — researchers who used it. Many users means many independent attempts at the
  same ideas, even where the alpha count is modest.

Both are heavy-tailed: a few price/volume fields have counts thousands of times higher than a
typical field, so a raw count (or its mean) says little about an ordinary one. The score
therefore uses each count's **percentile rank within the scope**:

    crowding = w_alpha * pct_rank(alphaCount) + w_user * pct_rank(userCount)

with the weights in ``config/forge/learning.yaml`` under ``crowding`` (they sum to 1, so the
score stays in [0, 1]). 0.9 reads as "more crowded than about 90% of what this market offers".

``pct_rank`` is the mid-rank percentile ``(below + 0.5 * equal) / n``: ties share one rank, a
single item scores 0.5, and no item is ever ranked below everything or above everything —
though in a market of 76,000 fields the extremes round to 0.0 and 1.0 at the four decimals
this reports. A missing count is left out and the other count carries the whole weight; with
neither count the score is ``None`` — unknown, never "uncrowded".

The score is relative to the scope it was computed in, so compare scores within one scope.
"""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any

from .. import config as forge_config

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class CrowdingWeights:
    alpha_count: float = 0.5
    user_count: float = 0.5


@cache
def weights() -> CrowdingWeights:
    """The weights from ``learning.yaml``, normalised to sum to 1."""
    raw: dict[str, Any] = forge_config.learning().get("crowding") or {}
    a = float(raw.get("alpha_count_weight", 0.5))
    u = float(raw.get("user_count_weight", 0.5))
    if a < 0 or u < 0 or a + u <= 0:
        raise ValueError("learning.yaml crowding weights must be >= 0 and not both 0")
    return CrowdingWeights(alpha_count=a / (a + u), user_count=u / (a + u))


def _ranker(values: Iterable[float | int | None]) -> Any:
    """A function from a value to its mid-rank percentile among ``values`` (``None`` ignored)."""
    ordered = sorted(v for v in values if v is not None)
    n = len(ordered)

    def rank(value: float | int | None) -> float | None:
        if value is None or n == 0:
            return None
        below = bisect_left(ordered, value)
        equal = bisect_right(ordered, value) - below
        return (below + 0.5 * equal) / n

    return rank


def crowding_scores(
    rows: Iterable[Mapping[str, Any]],
    *,
    key: str,
    w: CrowdingWeights | None = None,
) -> dict[str, float | None]:
    """Crowding per row id, ranked against every row given (the whole scope, not a page).

    Each row needs ``key`` (its id), ``alpha_count`` and ``user_count``.
    """
    w = w or weights()
    items = list(rows)
    alpha_rank = _ranker(r.get("alpha_count") for r in items)
    user_rank = _ranker(r.get("user_count") for r in items)
    scores: dict[str, float | None] = {}
    for r in items:
        parts = [
            (w.alpha_count, alpha_rank(r.get("alpha_count"))),
            (w.user_count, user_rank(r.get("user_count"))),
        ]
        known = [(weight, p) for weight, p in parts if p is not None and weight > 0]
        total = sum(weight for weight, _ in known)
        scores[str(r[key])] = (
            round(sum(weight * p for weight, p in known) / total, 4) if total > 0 else None
        )
    return scores
