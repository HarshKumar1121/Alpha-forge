"""``GET /api/forge/quota`` and ``POST /api/forge/quota/estimate``: where today's simulations go.

Read-only. Nothing here sends, reserves or spends a simulation. The figures come from the Quota
Governor (:mod:`alpha_harness.forge.quota.governor`), which is the single authority on how much
of BRAIN's day each lane may use.
"""

import math

from fastapi import APIRouter
from pydantic import Field

from ..forge.quota.governor import DayState, QuotaGovernor
from ..forge.quota.ledger import platform_day_bounds
from ..schemas import Out
from .deps import State, refuse

router = APIRouter(prefix="/api/forge/quota", tags=["forge-quota"])


class QuotaLane(Out):
    lane: str
    #: One plain sentence on what the lane is for, from ``lanes.yaml``.
    about: str
    share: float
    #: ``share x budget``: what the lane was promised for the day.
    planned: int
    #: What it may use right now: its share plus quota idle lanes have released to it.
    effective_cap: int
    spent: int
    queued: int
    cached: int
    #: What it may still plan today, after its queue.
    remaining: int


class QuotaHour(Out):
    #: ``"HH:00"`` on the user's clock (``lanes.yaml`` timezone).
    hour: str
    spent: int


class QuotaPacing(Out):
    max_per_hour: int
    #: Sent in the rolling last 60 minutes.
    last_hour: int
    quiet_now: bool
    quiet_hours: list[str]


class QuotaToday(Out):
    #: BRAIN's day, ``YYYY-MM-DD`` US Eastern.
    day: str
    resets_in_seconds: int
    limit: int
    safety_margin: int
    #: ``limit - safetyMargin``: the most the governor will let through today.
    budget: int
    spent: int
    #: Spent per BRAIN beyond the ledger (e.g. run on the website); counted in ``reserve``.
    unrecorded: int
    cached: int
    queued: int
    remaining: int
    #: True when BRAIN's own quota header from today was used.
    exact: bool
    brain_remaining: int | None
    margin_warning: bool
    day_elapsed: float
    timezone: str
    pacing: QuotaPacing
    lanes: list[QuotaLane]
    hourly: list[QuotaHour]


class QuotaEstimateRequest(Out):
    lane: str = Field(min_length=1, max_length=32)
    simulations: int = Field(ge=0, le=100_000)
    #: 4 for region-agnostic simulations, which BRAIN charges once per region.
    cost_each: int = Field(default=1, ge=1, le=4)


class QuotaEstimate(Out):
    lane: str
    asked: int
    admitted: int
    trimmed: int
    admitted_cost: int
    reason: str
    lane_remaining: int
    global_remaining: int
    #: At the hourly pacing cap, how long the admitted part takes to send.
    hours_to_send: float


def _governor(state: State) -> QuotaGovernor:
    governor = state.engine.governor
    if governor is None:
        raise refuse(503, "QUOTA_GOVERNOR_OFF", "The Quota Governor is not running.")
    return governor


def _today(day: DayState, hourly: dict[str, int], governor: QuotaGovernor) -> QuotaToday:
    config = governor.config()
    return QuotaToday(
        day=day.day,
        resets_in_seconds=round(day.resets_in_seconds),
        limit=day.limit,
        safety_margin=day.safety_margin,
        budget=day.budget,
        spent=day.spent,
        unrecorded=day.unrecorded,
        cached=day.cached,
        queued=day.queued,
        remaining=day.remaining,
        exact=day.exact,
        brain_remaining=day.brain_remaining,
        margin_warning=day.margin_warning,
        day_elapsed=round(day.day_elapsed, 4),
        timezone=config.timezone.key,
        pacing=QuotaPacing(
            max_per_hour=config.max_per_hour,
            last_hour=day.last_hour,
            quiet_now=day.quiet_now,
            quiet_hours=[
                f"{q.start.strftime('%H:%M')}-{q.end.strftime('%H:%M')}" for q in config.quiet_hours
            ],
        ),
        lanes=[
            QuotaLane(
                lane=lane.lane,
                about=lane.about,
                share=lane.share,
                planned=math.floor(lane.planned),
                effective_cap=math.floor(lane.effective_cap),
                spent=lane.spent,
                queued=lane.queued,
                cached=lane.cached,
                remaining=math.floor(lane.remaining),
            )
            for lane in day.lanes.values()
        ],
        hourly=[
            QuotaHour(hour=f"{h:02d}:00", spent=hourly.get(f"{h:02d}:00", 0))
            for h in _hours_from(platform_day_bounds(day.now)[0].astimezone(config.timezone).hour)
        ],
    )


def _hours_from(first: int) -> list[int]:
    """The 24 local hours in the order BRAIN's day passes through them (it starts at 00:00
    US Eastern, which is mid-morning in India), so the chart reads left to right."""
    return [(first + offset) % 24 for offset in range(24)]


@router.get("")
async def quota_today(state: State) -> QuotaToday:
    """Today's budget: planned vs spent per lane, hourly spend, and the safety margin."""
    governor = _governor(state)
    day = await governor.snapshot()
    hourly = await governor.hourly(day)
    return _today(day, hourly, governor)


@router.post("/estimate")
async def estimate(body: QuotaEstimateRequest, state: State) -> QuotaEstimate:
    """Would a plan of ``simulations`` fit in ``lane`` today? What would be trimmed?"""
    governor = _governor(state)
    if body.lane not in governor.config().lanes:
        raise refuse(422, "UNKNOWN_LANE", f"There is no lane called {body.lane!r}.")
    admission = await governor.admit(body.lane, [body.cost_each] * body.simulations)
    per_hour = governor.config().max_per_hour
    return QuotaEstimate(
        lane=admission.lane,
        asked=admission.asked,
        admitted=admission.admitted,
        trimmed=admission.trimmed,
        admitted_cost=admission.admitted_cost,
        reason=admission.reason,
        lane_remaining=admission.lane_remaining,
        global_remaining=admission.global_remaining,
        hours_to_send=round(admission.admitted_cost / per_hour, 2) if per_hour else 0.0,
    )
