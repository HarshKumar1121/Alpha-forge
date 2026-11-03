"""The Gym as a :class:`~..simulator.Simulator`: the whole research pipeline, for free.

Only the local engine's operators are available, and BRAIN settings are approximated for a
400-stock market (see :func:`settings_from`). Results live in this object's memory, which is
why ``ephemeral`` is True: callers save the PnL they need straight away.

Ported from alpha-copilot ``local/client.py``, recast from its synchronous
build/start/wait/alpha/pnl calls onto the async Protocol.
"""

import asyncio
import re
from typing import TYPE_CHECKING, Any

import pandas as pd
import structlog

from ...engine.lifecycle import request_hash
from .. import alpha_types
from ..expr import ParseError
from ..simulator import SimOutcome
from .engine import EvalError
from .gym import Market, make_market
from .sim import CheckLimits, SimResult, SimSettings, as_brain_alpha, simulate

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from ...brain.schemas import SimulationRequest, SimulationSettings

log = structlog.get_logger(__name__)

#: BRAIN neutralizations the Gym can honour. Anything else (the risk-model neutralizations,
#: COUNTRY, …) falls back to MARKET: the Gym has no risk model and a single country.
NEUTRAL = {
    "SUBINDUSTRY": "INDUSTRY",
    "INDUSTRY": "INDUSTRY",
    "SECTOR": "SECTOR",
    "MARKET": "MARKET",
    "NONE": "NONE",
}
#: Errors a user's expression can raise inside pandas. Anything else is a bug and propagates.
EXPRESSION_ERRORS = (ParseError, EvalError, ValueError, TypeError, KeyError, ArithmeticError)

_PERIOD = re.compile(r"^P(\d+)Y(\d+)M")


def _test_years(test_period: str | None) -> float:
    """``P2Y0M0D`` -> 2.0. No test period means everything is train."""
    m = _PERIOD.match(test_period or "")
    return int(m.group(1)) + int(m.group(2)) / 12 if m else 0.0


def settings_from(s: SimulationSettings, n_stocks: int) -> SimSettings:
    """Map BRAIN settings onto the Gym.

    BRAIN universes are thousands of names; the Gym has hundreds. Map by proportion:
    TOP3000 -> all, TOP1000 -> top third, TOP500 -> top sixth (never fewer than 30).
    """
    digits = "".join(ch for ch in s.universe if ch.isdigit())
    size = int(digits) if digits else 3000
    return SimSettings(
        universe=None if size >= 3000 else max(30, int(n_stocks * size / 3000)),
        decay=s.decay,
        neutralization=NEUTRAL.get(s.neutralization, "MARKET"),
        truncation=s.truncation,
        test_years=_test_years(s.test_period),
    )


class GymSimulator:
    """Satisfies both :class:`~..simulator.Simulator` and :class:`~..simulator.FieldSource`."""

    name = "gym"
    costs_quota = False
    ephemeral = True
    batch_size = 10

    def __init__(self, seed: int = 7, limits: CheckLimits | None = None) -> None:
        self.seed = seed
        self.limits = limits or CheckLimits.from_alpha_type(alpha_types.load("regular"))
        self._pnl: dict[str, pd.Series] = {}
        self._turnover: dict[str, pd.Series] = {}
        self._yearly: dict[str, dict[int, float]] = {}

    @property
    def market(self) -> Market:
        return make_market(self.seed)

    # -- FieldSource ----------------------------------------------------------------------------
    def field_names(self) -> set[str]:
        return set(self.market.fields)

    def frame(self, field: str) -> pd.DataFrame:
        return self.market.fields[field]

    def groups(self) -> dict[str, pd.Series]:
        return self.market.groups

    # -- Simulator ------------------------------------------------------------------------------
    def alpha_id(self, request: SimulationRequest) -> str:
        """Content-addressed: the same request on the same market is the same alpha, in any
        process. A counter would hand two different alphas the same id across runs."""
        return f"GYM{self.seed}-{request_hash(request)[:16]}"

    async def simulate(self, requests: Sequence[SimulationRequest]) -> list[SimOutcome]:
        # CPU-bound pandas work: off the event loop, or it stalls the tracker and the socket.
        return await asyncio.to_thread(self._run_all, list(requests))

    async def pnl(self, alpha_id: str) -> list[tuple[date, float]]:
        return self._rows(self._pnl.get(alpha_id))

    async def turnover(self, alpha_id: str) -> list[tuple[date, float]]:
        return self._rows(self._turnover.get(alpha_id))

    async def yearly_stats(self, alpha_id: str) -> list[dict[str, Any]]:
        """Synthetic yearly stats: the per-year Sharpe the Gym already computes for its own
        tear sheet (``SimResult.yearly``). No turnover/returns breakdown — the Gym approximates
        BRAIN's ``yearly-stats`` recordset only as far as the Playground itself needs."""
        return [{"year": y, "sharpe": s} for y, s in sorted(self._yearly.get(alpha_id, {}).items())]

    @staticmethod
    def _rows(series: pd.Series | None) -> list[tuple[date, float]]:
        if series is None:
            return []
        days = pd.DatetimeIndex(series.index)
        return [(d.date(), float(v)) for d, v in zip(days, series.to_numpy(), strict=True)]

    def tear_sheet(self, request: SimulationRequest) -> SimResult:
        """The full local result — IC, quintiles, weights — for one request. Raises on a bad
        expression; the Playground shows the message."""
        return simulate(
            request.regular or "",
            self.market.fields,
            self.market.groups,
            settings_from(request.settings, self.market.n_stocks),
            limits=self.limits,
        )

    def _run_all(self, requests: list[SimulationRequest]) -> list[SimOutcome]:
        return [self._run_one(r) for r in requests]

    def _run_one(self, request: SimulationRequest) -> SimOutcome:
        aid = self.alpha_id(request)
        try:
            result = self.tear_sheet(request)
        except EXPRESSION_ERRORS as exc:
            log.debug("gym simulation failed", expression=request.regular, error=str(exc))
            return SimOutcome(request, None, None, error=str(exc))
        self._pnl[aid] = result.pnl
        self._turnover[aid] = result.turnover
        self._yearly[aid] = result.yearly
        alpha: dict[str, Any] = {**as_brain_alpha(result), "id": aid}
        return SimOutcome(request, aid, alpha)
