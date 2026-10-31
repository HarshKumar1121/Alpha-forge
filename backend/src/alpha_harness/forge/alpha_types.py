"""Alpha types: every BRAIN alpha type (or competition) is a YAML file, not code.

When BRAIN adds a new type or changes a rule, you edit or add one file in
``config/forge/alpha_types``. ``extends:`` lets a type state only what differs from its parent;
files starting with ``_`` are templates and are skipped.

Three things live here, all free to run:

* :func:`lint`  — would this expression be refused, or wasted, before it costs a simulation?
* :func:`gate`  — does a simulated alpha clear this type's local pre-filters? BRAIN's own
  checks are always final; these only save you from queueing obvious failures.
* :func:`score_tier` — competition points (ARC-style tiers), when a type defines them.

Ported from alpha-copilot ``profiles.py``.
"""

import copy
import re
from dataclasses import dataclass, field
from functools import cache
from typing import TYPE_CHECKING, Any

from . import config
from .expr import ExprInfo, ParseError, analyse

if TYPE_CHECKING:
    from pathlib import Path


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)  # pyright: ignore[reportUnknownArgumentType]
        else:
            out[k] = copy.deepcopy(v)
    return out


@dataclass(frozen=True, slots=True)
class AlphaType:
    name: str
    raw: dict[str, Any]

    @property
    def description(self) -> str:
        return str(self.raw.get("description", ""))

    @property
    def sim_type(self) -> str:
        return str(self.raw["simulation"]["type"])

    @property
    def settings(self) -> dict[str, Any]:
        """BRAIN simulation settings, camelCase as the platform spells them."""
        return dict(self.raw["simulation"]["settings"])

    @property
    def lint_rules(self) -> dict[str, Any]:
        return self.raw.get("lint") or {}

    @property
    def gates(self) -> dict[str, Any]:
        return self.raw.get("gates") or {}

    @property
    def pipeline(self) -> list[str]:
        return list(self.raw.get("pipeline") or [])

    @property
    def robustness(self) -> dict[str, Any]:
        return self.raw.get("robustness") or {}

    @property
    def correlation(self) -> dict[str, Any]:
        return self.raw.get("correlation") or {}

    @property
    def budget(self) -> int:
        """How many variants one hypothesis may try: more trials means a higher bar (DSR)."""
        return int((self.raw.get("budget") or {}).get("max_variants_per_hypothesis", 40))

    def without_test_gates(self) -> AlphaType:
        """The same type judged on train only — what selection may look at (CLAUDE.md rule 6)."""
        gates = {k: v for k, v in self.gates.items() if not k.startswith("test.")}
        return AlphaType(self.name, {**self.raw, "gates": gates})


