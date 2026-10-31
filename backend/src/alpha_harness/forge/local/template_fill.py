"""Fill a template with Gym fields, so it can be tried for free before it costs a simulation.

A template's field slots carry rules (archetype, dataset). Gym fields are described the same
way a BRAIN field would be: the dataset comes from ``config/forge/gym_fields.yaml`` and the
archetype is measured by the profiler from the data — not hand-labelled, so "this slot wants a
slow field" is checked against how the field actually behaves.
"""

import fnmatch
from dataclasses import dataclass
from functools import cache

from .. import config
from ..expr import analyse
from ..research.profiler import profile_local
from ..research.templates import FieldInfo, Template, bindings, expand
from .engine import LOCAL_OPERATORS
from .gym import make_market


@dataclass(frozen=True, slots=True)
class GymExample:
    expression: str
    #: Field slot -> the Gym field that fills it.
    fields: dict[str, str]


@dataclass(frozen=True, slots=True)
class Unavailable:
    reason: str


@cache
def gym_fields(seed: int = 7) -> tuple[FieldInfo, ...]:
    """Every Gym MATRIX field as a template candidate, in ``gym_fields.yaml`` preference order.

    Profiling the whole market takes a few seconds, so it happens once per process.
    """
    market = make_market(seed)
    groups: dict[str, list[str]] = (config.read_yaml(config.config_dir() / "gym_fields.yaml")).get(
        "datasets"
    ) or {}
    out: list[FieldInfo] = []
    for dataset, patterns in groups.items():
        for pattern in patterns:
            for name in fnmatch.filter(market.fields, pattern):
                arch = profile_local(market.fields[name], market.fields["cap"])["archetype"]
                out.append(FieldInfo(name, dataset, str(arch), "MATRIX"))
    return tuple(out)


def gym_example(t: Template) -> GymExample | Unavailable:
    """The template rendered on the most suitable Gym fields at its default params, or why it
    cannot run on the Gym (an operator or field the Gym does not have)."""
    if any(t.field_slot(n).type == "VECTOR" for n in t.field_slots):
        return Unavailable("needs a VECTOR field; the Gym has none")
    names = {f.id for f in gym_fields()}
    if missing := sorted(t.needs - names):
        return Unavailable(f"uses {', '.join(missing)}, which the Gym does not have")
    # A slot filled with a field the expression already uses (rank(adv20) * rank(adv20)) is no
    # test of the idea, so those fields are left out of the candidates.
    candidates = [f for f in gym_fields() if f.id not in t.needs]
    # bindings() follows candidate order, so the first one uses the most preferred fields.
    found = bindings(t, candidates)
    if not found:
        return Unavailable("no Gym field fits this template's slot rules")
    chosen = {k: f.id for k, f in found[0].items()}
    (expression,) = expand(t, candidates, {**t.default_params(), **chosen})
    if unknown := sorted(set(analyse(expression).operators) - LOCAL_OPERATORS):
        return Unavailable(f"uses {', '.join(unknown)}, which the Gym engine does not implement")
    return GymExample(expression, chosen)
