"""``/api/forge/probe``: learn how a real field behaves from three cheap probe alphas, through
the Quota Governor's ``explore`` lane — the Practice-mode counterpart to the Gym's free local
profiling.

BRAIN never hands out raw field values (see ``forge/simulator.py``'s module docstring, point 3),
so a real field's behaviour has to come from probe alphas, not local statistics. Three
simulations only, tagged ``forge.explore`` so the Quota Governor admits and paces them like any
other spend (CLAUDE.md rule 3); never submits (rule 1). See ``forge/research/profiler.py`` for
what the three probes are and how the archetype is read off them.
"""

import math
from typing import Any

from fastapi import APIRouter
from pydantic import Field

from ..forge.pipelines.brain_adapter import BrainSimulator
from ..forge.research.profiler import probes, profile_from_probes
from ..forge.simulator import SimOutcome, request_for
from ..schemas import Out
from .catalog import Scope
from .deps import State, refuse

router = APIRouter(prefix="/api/forge/probe", tags=["forge-probe"])

#: Same lane the Quota Governor reserves for exactly this: "cheap probes that tell you how a
#: field or dataset behaves before you build on it" (config/forge/lanes.yaml).
LANE = "explore"


class ProbeRequest(Out):
    field_id: str = Field(min_length=1, max_length=200)


class ProbeAngle(Out):
    name: str
    expression: str
    alpha_id: str | None
    train_sharpe: float | None
    turnover: float | None
    error: str | None


class ProbeResult(Out):
    field_id: str
    archetype: str
    best_angle: str
    #: +1: use the best angle as is; -1: flip its sign.
    sign: int
    level_turnover: float | None
    notes: list[str]
    angles: list[ProbeAngle]
    lane: str
    #: How many of the 3 probes the Quota Governor admitted; fewer still yields a (less
    #: confident) profile, since ``profile_from_probes`` tolerates a missing probe.
    admitted: int
    asked: int


def _num(v: Any) -> float | None:
    """``None``/``nan`` mean "not enough data"; on the wire that is ``null``, not a fake number."""
    if v is None:
        return None
    f = float(v)
    return None if math.isnan(f) else f


def _block(alpha: dict[str, Any] | None, block: str, key: str) -> float | None:
    return _num(((alpha or {}).get(block) or {}).get(key))


@router.post("/field")
async def probe_field(scope: Scope, body: ProbeRequest, state: State) -> ProbeResult:
    """Run the 3 cheap behaviour probes on a real field and read off its archetype.

    Cheap, not free: unlike the Gym, this reaches BRAIN, so it spends 1-3 simulations from the
    ``explore`` lane before you spend real quota building templates on this field.
    """
    governor = state.engine.governor
    if governor is None:
        raise refuse(503, "quota_governor_off", "The Quota Governor is not running.")

    exprs = probes(body.field_id)
    names = list(exprs)  # dict order: level, change, history
    admission = await governor.admit(LANE, [1] * len(names))
    names = names[: admission.admitted]
    if not names:
        raise refuse(
            429,
            "no_quota",
            f"The {LANE} lane has no room today: {admission.reason}",
        )

    settings = {
        "instrumentType": scope.instrument_type,
        "region": scope.region,
        "delay": scope.delay,
        "universe": scope.universe,
    }
    simulator = BrainSimulator(state.engine, state.db, state.endpoints, LANE)
    requests = [request_for(exprs[n], settings) for n in names]
    outcomes: list[SimOutcome] = await simulator.simulate(requests)

    results = {n: (o.alpha if o.ok else None) for n, o in zip(names, outcomes, strict=True)}
    profile = profile_from_probes(results)

    angles = [
        ProbeAngle(
            name=n,
            expression=exprs[n],
            alpha_id=o.alpha_id,
            train_sharpe=_block(o.alpha, "train", "sharpe"),
            turnover=_block(o.alpha, "is", "turnover"),
            error=o.error,
        )
        for n, o in zip(names, outcomes, strict=True)
    ]
    return ProbeResult(
        field_id=body.field_id,
        archetype=profile["archetype"],
        best_angle=profile["best_angle"],
        sign=profile["sign"],
        level_turnover=_num(profile["level_turnover"]),
        notes=profile["notes"],
        angles=angles,
        lane=LANE,
        admitted=admission.admitted,
        asked=admission.asked,
    )