@cache
def load_all(directory: Path | None = None) -> dict[str, AlphaType]:
    """Every alpha type under ``directory`` (default ``config/forge/alpha_types``), with
    ``extends:`` resolved. Cached: treat the result as read-only."""
    folder = directory or config.config_dir() / "alpha_types"
    raws: dict[str, dict[str, Any]] = {}
    for path in sorted(folder.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        data = config.read_yaml(path)
        raws[str(data.get("name", path.stem))] = data

    resolved: dict[str, dict[str, Any]] = {}

    def resolve(name: str, chain: tuple[str, ...] = ()) -> dict[str, Any]:
        if name in resolved:
            return resolved[name]
        if name in chain:
            raise ValueError(f"Alpha type inheritance loop: {' -> '.join((*chain, name))}")
        if name not in raws:
            raise ValueError(f"{chain[-1]} extends unknown alpha type {name!r}")
        data = raws[name]
        parent = data.get("extends")
        merged = _merge(resolve(str(parent), (*chain, name)), data) if parent else dict(data)
        merged.pop("extends", None)
        resolved[name] = merged
        return merged

    return {n: AlphaType(n, resolve(n)) for n in raws}


def load(name: str, directory: Path | None = None) -> AlphaType:
    types = load_all(directory)
    if name not in types:
        raise KeyError(f"No alpha type {name!r}. Available: {', '.join(sorted(types))}")
    return types[name]


# --- offline lint ---------------------------------------------------------------------------


@dataclass(slots=True)
class LintResult:
    expression: str
    info: ExprInfo | None
    errors: list[str] = field(default_factory=list[str])
    warnings: list[str] = field(default_factory=list[str])

    @property
    def ok(self) -> bool:
        return not self.errors


def lint(
    expression: str,
    alpha_type: AlphaType,
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
    operators: set[str] | None = None,
    target_dataset: str | None = None,
) -> LintResult:
    """Check an Alpha against a type without spending a simulation.

    ``catalog`` maps field id -> {dataset, type, coverage}; ``operators`` is the set of
    operator names your account has. Both are optional but catch most wasted simulations.
    """
    try:
        info = analyse(expression)
    except ParseError as exc:
        return LintResult(expression, None, [f"Syntax: {exc}"])
    res = LintResult(expression, info)
    rules = alpha_type.lint_rules
    name = alpha_type.name

    if (mx := rules.get("max_operators")) is not None and info.operator_count > mx:
        res.errors.append(f"{info.operator_count} operators > {mx} allowed for {name}")
    if (mx := rules.get("max_fields")) is not None and len(info.fields) > mx:
        res.errors.append(f"{len(info.fields)} data fields > {mx} allowed for {name}")
    if (mx := rules.get("max_depth")) and info.depth > mx:
        res.warnings.append(f"Nesting depth {info.depth} > {mx}: likely overfit; simplify.")
    if allowed := rules.get("allowed_lookbacks"):
        odd = [lb for lb in info.lookbacks if lb not in allowed]
        if odd:
            res.warnings.append(
                f"Unusual lookbacks {odd}: prefer economic windows (week/month/quarter/year). "
                "Odd numbers like 37 are a classic sign of parameter mining."
            )
    if operators is not None:
        unknown = [o for o in info.operators if o not in operators]
        if unknown:
            res.errors.append(f"Operators not on your account: {unknown}")

    if catalog is not None:
        _lint_fields(res, expression, info, rules, catalog, target_dataset)
    return res


def _lint_fields(
    res: LintResult,
    expression: str,
    info: ExprInfo,
    rules: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    target_dataset: str | None,
) -> None:
    missing = [f for f in info.fields if f not in catalog]
    if missing:
        res.errors.append(f"Unknown fields (typo or wrong region/universe?): {missing}")
    known = [f for f in info.fields if f in catalog]
    datasets = {catalog[f].get("dataset") for f in known}
    if rules.get("single_dataset") and len(datasets) > 1:
        res.errors.append(f"Single-dataset type but fields span {sorted(map(str, datasets))}")
    if target_dataset and rules.get("min_fields_from_target_dataset"):
        need = int(rules["min_fields_from_target_dataset"])
        have = sum(1 for f in known if catalog[f].get("dataset") == target_dataset)
        if have < need:
            res.errors.append(f"Needs >= {need} field(s) from dataset {target_dataset}")
    for f in known:
        meta = catalog[f]
        if meta.get("type") == "VECTOR" and not _wrapped_in_vec(expression, f):
            res.errors.append(f"{f} is a VECTOR field: wrap it in a vec_ operator first")
        cov = meta.get("coverage")
        if cov is not None and cov < 0.5 and f"ts_backfill({f}" not in expression:
            res.warnings.append(f"{f} coverage {cov:.0%}: consider ts_backfill({f}, 63)")


def _wrapped_in_vec(expression: str, field_id: str) -> bool:
    return re.search(rf"vec_\w+\(\s*{re.escape(field_id)}\b", expression) is not None


# --- gates on simulation results --------------------------------------------------------------


def metric(alpha: dict[str, Any], dotted: str) -> float | None:
    """Read ``is.sharpe`` / ``train.fitness`` / ``test.sharpe`` from a BRAIN alpha body."""
    block, _, key = dotted.partition(".")
    value = (alpha.get(block) or {}).get(key)
    return None if value is None else float(value)


@dataclass(frozen=True, slots=True)
class GateResult:
    passed: bool
    reasons: list[str]
    values: dict[str, float | None]


def gate(alpha: dict[str, Any], alpha_type: AlphaType) -> GateResult:
    reasons: list[str] = []
    values: dict[str, float | None] = {}
    for key, rule in alpha_type.gates.items():
        if key == "checks_must_pass":
            checks = {
                c.get("name"): c.get("result") for c in (alpha.get("is") or {}).get("checks", [])
            }
            reasons += [f"BRAIN check {n} FAILED" for n in rule or [] if checks.get(n) == "FAIL"]
            continue
        if not isinstance(rule, dict):
            continue
        v = metric(alpha, key)
        values[key] = v
        lo, hi = rule.get("min"), rule.get("max")  # pyright: ignore[reportUnknownMemberType]
        if v is None:
            # Missing train/test just means no test period was set; don't fail on it.
            if (lo is not None or hi is not None) and not key.startswith(("train.", "test.")):
                reasons.append(f"{key} missing")
            continue
        if lo is not None and v < lo:
            reasons.append(f"{key}={v:.3f} < {lo}")
        if hi is not None and v > hi:
            reasons.append(f"{key}={v:.3f} > {hi}")
    return GateResult(not reasons, reasons, values)


def score_tier(alpha: dict[str, Any], alpha_type: AlphaType) -> int | None:
    tiers: dict[str, list[list[float]]] | None = alpha_type.raw.get("score_tiers")
    if not tiers:
        return None
    total = 0
    for key, steps in tiers.items():
        v = metric(alpha, key)
        if v is not None:
            total += int(max((pts for thr, pts in steps if v >= thr), default=0))
    return total
