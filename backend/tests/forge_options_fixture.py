"""A hand-written ``OPTIONS /simulations`` response in BRAIN's 4.0 shape.

Small, but it has every feature the scope code must handle: a common tree, two simulation types
whose trees carry region/delay/universe/neutralization, choices that depend on a parent field
(delay on region, universe and neutralization on region), a region with only one delay (GLB),
regional universes (MINVOL1M only in GLB), and region ``ALL`` offered only by the
region-agnostic type. Values and labels follow what BRAIN sends: delays are ints, labels text.
"""

from typing import Any


def _c(*values: Any) -> list[dict[str, Any]]:
    return [{"value": v, "label": str(v)} for v in values]


def _by_region(table: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {"instrumentType": {"EQUITY": {"region": table}}}


REGULAR_TREE: dict[str, Any] = {
    "region": {
        "type": "choice",
        "label": "Region",
        "required": True,
        "choices": {"instrumentType": {"EQUITY": _c("USA", "GLB", "EUR")}},
    },
    "delay": {
        "type": "choice",
        "label": "Delay",
        "required": True,
        "choices": _by_region({"USA": _c(0, 1), "GLB": _c(1), "EUR": _c(0, 1)}),
    },
    "universe": {
        "type": "choice",
        "label": "Universe",
        "required": True,
        "choices": _by_region(
            {
                "USA": _c("TOP3000", "TOP1000", "ILLIQUID_MINVOL1M"),
                "GLB": _c("TOP3000", "MINVOL1M", "TOPDIV3000"),
                "EUR": _c("TOP2500"),
            }
        ),
    },
    "neutralization": {
        "type": "choice",
        "label": "Neutralization",
        "required": True,
        "choices": _by_region(
            {
                "USA": [
                    {"value": "SUBINDUSTRY", "label": "Subindustry"},
                    {"value": "INDUSTRY", "label": "Industry"},
                    {"value": "STATISTICAL", "label": "Statistical"},
                ],
                "GLB": [
                    {"value": "COUNTRY", "label": "Country"},
                    {"value": "INDUSTRY", "label": "Industry"},
                ],
                "EUR": [{"value": "COUNTRY", "label": "Country"}],
            }
        ),
    },
}

REGION_AGNOSTIC_TREE: dict[str, Any] = {
    "region": {
        "type": "choice",
        "label": "Region",
        "required": True,
        "choices": {"instrumentType": {"EQUITY": _c("ALL")}},
    },
    "delay": {
        "type": "choice",
        "label": "Delay",
        "required": True,
        "choices": _by_region({"ALL": _c(1)}),
    },
    "universe": {
        "type": "choice",
        "label": "Universe",
        "required": True,
        "choices": _by_region({"ALL": _c("TOPDIV3000")}),
    },
    "neutralization": {
        "type": "choice",
        "label": "Neutralization",
        "required": True,
        "choices": _by_region({"ALL": [{"value": "MARKET", "label": "Market"}]}),
    },
}

OPTIONS_SIMULATIONS: dict[str, Any] = {
    "actions": {
        "POST": {
            "type": {
                "type": "choice",
                "label": "Type",
                "choices": [
                    {
                        "value": "REGULAR",
                        "label": "Regular",
                        "settings": {"children": REGULAR_TREE},
                    },
                    {
                        "value": "REGION_AGNOSTIC",
                        "label": "Region Agnostic",
                        "settings": {"children": REGION_AGNOSTIC_TREE},
                    },
                ],
            },
            "settings": {
                "children": {
                    "instrumentType": {
                        "type": "choice",
                        "label": "Instrument type",
                        "required": True,
                        "choices": [{"value": "EQUITY", "label": "Equity"}],
                    },
                    "decay": {"type": "integer", "label": "Decay", "minValue": 0, "maxValue": 512},
                }
            },
        }
    }
}
