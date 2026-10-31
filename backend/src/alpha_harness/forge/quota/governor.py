"""The Quota Governor: how much each lane may still spend today, and what may be sent now.

The rules, in the order they bind:

1. **The day's budget** is BRAIN's daily limit minus the safety margin. The last
   ``safety_margin`` simulations are never planned into — they are for retries, surprises and
   work done by hand on the website.
2. **Trust the lower remaining.** The local ledger and BRAIN's own ``X-RateLimit-Remaining``
   header can disagree (sims run on the website, a lost response). Whichever says less is
   left is believed, because overspending is the error that cannot be undone.
3. **Fixed shares, released linearly.** Each lane is promised ``share x budget``. A lane that
   is not using its share keeps protecting it in proportion to how much of BRAIN's day is left;
   the rest flows to busy lanes. At 00:00 Eastern every share is protected, by 23:59 nothing
   idle is. That way a lane that starts work in the afternoon still finds most of its
   share, yet a quiet day's quota is not wasted.
4. **Pacing.** At most ``max_per_hour`` sent in any rolling hour, nothing in quiet hours.

Two entry points use those rules differently:

- :meth:`QuotaGovernor.admit` is strict: a plan that does not fit is trimmed before it enters
  the engine. ``/api/forge/quota/estimate`` and the Phase 6 pipelines use it.
- :meth:`QuotaGovernor.pace` is the engine's dispatch gate. Work it does not let through is
  *held* in the queue, never rejected: it goes out when its lane has room or after the reset.

Cache hits cost nothing here: they never reach the dispatch gate and the ledger records them
with cost 0.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from ...brain.platform import DAILY_SIMULATIONS
from ...db.models import QuotaSnapshot, utcnow
from . import ledger
from .config import LanesConfig, load
from .models import LaneState

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from ...db.sqlite import Database
    from ...engine.packer import WorkItem


@dataclass(frozen=True, slots=True)
class BrainReading:
    """BRAIN's own ``X-RateLimit-*`` figures, from a simulation POST made today."""

    limit: int
    remaining: int


@dataclass(frozen=True, slots=True)
class LaneDay:
    lane: str
    about: str
    share: float
    #: ``share x budget``: what the lane was promised this morning.
    planned: float
    #: What it may use now, its share plus quota other lanes have released to it.
    effective_cap: float
    spent: int
    queued: int
    cached: int

    @property
    def room(self) -> float:
        """What the lane may still SEND today, ignoring its own queue."""
        return max(0.0, self.effective_cap - self.spent)

    @property
    def remaining(self) -> float:
        """What the lane may still PLAN today: room after its queue is sent."""
        return max(0.0, self.effective_cap - self.spent - self.queued)


@dataclass(frozen=True, slots=True)
class DayState:
    """Everything the governor knows about BRAIN's day at one moment."""

    day: str
    now: datetime
    limit: int
    safety_margin: int
    #: ``limit - safety_margin``: the most the governor will ever let through today.
    budget: int
    #: Charged today, after trusting the lower remaining (see module docstring rule 2).
    spent: int
    #: Spent per BRAIN beyond what the ledger knows of (e.g. sims run on the website).
    unrecorded: int
    queued: int
    cached: int
    #: Fraction of BRAIN's day already gone, 0..1.
    day_elapsed: float
    resets_in_seconds: float
    #: Whether BRAIN's own figure was available and used.
    exact: bool
    brain_remaining: int | None
    last_hour: int
    quiet_now: bool
    lanes: dict[str, LaneDay] = field(default_factory=dict[str, LaneDay])

    @property
    def remaining(self) -> int:
        """Left to PLAN today within the budget: spent and queued work both count."""
        return max(0, self.budget - self.spent - self.queued)

    @property
    def room(self) -> int:
        """Left to SEND today within the budget."""
        return max(0, self.budget - self.spent)

    @property
    def margin_warning(self) -> bool:
        """True once the day is into, or about to be into, the safety margin."""
        if self.brain_remaining is not None and self.brain_remaining <= self.safety_margin:
            return True
        return self.limit - self.spent - self.queued <= self.safety_margin


