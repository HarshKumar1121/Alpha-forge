"""``config/forge/pipelines/*.yaml``, read into a typed, validated shape.

One file per alpha type: which lane pays for it, which template complexities it may draw from,
and how its correlation and BRAIN-check stages are scoped. The numbers and the self/power-pool
choice are platform rules and personal choices, so they live in YAML (CLAUDE.md rule 7); this
module only validates and parses.
"""

from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any

from .. import config as forge_config

if TYPE_CHECKING:
    from pathlib import Path

CORRELATION_KINDS = ("self", "power-pool")


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    name: str
    alpha_type: str
    lane: str
    template_complexity: tuple[str, ...]
    correlation_kind: str
    correlation_top_n: int
    check_top_n: int
    default_budget: int


def _parse(name: str, raw: dict[str, Any]) -> PipelineConfig:
    kind = str((raw.get("correlation") or {}).get("kind", "self"))
    if kind not in CORRELATION_KINDS:
        raise ValueError(f"pipeline {name!r}: correlation.kind must be one of {CORRELATION_KINDS}")
    complexity = tuple(str(c) for c in raw.get("template_complexity") or ())
    if not complexity:
        raise ValueError(f"pipeline {name!r}: template_complexity must list at least one")
    return PipelineConfig(
        name=name,
        alpha_type=str(raw.get("alpha_type", name)),
        lane=str(raw["lane"]),
        template_complexity=complexity,
        correlation_kind=kind,
        correlation_top_n=int((raw.get("correlation") or {}).get("top_n", 5)),
        check_top_n=int((raw.get("check") or {}).get("top_n", 10)),
        default_budget=int((raw.get("budget") or {}).get("default", 40)),
    )


@cache
def load_all(directory: Path | None = None) -> dict[str, PipelineConfig]:
    """Every pipeline under ``directory`` (default ``config/forge/pipelines``). Files starting
    with ``_`` are templates and are skipped. Cached: treat the result as read-only."""
    folder = directory or forge_config.config_dir() / "pipelines"
    out: dict[str, PipelineConfig] = {}
    for path in sorted(folder.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        data = forge_config.read_yaml(path)
        name = str(data.get("name", path.stem))
        out[name] = _parse(name, data)
    return out


def load(name: str, directory: Path | None = None) -> PipelineConfig:
    pipelines = load_all(directory)
    if name not in pipelines:
        raise KeyError(f"No pipeline {name!r}. Available: {', '.join(sorted(pipelines))}")
    return pipelines[name]
