"""Scopes: the (instrumentType, region, delay, universe) a piece of research runs in.

A scope decides everything downstream: which fields exist, what they cover, which pyramid an
alpha pays into and which alphas BRAIN compares it with. So a scope is only ever built from
the account's own ``OPTIONS /simulations`` schema (resolved by
:mod:`alpha_harness.brain.settings_schema`) — never from a hardcoded list, which would offer
markets the account cannot run and spend a simulation finding that out.
"""

from typing import Any

from pydantic import BaseModel

from ...brain.platform import INSTRUMENT_TYPE, REGION_AGNOSTIC_REGION
from ...brain.settings_schema import resolve_choices, validate_settings
from ...catalog.queries import Tuple4

#: The four settings that make a scope, outermost first: each one's choices depend on the
#: ones before it.
SCOPE_KEYS = ("instrumentType", "region", "delay", "universe")


class Scope(BaseModel):
    instrument_type: str = INSTRUMENT_TYPE
    region: str
    delay: int
    universe: str

    def settings(self) -> dict[str, Any]:
        """The scope as BRAIN's settings keys."""
        return {
            "instrumentType": self.instrument_type,
            "region": self.region,
            "delay": self.delay,
            "universe": self.universe,
        }

    def to_tuple4(self) -> Tuple4:
        return Tuple4(
            instrument_type=self.instrument_type,
            region=self.region,
            delay=self.delay,
            universe=self.universe,
        )


def choices(schema: dict[str, Any], field: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
    """``[{value, label}]`` BRAIN offers for ``field`` given the settings chosen so far."""
    node = schema.get(field)
    if not isinstance(node, dict):
        return []
    resolved = resolve_choices(node.get("choices"), settings) or []
    return [
        {"value": c.get("value"), "label": str(c.get("label") or c.get("value"))}
        for c in resolved
        if c.get("value") is not None
    ]


def scope_tree(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Every scope the account can run, as a tree a picker can walk.

    ``instrumentType -> region -> delay -> universe``, each node ``{value, label, children}``;
    every universe carries the neutralizations BRAIN offers there. Region ``ALL`` is flagged
    ``regionAgnostic``: it only appears when the region-agnostic tree offers it.
    """
    tree: list[dict[str, Any]] = []
    for it in choices(schema, "instrumentType", {}):
        s1 = {"instrumentType": it["value"]}
        regions: list[dict[str, Any]] = []
        for region in choices(schema, "region", s1):
            s2 = {**s1, "region": region["value"]}
            delays: list[dict[str, Any]] = []
            for delay in choices(schema, "delay", s2):
                s3 = {**s2, "delay": delay["value"]}
                universes = [
                    {
                        **universe,
                        "neutralizations": choices(
                            schema, "neutralization", {**s3, "universe": universe["value"]}
                        ),
                    }
                    for universe in choices(schema, "universe", s3)
                ]
                delays.append({**delay, "value": int(delay["value"]), "universes": universes})
            regions.append(
                {
                    **region,
                    "regionAgnostic": region["value"] == REGION_AGNOSTIC_REGION,
                    "delays": delays,
                }
            )
        tree.append({**it, "regions": regions})
    return tree


def check_scope(schema: dict[str, Any], scope: Scope) -> list[str]:
    """Why BRAIN would refuse this scope, in plain sentences; empty when it can run.

    Each key is checked against its parent's choices, so ``GLB / D0`` reads "Delay 0 is not
    available for region='GLB'" rather than a generic failure.
    """
    settings = scope.settings()
    problems = validate_settings(
        {k: v for k, v in schema.items() if k in SCOPE_KEYS}, settings, require_all=False
    )
    if problems:
        return problems
    # Every value was legal for its parent, but a key BRAIN lists may still offer nothing here.
    for key in SCOPE_KEYS:
        if key in schema and not choices(schema, key, settings):
            return [f"BRAIN offers no {key} choices for this scope."]
    return []