@dataclass(frozen=True, slots=True)
class Admission:
    """The answer to "may this plan run today?"."""

    lane: str
    asked: int
    admitted: int
    trimmed: int
    admitted_cost: int
    reason: str
    lane_remaining: int
    global_remaining: int


def protected(planned: float, spent: int, queued: int, day_elapsed: float) -> float:
    """How much of the day a lane keeps for itself (rule 3 of the module docstring).

    - What it already spent is gone for everyone.
    - Work it has queued keeps its claim, but only up to its own share.
    - The unused rest of its share is released linearly as BRAIN's day passes.
    """
    return max(float(spent), min(planned, float(spent + queued)), planned * (1.0 - day_elapsed))


def effective_caps(
    budget: int,
    planned: dict[str, float],
    spent: dict[str, int],
    queued: dict[str, int],
    day_elapsed: float,
) -> dict[str, float]:
    """What each lane may use: the budget minus what every OTHER lane protects.

    Never more than the budget in total: a lane's spend is counted in every other lane's
    deduction, so all of them together can never send past ``budget`` (and :meth:`pace` checks
    the global room anyway).
    """
    keeps = {
        lane: protected(planned[lane], spent.get(lane, 0), queued.get(lane, 0), day_elapsed)
        for lane in planned
    }
    total = sum(keeps.values())
    return {lane: max(0.0, budget - (total - keeps[lane])) for lane in planned}


def fit_prefix(costs: Sequence[int], room: float) -> tuple[int, int]:
    """How many of ``costs``, taken in order, fit in ``room``; and what they cost.

    In order, because a plan is sorted best-first: what is trimmed is the tail, the items with
    the lowest predicted value.
    """
    used = 0
    for count, cost in enumerate(costs):
        if used + cost > room:
            return count, used
        used += cost
    return len(costs), used


