"""Correlation, in two explicit stages that are never merged into one number.

- **Self-prescreen**: the candidate's PnL against the local synced pool
  (:func:`..research.pool.top_correlated`) — free, instant, and only as good as what has been
  synced. Labelled so nobody mistakes it for BRAIN's answer.
- **Prod-final**: BRAIN's own verdict from ``/alphas/{id}/correlations/{kind}``
  (:meth:`...brain.endpoints.BrainEndpoints.correlations`) — authoritative, but it costs a
  round trip and only exists once the alpha has an id.

A screen that shows only one of these, or averages them, is showing something BRAIN never
said. The Submittable Board and tear sheet (CLAUDE.md UI conventions: every number needs a
caption) must carry both, each under its own label.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..research import pool as pl

if TYPE_CHECKING:
    import pandas as pd


@dataclass(frozen=True, slots=True)
class CorrelationStage:
    label: str
    #: ``None`` when nothing could be computed (e.g. an empty pool, or BRAIN has not decided).
    value: float | None
    #: Which other alpha produced ``value``, for the self-prescreen stage.
    against: str | None
    caption: str


SELF_PRESCREEN_CAPTION = (
    "Local estimate against your synced pool. Free and instant, but only as good as what you "
    "have synced — not BRAIN's verdict."
)
PROD_FINAL_CAPTION = "BRAIN's actual correlation check. Authoritative."


def self_prescreen(candidate: pd.Series, pool: dict[str, pd.Series]) -> CorrelationStage:
    """The local pool alpha most correlated with ``candidate``, labelled as an estimate."""
    top = pl.top_correlated(candidate, pool, n=1)
    if not top:
        return CorrelationStage(
            "self_prescreen", value=None, against=None, caption=SELF_PRESCREEN_CAPTION
        )
    against, value = top[0]
    return CorrelationStage(
        "self_prescreen", value=value, against=against, caption=SELF_PRESCREEN_CAPTION
    )


def _max_abs_correlation(body: dict[str, Any]) -> float | None:
    """The worst (largest magnitude) pairwise correlation in a BRAIN ``/correlations`` body.

    BRAIN's recordset shape is rows of ``{..., "correlation": float}``; a body this cannot
    read (a different shape, or no rows) yields ``None`` rather than a guessed number.
    """
    rows = body.get("records") or body.get("rows") or []
    values = [
        float(r["correlation"])
        for r in rows
        if isinstance(r, dict) and r.get("correlation") is not None
    ]
    return max((abs(v) for v in values), default=None)


async def prod_final(endpoints: Any, alpha_id: str, *, kind: str = "self") -> CorrelationStage:
    """BRAIN's own verdict for ``alpha_id`` — ``kind`` is ``self``, ``prod`` or ``power-pool``,
    as :meth:`BrainEndpoints.correlations` takes it."""
    body = await endpoints.correlations(alpha_id, kind)
    return CorrelationStage(
        "prod_final", value=_max_abs_correlation(body), against=None, caption=PROD_FINAL_CAPTION
    )
