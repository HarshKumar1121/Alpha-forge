"""Forge Gym Playground: simulate an expression on the synthetic Gym market, for free.

Nothing here reaches BRAIN or spends quota — the Gym is a local market with planted effects
(see :mod:`alpha_harness.forge.local.gym`). The tear sheet is the local backtest's full
output: BRAIN's headline figures plus the IC, IC decay and quintile diagnostics a desk reads
first.
"""

import asyncio
import math
from datetime import date
from functools import cache
from typing import Literal

from fastapi import APIRouter
from pydantic import Field

from ..brain.platform import DEFAULT_TRUNCATION, MAX_DECAY, MAX_TEST_PERIOD_YEARS
from ..forge.local.client import EXPRESSION_ERRORS, GymSimulator, settings_from
from ..forge.local.engine import LOCAL_OPERATORS
from ..forge.local.gym import N_DAYS
from ..forge.simulator import request_for
from ..schemas import Out
from .deps import refuse

router = APIRouter(prefix="/api/forge/gym", tags=["forge-gym"])

#: What the Gym can honour. Anything finer would be silently approximated, so it isn't offered.
GymNeutralization = Literal["NONE", "MARKET", "SECTOR", "INDUSTRY", "SUBINDUSTRY"]
GymUniverse = Literal["TOP3000", "TOP1000", "TOP500", "TOP200"]


@cache
def _gym() -> GymSimulator:
    """One per process. Built on first use, not at import: its rules come from YAML."""
    return GymSimulator()


def _num(value: float) -> float | None:
    """NaN means "not enough data"; on the wire that is ``null``, not a fake number."""
    return None if math.isnan(value) else float(value)


class GymRequest(Out):
    expression: str = Field(min_length=1, max_length=4000)
    decay: int = Field(default=0, ge=0, le=MAX_DECAY)
    neutralization: GymNeutralization = "INDUSTRY"
    universe: GymUniverse = "TOP3000"
    truncation: float = Field(default=DEFAULT_TRUNCATION, gt=0, le=1)
    test_years: int = Field(default=2, ge=0, le=MAX_TEST_PERIOD_YEARS)


class Block(Out):
    """BRAIN's headline figures for one period."""

    sharpe: float | None
    returns: float | None
    turnover: float | None
    fitness: float | None
    drawdown: float | None
    margin: float | None


class Ic(Out):
    mean: float | None
    t: float | None
    hit: float | None


class IcPoint(Out):
    horizon: int
    ic: float | None


class Quintile(Out):
    quintile: int
    #: Mean next-day return of the stocks in this fifth, as a fraction (0.0005 = 5 bps).
    mean_return: float | None


class YearSharpe(Out):
    year: int
    sharpe: float | None
    #: Wholly inside the held-out test period.
    in_test: bool


class GymCheck(Out):
    name: str
    result: Literal["PASS", "FAIL"]
    value: float | None
    limit: float | None


class PnlPoint(Out):
    day: date
    #: Cumulative PnL in dollars on the $20M book.
    pnl: float


class Applied(Out):
    """The settings as the Gym actually ran them — see the simulator docstring, point 8."""

    stocks: int
    decay: int
    neutralization: str
    truncation: float
    test_years: float


class GymTearSheet(Out):
    expression: str
    applied: Applied
    in_sample: Block
    train: Block
    test: Block
    test_start: date
    sub_universe_sharpe: float | None
    ic: Ic
    ic_decay: list[IcPoint]
    quintiles: list[Quintile]
    yearly: list[YearSharpe]
    checks: list[GymCheck]
    passed: bool
    pnl: list[PnlPoint]


class GymField(Out):
    name: str
    kind: Literal["MATRIX", "GROUP"]
    description: str
    coverage: float


class GymCatalog(Out):
    fields: list[GymField]
    operators: list[str]
    days: int
    stocks: int


def _block(stats: dict[str, float]) -> Block:
    return Block(**{k: _num(stats[k]) for k in Block.model_fields})


@router.post("/simulate")
async def simulate(body: GymRequest) -> GymTearSheet:
    """Run one expression on the Gym and return its tear sheet. Free; never touches BRAIN."""
    settings = {
        "region": "USA",
        "universe": body.universe,
        "delay": 1,
        "decay": body.decay,
        "neutralization": body.neutralization,
        "truncation": body.truncation,
        "testPeriod": f"P{body.test_years}Y0M0D",
    }
    request = request_for(body.expression, settings)
    gym = _gym()
    try:
        r = await asyncio.to_thread(gym.tear_sheet, request)
    except EXPRESSION_ERRORS as exc:
        raise refuse(422, "bad_expression", str(exc)) from exc
    applied = settings_from(request.settings, gym.market.n_stocks)
    test_start = r.test_start.date()
    cum = r.pnl.cumsum()
    return GymTearSheet(
        expression=body.expression,
        applied=Applied(
            stocks=applied.universe or gym.market.n_stocks,
            decay=applied.decay,
            neutralization=applied.neutralization,
            truncation=applied.truncation,
            test_years=applied.test_years,
        ),
        in_sample=_block(r.stats["is"]),
        train=_block(r.stats["train"]),
        test=_block(r.stats["test"]),
        test_start=test_start,
        sub_universe_sharpe=_num(r.sub_universe_sharpe),
        ic=Ic(mean=_num(r.ic["mean"]), t=_num(r.ic["t"]), hit=_num(r.ic["hit"])),
        ic_decay=[IcPoint(horizon=h, ic=_num(v)) for h, v in r.ic_decay.items()],
        quintiles=[
            Quintile(quintile=i + 1, mean_return=_num(q)) for i, q in enumerate(r.quintiles)
        ],
        yearly=[
            # A year that straddles the split is mostly research data: test only once wholly in it.
            YearSharpe(year=y, sharpe=_num(v), in_test=date(y, 1, 1) >= test_start)
            for y, v in r.yearly.items()
        ],
        checks=[
            GymCheck(
                name=c["name"], result=c["result"], value=_num(c["value"]), limit=_num(c["limit"])
            )
            for c in r.checks
        ],
        passed=all(c["result"] == "PASS" for c in r.checks),
        pnl=[
            PnlPoint(day=d.date(), pnl=float(v))
            for d, v in zip(cum.index, cum.to_numpy(), strict=True)
        ],
    )


@cache
def _catalog() -> GymCatalog:
    market = _gym().market
    return GymCatalog(
        fields=[GymField.model_validate(doc, from_attributes=True) for doc in market.describe()],
        operators=sorted(LOCAL_OPERATORS),
        days=N_DAYS,
        stocks=market.n_stocks,
    )


@router.get("/catalog")
async def catalog() -> GymCatalog:
    """The Gym's fields and operators. The answer key is deliberately not served."""
    return await asyncio.to_thread(_catalog)