class QuotaGovernor:
    """Reads the ledger, BRAIN's last quota reading and the engine's queue; decides.

    ``clock`` and ``config`` are injectable so tests can stand at any moment of any day with
    any rules, without waiting or touching ``config/forge``.
    """

    def __init__(
        self,
        db: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
        config: Callable[[], LanesConfig] = load,
    ) -> None:
        self.db = db
        self.clock = clock
        self.config = config

    async def state(self) -> DayState:
        """BRAIN's day as it stands now."""
        config = self.config()
        now = self.clock()
        day = ledger.platform_day(now)
        start, end = ledger.platform_day_bounds(now)
        day_elapsed = min(1.0, max(0.0, (now - start) / (end - start)))

        async with self.db.session() as session:
            spent_by_lane = await ledger.spent_by_lane(session, day)
            cached_by_lane = await ledger.cached_by_lane(session, day)
            queued_by_lane = await ledger.waiting_by_lane(session)
            last_hour = await ledger.spent_since(session, now - timedelta(hours=1))
            reading = await self._brain_reading(session, start)

        limit = min(config.daily_limit, DAILY_SIMULATIONS if reading is None else reading.limit)
        budget = max(0, limit - config.safety_margin)

        recorded = sum(spent_by_lane.values())
        # Rule 2: BRAIN's count of what is used wins whenever it is the larger. What the
        # ledger did not see was not sent by any Forge lane, so it is charged to the lane for
        # work done by hand.
        unrecorded = 0
        if reading is not None:
            unrecorded = max(0, (reading.limit - reading.remaining) - recorded)
        if unrecorded:
            spent_by_lane = dict(spent_by_lane)
            lane = config.default_lane
            spent_by_lane[lane] = spent_by_lane.get(lane, 0) + unrecorded

        planned = {name: lane.share * budget for name, lane in config.lanes.items()}
        caps = effective_caps(budget, planned, spent_by_lane, queued_by_lane, day_elapsed)
        lanes = {
            name: LaneDay(
                lane=name,
                about=lane.about,
                share=lane.share,
                planned=planned[name],
                effective_cap=caps[name],
                spent=spent_by_lane.get(name, 0),
                queued=queued_by_lane.get(name, 0),
                cached=cached_by_lane.get(name, 0),
            )
            for name, lane in config.lanes.items()
        }
        local = now.astimezone(config.timezone).time()
        return DayState(
            day=day,
            now=now,
            limit=limit,
            safety_margin=config.safety_margin,
            budget=budget,
            spent=recorded + unrecorded,
            unrecorded=unrecorded,
            queued=sum(queued_by_lane.values()),
            cached=sum(cached_by_lane.values()),
            day_elapsed=day_elapsed,
            resets_in_seconds=max(0.0, (end - now).total_seconds()),
            exact=reading is not None,
            brain_remaining=None if reading is None else reading.remaining,
            last_hour=last_hour,
            quiet_now=config.is_quiet(local),
            lanes=lanes,
        )

    @staticmethod
    async def _brain_reading(session: AsyncSession, day_start: datetime) -> BrainReading | None:
        """BRAIN's latest quota header, if one arrived today. Yesterday's says nothing now."""
        snapshot = await session.scalar(
            select(QuotaSnapshot).order_by(QuotaSnapshot.observed_at.desc()).limit(1)
        )
        if snapshot is None or snapshot.remaining is None or snapshot.observed_at < day_start:
            return None
        return BrainReading(
            limit=snapshot.limit_total or DAILY_SIMULATIONS, remaining=snapshot.remaining
        )

    async def snapshot(self) -> DayState:
        """The day for the Today screen; also writes the day's ``lane_state`` the first time."""
        state = await self.state()
        await self._note_allocation(state)
        return state

    async def hourly(self, state: DayState) -> dict[str, int]:
        """The day's spend per hour on the user's clock, for the Today chart."""
        async with self.db.session() as session:
            return await ledger.hourly(session, state.day, self.config().timezone)

    async def _note_allocation(self, state: DayState) -> None:
        rows = [
            {
                "day": state.day,
                "lane": lane.lane,
                "share": lane.share,
                "cap": math.floor(lane.planned),
                "allocation": self.config().allocation,
                "created_at": state.now,
            }
            for lane in state.lanes.values()
        ]
        async with self.db.session() as session:
            await session.execute(insert(LaneState).values(rows).on_conflict_do_nothing())

    async def admit(self, lane: str, costs: Sequence[int]) -> Admission:
        """Strict admission for a plan: the in-order prefix of ``costs`` that fits today.

        A plan fits when it is inside its lane's remaining quota AND the day's remaining
        budget, both after work already queued. Read-only: nothing is reserved; the caller
        enqueues what was admitted.
        """
        config = self.config()
        if lane not in config.lanes:
            raise ValueError(f"Unknown lane {lane!r}")
        state = await self.state()
        lane_left = state.lanes[lane].remaining
        room = min(lane_left, state.remaining)
        admitted, cost = fit_prefix(costs, room)
        trimmed = len(costs) - admitted
        if not trimmed:
            reason = "Fits within today's quota."
        elif lane_left < state.remaining:
            reason = f"Trimmed to the {math.floor(lane_left)} left in lane {lane!r} today."
        else:
            reason = f"Trimmed to the {state.remaining} left in today's budget."
        return Admission(
            lane=lane,
            asked=len(costs),
            admitted=admitted,
            trimmed=trimmed,
            admitted_cost=cost,
            reason=reason,
            lane_remaining=math.floor(lane_left),
            global_remaining=state.remaining,
        )

    async def pace(self, items: Iterable[WorkItem]) -> list[WorkItem]:
        """The engine's dispatch gate: which queued items may be SENT right now.

        Takes items in queue order and lets each through while it fits the rolling hour, the
        day's room and its lane's room. An item that does not fit stays queued (held, not
        rejected); a later, cheaper one from another lane may still go.
        """
        items = list(items)
        if not items:
            return []
        config = self.config()
        state = await self.state()
        if state.quiet_now:
            return []
        hour_room = config.max_per_hour - state.last_hour
        day_room = state.room
        lane_room: dict[str, float] = defaultdict(float)
        lane_room.update({name: lane.room for name, lane in state.lanes.items()})

        allowed: list[WorkItem] = []
        for item in items:
            cost = ledger.cost_of(str(item.payload.get("type") or ""))
            lane = config.lane_of(item.task)
            if cost > min(hour_room, day_room, lane_room[lane]):
                continue
            allowed.append(item)
            hour_room -= cost
            day_room -= cost
            lane_room[lane] -= cost
        return allowed
