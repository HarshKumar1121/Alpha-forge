"""``lanes.yaml``, read into a typed, validated shape.

A lane is a purpose a simulation is spent on (exploring a dataset, validating a candidate, your
own manual work). Splitting the day into lanes is how a 5,000-a-day budget stays balanced: a
greedy pipeline cannot eat the quota that validation of its own best ideas needs later.

The numbers are platform rules and personal choices, so they live in YAML (CLAUDE.md rule 7);
this module only checks that they add up and answers "which lane pays for this task?".
"""

from dataclasses import dataclass
from datetime import time
from functools import cache
from typing import Any
from zoneinfo import ZoneInfo

from .. import config as forge_config

#: The prefix Forge pipelines put on an engine task so its lane can be read off the name.
FORGE_TASK_PREFIX = "forge."


@dataclass(frozen=True, slots=True)
class Lane:
    name: str
    about: str
    #: Fraction of the day's budget this lane is promised.
    share: float
    #: Where the Phase 9 bandit may move ``share`` to. Unused while allocation is fixed.
    bounds: tuple[float, float]
    alpha_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QuietHours:
    """A local-time window with no sending. ``start > end`` wraps midnight (22:00-06:00)."""

    start: time
    end: time

    def contains(self, moment: time) -> bool:
        if self.start <= self.end:
            return self.start <= moment < self.end
        return moment >= self.start or moment < self.end


@dataclass(frozen=True, slots=True)
class LanesConfig:
    daily_limit: int
    safety_margin: int
    #: The user's clock, for quiet hours and the hourly chart. The DAY is BRAIN's, not this.
    timezone: ZoneInfo
    max_per_hour: int
    quiet_hours: tuple[QuietHours, ...]
    allocation: str
    default_lane: str
    task_lanes: dict[str, str]
    lanes: dict[str, Lane]

    def lane_of(self, task: str) -> str:
        """The lane that pays for an engine task.

        ``forge.<lane>`` names its lane directly; an explicit ``task_lanes`` entry comes next;
        everything else — every existing lab screen and manual run — is ``default_lane``.
        """
        if task.startswith(FORGE_TASK_PREFIX):
            lane = task.removeprefix(FORGE_TASK_PREFIX).split(".", 1)[0]
            if lane in self.lanes:
                return lane
        return self.task_lanes.get(task, self.default_lane)

    def is_quiet(self, local: time) -> bool:
        return any(window.contains(local) for window in self.quiet_hours)


def _clock(text: str) -> time:
    hours, minutes = text.strip().split(":")
    return time(int(hours), int(minutes))


def _quiet(entry: str) -> QuietHours:
    start, end = entry.split("-")
    return QuietHours(_clock(start), _clock(end))


def parse(raw: dict[str, Any]) -> LanesConfig:
    """Validate ``lanes.yaml``. Raises ``ValueError`` naming the first thing that is wrong."""
    allocation = str(raw.get("allocation", "fixed"))
    if allocation != "fixed":
        # Thompson-sampling shares need yield history that only exists from Phase 9 on.
        raise ValueError(f"lanes.yaml allocation {allocation!r} is not supported yet; use fixed")

    lanes: dict[str, Lane] = {}
    for name, body in (raw.get("lanes") or {}).items():
        low, high = body.get("bounds", [0.0, 1.0])
        lanes[str(name)] = Lane(
            name=str(name),
            about=str(body.get("about", "")),
            share=float(body["share"]),
            bounds=(float(low), float(high)),
            alpha_types=tuple(body.get("alpha_types") or ()),
        )
    if not lanes:
        raise ValueError("lanes.yaml defines no lanes")
    total = sum(lane.share for lane in lanes.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"lanes.yaml shares sum to {total:.4f}, not 1")

    default_lane = str(raw.get("default_lane", "reserve"))
    task_lanes = {str(k): str(v) for k, v in (raw.get("task_lanes") or {}).items()}
    for lane in (default_lane, *task_lanes.values()):
        if lane not in lanes:
            raise ValueError(f"lanes.yaml names lane {lane!r}, which is not defined")

    daily_limit = int(raw["daily_limit"])
    safety_margin = int(raw.get("safety_margin", 0))
    if not 0 <= safety_margin < daily_limit:
        raise ValueError("lanes.yaml safety_margin must be between 0 and daily_limit")

    pacing = raw.get("pacing") or {}
    return LanesConfig(
        daily_limit=daily_limit,
        safety_margin=safety_margin,
        timezone=ZoneInfo(str(raw.get("timezone", "UTC"))),
        max_per_hour=int(pacing.get("max_per_hour", daily_limit)),
        quiet_hours=tuple(_quiet(str(q)) for q in pacing.get("quiet_hours") or ()),
        allocation=allocation,
        default_lane=default_lane,
        task_lanes=task_lanes,
        lanes=lanes,
    )


@cache
def load() -> LanesConfig:
    """The parsed config, once per process. :func:`alpha_harness.forge.config.reload` clears it."""
    return parse(forge_config.lanes())
