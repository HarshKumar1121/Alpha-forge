"""Templates: an economic idea with typed slots, rendered into expressions.

A template lives in ``config/forge/templates/*.yaml``::

    - id: analyst_revision_group
      category: analyst/revision
      angle: change                 # which probe angle this template expresses (see below)
      slots:
        X: {kind: field, archetype: [slow, medium]}
        W: {kind: window, values: [21, 63]}
        G: {kind: group, values: [industry, subindustry]}
      expr: "group_rank(ts_delta($X, $W), $G)"

Three slot kinds:

* ``field``  — a data field. Optional filters: ``archetype`` (the field's behaviour, from the
  profiler), ``type`` (MATRIX or VECTOR; absent = either), ``same_dataset_as`` /
  ``different_dataset_from`` (another field slot), and ``aggregate`` for VECTOR fields
  (default ``vec_avg``). A field is always cleaned before it is used: ``ts_backfill(X, 63)``,
  or ``ts_backfill(vec_avg(X), 63)`` for a VECTOR — templates never wrap the slot themselves.
* ``window`` — a lookback, from economic values only (week 5, month 21, quarter 63, year 252).
* ``group``  — a peer set: industry, subindustry, sector, market.

Why the right template depends on how the field BEHAVES (the profiler's archetypes):

    slow        updates rarely (quarterly fundamentals, monthly surveys)  -> levels, changes, events
    medium      drifts over weeks                                         -> changes, surprises
    persistent  daily but sticky ranks                                    -> own-history, surprises
    fast        noisy day to day (sentiment, flows, prices)               -> smoothing, short means

``angle`` ties a template to one of the three probes (level, change, history): the planner
signs a template by the Sharpe of its matching probe, so the data — not a guess — says
whether "more" is good or bad for this field.

Which alpha types a template fits is computed (:func:`fits`), never trusted from a label:
``complexity: simple`` is the author's intent, the linter is the judge.

Ported from alpha-copilot ``research/templates.py`` and upgraded from its single ``$f`` slot to
the typed-slot format.
"""

import itertools
import re
from dataclasses import dataclass, replace
from functools import cache
from typing import TYPE_CHECKING, Any

import structlog
import yaml

from .. import config
from ..alpha_types import lint, load_all
from ..expr import ParseError, analyse, on_result, render_template

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence
    from pathlib import Path

    from ..alpha_types import AlphaType

log = structlog.get_logger(__name__)

ANGLES = ("level", "change", "history")
ARCHETYPES = ("slow", "medium", "persistent", "fast")
COMPLEXITIES = ("simple", "medium", "complex")
FIELD_TYPES = ("MATRIX", "VECTOR")
#: How far a sparse field is carried forward before it counts as missing. A quarter: long
#: enough to bridge a quarterly fundamental's gap between reports, short enough that a
#: delisted or stale value does not linger for a year.
BACKFILL_DAYS = 63

#: The schema of one YAML entry. Everything else is a typo the loader should catch.
REQUIRED_KEYS = ("id", "name", "category", "complexity", "slots", "expr", "rationale")
OPTIONAL_KEYS = ("angle", "only_if")
SLOT_KEYS = {
    "field": {
        "kind",
        "archetype",
        "type",
        "aggregate",
        "same_dataset_as",
        "different_dataset_from",
        "hint",
    },
    "window": {"kind", "values"},
    "group": {"kind", "values"},
}
_PLACEHOLDER = re.compile(r"\$([A-Za-z_]\w*)")
#: A template cleaning its own slot would clean it twice: the engine already does.
_SELF_WRAP = re.compile(r"\b(ts_backfill|group_backfill|vec_\w+)\(\s*\$([A-Za-z_]\w*)")


class ConstraintError(ValueError):
    """A requested fill breaks one of the template's slot rules."""


type Param = int | str


@dataclass(frozen=True, slots=True)
class FieldSlot:
    archetype: tuple[str, ...] = ()
    type: str | None = None  # MATRIX | VECTOR | None (either)
    aggregate: str = "vec_avg"
    same_dataset_as: str | None = None
    different_dataset_from: str | None = None
    hint: str = ""


@dataclass(frozen=True, slots=True)
class WindowSlot:
    values: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class GroupSlot:
    values: tuple[str, ...]


type Slot = FieldSlot | WindowSlot | GroupSlot


@dataclass(frozen=True, slots=True)
class FieldInfo:
    """What filling a field slot needs to know about a candidate field."""

    id: str
    dataset: str = ""
    archetype: str | None = None
    type: str = "MATRIX"


@dataclass(frozen=True, slots=True)
class Template:
    id: str
    name: str
    category: str
    complexity: str
    slots: Mapping[str, Slot]
    expr: str
    rationale: str
    angle: str = "level"
    only_if: str | None = None
    #: Fields the expression names directly (``returns``, ``adv20``…), beyond its slots.
    needs: frozenset[str] = frozenset()

    @property
    def field_slots(self) -> list[str]:
        return [k for k, s in self.slots.items() if isinstance(s, FieldSlot)]

    @property
    def single_field(self) -> str | None:
        """The slot name when the template takes exactly one field — what the planner,
        which researches one field at a time, can use."""
        names = self.field_slots
        return names[0] if len(names) == 1 else None

    @property
    def params(self) -> dict[str, tuple[Param, ...]]:
        """Window and group slots: everything but the fields."""
        return {k: s.values for k, s in self.slots.items() if isinstance(s, WindowSlot | GroupSlot)}

    def default_params(self) -> dict[str, Param]:
        """A neutral first guess, not the best one: the middle window, and the first group
        (listed first because it is the author's preferred peer set)."""
        out: dict[str, Param] = {}
        for k, s in self.slots.items():
            if isinstance(s, WindowSlot):
                out[k] = s.values[len(s.values) // 2]
            elif isinstance(s, GroupSlot):
                out[k] = s.values[0]
        return out

    def all_params(self) -> list[dict[str, Param]]:
        keys = list(self.params)
        combos = itertools.product(*self.params.values())
        return [dict(zip(keys, c, strict=True)) for c in combos] or [{}]

    def field_slot(self, name: str) -> FieldSlot:
        slot = self.slots[name]
        if not isinstance(slot, FieldSlot):
            raise TypeError(f"template {self.id!r}: slot {name} is not a field slot")
        return slot

    def neighbours(self, params: Mapping[str, Param]) -> list[dict[str, Param]]:
        """Adjacent windows only: robustness of the idea, not a hunt for the best number."""
        out: list[dict[str, Param]] = []
        for k, s in self.slots.items():
            if not isinstance(s, WindowSlot) or k not in params:
                continue
            i = s.values.index(int(params[k]))
            out += [{**params, k: s.values[j]} for j in (i - 1, i + 1) if 0 <= j < len(s.values)]
        return out


# --- loading -----------------------------------------------------------------------------------


def _slot(template_id: str, name: str, raw: dict[str, Any]) -> Slot:
    kind = raw.get("kind")
    where = f"template {template_id!r} slot {name}"
    if kind == "field":
        archetype = tuple(str(a) for a in raw.get("archetype") or ())
        if unknown := set(archetype) - set(ARCHETYPES):
            raise ValueError(f"{where}: unknown archetype {sorted(unknown)}")
        return FieldSlot(
            archetype=archetype,
            type=raw.get("type"),
            aggregate=str(raw.get("aggregate", "vec_avg")),
            same_dataset_as=raw.get("same_dataset_as"),
            different_dataset_from=raw.get("different_dataset_from"),
            hint=str(raw.get("hint", "")),
        )
    if kind not in {"window", "group"}:
        raise ValueError(f"{where}: unknown kind {kind!r}")
    values: tuple[Any, ...] = tuple(raw.get("values") or ())
    if not values:
        raise ValueError(f"{where}: a {kind} slot needs values")
    if kind == "window":
        return WindowSlot(tuple(int(v) for v in values))
    return GroupSlot(tuple(str(v) for v in values))


def parse_template(raw: dict[str, Any]) -> Template:
    """One YAML entry -> a validated Template. Errors name the template and the slot."""
    tid = str(raw["id"])
    slots = {str(k): _slot(tid, str(k), v) for k, v in (raw.get("slots") or {}).items()}
    for k, s in slots.items():
        if isinstance(s, FieldSlot):
            for ref in (s.same_dataset_as, s.different_dataset_from):
                if ref is not None and not isinstance(slots.get(ref), FieldSlot):
                    raise ValueError(f"template {tid!r} slot {k}: {ref} is not a field slot")
    angle = str(raw.get("angle", "level"))
    if angle not in ANGLES:
        raise ValueError(f"template {tid!r}: angle must be one of {ANGLES}")
    t = Template(
        id=tid,
        name=str(raw.get("name", tid)),
        category=str(raw.get("category", "")),
        complexity=str(raw.get("complexity", "")),
        slots=slots,
        expr=str(raw["expr"]),
        rationale=" ".join(str(raw.get("rationale", "")).split()),
        angle=angle,
        only_if=raw.get("only_if"),
    )
    # Fill every field slot with a stand-in name: what's left over is what the template needs.
    stand_ins = {k: f"slot_{k}" for k in t.field_slots}
    try:
        fields = analyse(render(t, stand_ins, t.default_params())).fields
    except ParseError as exc:
        raise ValueError(f"template {tid!r} does not render to a valid expression: {exc}") from exc
    return replace(t, needs=frozenset(fields) - set(stand_ins.values()))


def validate(raw: object) -> list[str]:
    """Schema problems in one YAML entry, each naming the key. Empty means well-formed.

    :func:`parse_template` checks what it needs to build a Template; this checks the whole
    library contract, so a typo (``same_dataset``, ``archtype``) fails loudly instead of
    silently loosening a slot.
    """
    if not isinstance(raw, dict):
        return ["entry is not a mapping"]
    entry: dict[str, Any] = {str(k): v for k, v in raw.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    out = [f"{k}: required" for k in REQUIRED_KEYS if entry.get(k) in (None, "")]
    out += [f"{k}: unknown key" for k in entry if k not in (*REQUIRED_KEYS, *OPTIONAL_KEYS)]
    if (c := entry.get("complexity")) is not None and c not in COMPLEXITIES:
        out.append(f"complexity: {c!r} is not one of {', '.join(COMPLEXITIES)}")
    if (a := entry.get("angle")) is not None and a not in ANGLES:
        out.append(f"angle: {a!r} is not one of {', '.join(ANGLES)}")
    if (o := entry.get("only_if")) is not None and not isinstance(o, str):
        out.append("only_if: must be a profiler flag name, e.g. size_linked")
    slots = entry.get("slots")
    if slots is not None and not isinstance(slots, dict):
        return [*out, "slots: must be a mapping of slot name -> slot"]
    slots = {str(k): v for k, v in (slots or {}).items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    fields = {k for k, s in slots.items() if isinstance(s, dict) and s.get("kind") == "field"}  # pyright: ignore[reportUnknownMemberType]
    for name, s in slots.items():
        out += [f"slots.{name}: {p}" for p in _validate_slot(s, fields - {name})]
    expr = str(entry.get("expr") or "")
    used = set(_PLACEHOLDER.findall(expr))
    out += [f"expr: ${k} is not a declared slot" for k in sorted(used - set(slots))]
    out += [f"slots.{k}: declared but never used in expr" for k in sorted(set(slots) - used)]
    out += [
        f"expr: {op}(${k}…) wraps a slot; the engine already cleans every field "
        "(ts_backfill, vec_ aggregate), so templates never wrap slots themselves"
        for op, k in _SELF_WRAP.findall(expr)
    ]
    return out


def _validate_slot(raw: object, other_fields: set[str]) -> list[str]:
    if not isinstance(raw, dict):
        return ["must be a mapping, e.g. {kind: field}"]
    s: dict[str, Any] = {str(k): v for k, v in raw.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    kind = s.get("kind")
    if kind not in SLOT_KEYS:
        return [f"kind {kind!r} is not one of field, window, group"]
    out = [f"{k}: unknown key for a {kind} slot" for k in s if k not in SLOT_KEYS[kind]]
    if kind == "field":
        arch = s.get("archetype") or []
        if not isinstance(arch, list) or set(map(str, arch)) - set(ARCHETYPES):  # pyright: ignore[reportUnknownArgumentType]
            out.append(f"archetype: must be a list drawn from {', '.join(ARCHETYPES)}")
        if (t := s.get("type")) is not None and t not in FIELD_TYPES:
            out.append(f"type: {t!r} is not MATRIX or VECTOR")
        if (agg := s.get("aggregate")) is not None and not str(agg).startswith("vec_"):
            out.append(f"aggregate: {agg!r} is not a vec_ operator")
        out += [
            f"{key}: {s[key]!r} is not another field slot"
            for key in ("same_dataset_as", "different_dataset_from")
            if s.get(key) is not None and s[key] not in other_fields
        ]
        return out
    values = s.get("values")
    if not isinstance(values, list) or not values:
        return [*out, f"values: a {kind} slot needs a non-empty list"]
    if kind == "window" and not all(type(v) is int and v > 0 for v in values):  # pyright: ignore[reportUnknownVariableType]
        out.append("values: window values must be positive integers (days)")
    if kind == "group" and not all(isinstance(v, str) for v in values):  # pyright: ignore[reportUnknownVariableType]
        out.append("values: group values must be names, e.g. industry")
    return out


@dataclass(frozen=True, slots=True)
class LoadError:
    file: str
    template_id: str | None
    message: str

    def __str__(self) -> str:
        where = f"{self.file}: {self.template_id}" if self.template_id else self.file
        return f"{where}: {self.message}"


@dataclass(frozen=True, slots=True)
class LibraryLoad:
    templates: tuple[Template, ...]
    errors: tuple[LoadError, ...] = ()


@cache
def load_library(directory: Path | None = None) -> LibraryLoad:
    """Every template under ``directory`` (default ``config/forge/templates``), in file then
    listed order, plus a clear error for each entry that could not be loaded.

    One bad entry never takes the library down: its siblings, and the other files, still load.
    """
    folder = directory or config.config_dir() / "templates"
    out: list[Template] = []
    errors: list[LoadError] = []
    seen: dict[str, str] = {}
    for path in sorted(folder.glob("*.yaml")):
        try:
            entries = config.read_yaml(path).get("templates")
        except (yaml.YAMLError, ValueError) as exc:
            errors.append(LoadError(path.name, None, f"not valid YAML: {exc}"))
            continue
        if not isinstance(entries, list):
            errors.append(LoadError(path.name, None, "needs a top-level `templates:` list"))
            continue
        for i, raw in enumerate(entries, 1):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            tid = str(raw.get("id")) if isinstance(raw, dict) and raw.get("id") else f"entry #{i}"  # pyright: ignore[reportUnknownMemberType]
            if problems := validate(raw):
                errors += [LoadError(path.name, tid, p) for p in problems]
                continue
            try:
                t = parse_template(raw)  # pyright: ignore[reportUnknownArgumentType]
            except ValueError as exc:
                errors.append(LoadError(path.name, tid, str(exc)))
                continue
            if t.id in seen:
                errors.append(LoadError(path.name, tid, f"duplicate id (first in {seen[t.id]})"))
                continue
            seen[t.id] = path.name
            out.append(t)
    for e in errors:
        log.warning("forge.template_load_error", error=str(e))
    return LibraryLoad(tuple(out), tuple(errors))


def library() -> tuple[Template, ...]:
    """Every template that loaded cleanly. See :func:`load_library` for the ones that did not."""
    return load_library().templates


def get(template_id: str) -> Template | None:
    return next((t for t in library() if t.id == template_id), None)


# --- rendering ---------------------------------------------------------------------------------


def clean(field: str, *, vector: bool = False, aggregate: str = "vec_avg") -> str:
    """The cleaned form every template sees: carry sparse values forward, and reduce a VECTOR
    field (many values per stock per day) to one number first."""
    inner = f"{aggregate}({field})" if vector else field
    return f"ts_backfill({inner}, {BACKFILL_DAYS})"


def render(
    t: Template,
    fields: Mapping[str, str],
    params: Mapping[str, Param],
    *,
    sign: int = 1,
    vector: Collection[str] = (),
) -> str:
    """Fill a template. ``fields`` maps field slot -> field id; ``vector`` names the field ids
    that are VECTOR fields; a negative ``sign`` flips the whole signal."""
    values: dict[str, str] = {k: str(v) for k, v in params.items()}
    for name, field in fields.items():
        slot = t.field_slot(name)
        is_vector = slot.type == "VECTOR" or field in vector
        values[name] = clean(field, vector=is_vector, aggregate=slot.aggregate)
    e = render_template(t.expr, values)
    return e if sign > 0 else flip(e)


def flip(expression: str) -> str:
    """Negate an expression's result (only the last statement of a multi-statement one)."""
    return on_result(expression, lambda r: f"-({r})")


def render_single(
    t: Template, field: str, params: Mapping[str, Param], *, sign: int = 1, vector: bool = False
) -> str:
    """Render a single-field template on ``field``."""
    slot = t.single_field
    if slot is None:
        raise ValueError(f"template {t.id!r} does not take exactly one field")
    return render(t, {slot: field}, params, sign=sign, vector={field} if vector else ())


# --- choosing ----------------------------------------------------------------------------------


def match(
    profile: Mapping[str, Any],
    *,
    available: set[str] | None = None,
    alpha_type: AlphaType | None = None,
    field: str = "x",
    vector: bool | None = None,
    operators: set[str] | None = None,
) -> list[Template]:
    """Single-field templates suited to this field's behaviour that would pass the alpha
    type's lint.

    ``vector`` (when known) drops templates whose slot wants the other field type;
    ``operators`` (when known) drops templates the simulator could not run anyway.
    """
    arch = profile.get("archetype", "medium")
    out: list[Template] = []
    for t in library():
        slot_name = t.single_field
        if slot_name is None:
            continue
        slot = t.field_slot(slot_name)
        if slot.archetype and arch not in slot.archetype:
            continue
        if vector is not None and slot.type is not None and (slot.type == "VECTOR") != vector:
            continue
        if t.only_if and not profile.get(t.only_if):
            continue
        if available is not None and not t.needs <= available:
            continue
        if alpha_type is not None or operators is not None:
            # Worst case for the operator count: with a sign flip.
            sample = render_single(t, field, t.default_params(), sign=-1, vector=bool(vector))
            if alpha_type is not None and not lint(sample, alpha_type).ok:
                continue
            if operators is not None and not set(analyse(sample).operators) <= operators:
                continue
        out.append(t)
    return out


def field_problem(slot: FieldSlot, f: FieldInfo) -> str | None:
    """Why ``f`` cannot fill ``slot`` on its own (archetype or type), or None if it can."""
    if slot.archetype and f.archetype not in slot.archetype:
        return (
            f"field {f.id} behaves {f.archetype or 'unknown'}; "
            f"this slot takes {', '.join(slot.archetype)} fields"
        )
    if slot.type is not None and slot.type != f.type:
        return f"field {f.id} is {f.type}; this slot needs a {slot.type} field"
    return None


def _fits(slot: FieldSlot, f: FieldInfo) -> bool:
    return field_problem(slot, f) is None


def bindings(t: Template, fields: Sequence[FieldInfo]) -> list[dict[str, FieldInfo]]:
    """Every way to fill the template's field slots from ``fields`` — distinct fields, each
    fitting its slot's archetype and type, with the dataset constraints honoured."""
    names = t.field_slots
    slots = {n: t.field_slot(n) for n in names}
    out: list[dict[str, FieldInfo]] = []
    for combo in itertools.permutations(fields, len(names)):
        b = dict(zip(names, combo, strict=True))
        ok = True
        for n, s in slots.items():
            f = b[n]
            same = s.same_dataset_as and b[s.same_dataset_as].dataset != f.dataset
            diff = s.different_dataset_from and b[s.different_dataset_from].dataset == f.dataset
            if not _fits(s, f) or same or diff:
                ok = False
                break
        if ok:
            out.append(b)
    return out


type Choice = Param | Sequence[Param]


def expand(
    t: Template, fields: Sequence[FieldInfo], choices: Mapping[str, Choice] | None = None
) -> list[str]:
    """Every expression the template yields over ``fields`` and its window/group values.

    ``choices`` narrows the expansion: a field slot pinned to a field id from ``fields``, a
    window or group slot pinned to one value or a subset of its allowed values. A choice that
    breaks a slot rule raises :class:`ConstraintError` saying which rule — never a silent
    empty list, because "no candidates" and "you asked for something illegal" are different
    answers.
    """
    pinned, params = _apply_choices(t, fields, choices or {})
    vector = {f.id for f in fields if f.type == "VECTOR"}
    keys = list(params)
    combos = [dict(zip(keys, c, strict=True)) for c in itertools.product(*params.values())]
    return [
        render(t, {k: f.id for k, f in b.items()}, p, vector=vector)
        for b in bindings(t, fields)
        if all(b[n].id == f.id for n, f in pinned.items())
        for p in combos or [{}]
    ]


def _apply_choices(
    t: Template, fields: Sequence[FieldInfo], choices: Mapping[str, Choice]
) -> tuple[dict[str, FieldInfo], dict[str, tuple[Param, ...]]]:
    where = f"template {t.id!r}"
    by_id = {f.id: f for f in fields}
    pinned: dict[str, FieldInfo] = {}
    params = dict(t.params)
    for name, choice in choices.items():
        slot = t.slots.get(name)
        if slot is None:
            raise ConstraintError(f"{where}: no slot named {name}")
        if isinstance(slot, FieldSlot):
            if not isinstance(choice, str) or choice not in by_id:
                raise ConstraintError(f"{where} slot {name}: {choice!r} is not in the field set")
            f = by_id[choice]
            if problem := field_problem(slot, f):
                raise ConstraintError(f"{where} slot {name}: {problem}")
            pinned[name] = f
            continue
        wanted: tuple[Param, ...] = (
            (choice,) if isinstance(choice, int | str) else tuple(choice)  # pyright: ignore[reportUnnecessaryIsInstance]
        )
        if bad := [v for v in wanted if v not in slot.values]:
            allowed = ", ".join(map(str, slot.values))
            raise ConstraintError(f"{where} slot {name}: {bad} not among the allowed {allowed}")
        params[name] = wanted
    for name, f in pinned.items():
        slot = t.field_slot(name)
        same, diff = slot.same_dataset_as, slot.different_dataset_from
        if same in pinned and pinned[same].dataset != f.dataset:
            raise ConstraintError(
                f"{where} slot {name}: {f.id} ({f.dataset}) must share a dataset with "
                f"slot {same}'s {pinned[same].id} ({pinned[same].dataset})"
            )
        if diff in pinned and pinned[diff].dataset == f.dataset:
            raise ConstraintError(
                f"{where} slot {name}: {f.id} must come from a different dataset than "
                f"slot {diff}'s {pinned[diff].id} (both {f.dataset})"
            )
    if len({f.id for f in pinned.values()}) < len(pinned):
        raise ConstraintError(f"{where}: one field cannot fill two slots")
    return pinned, params


# --- alpha-type fit ----------------------------------------------------------------------------

#: The dataset BRAIN files price/volume fields under; the hard-coded fields live there.
PV_DATASET = "pv1"


@dataclass(frozen=True, slots=True)
class Fit:
    """Whether a template, filled as favourably as its slot rules allow, passes a type's lint.

    ``flipped_ok`` repeats the check with the signal negated: the planner flips a template
    whose probe says "less is better", and the extra ``-`` is one more operator.
    """

    ok: bool
    flipped_ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def placeholder_fill(t: Template) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Fill each field slot with a stand-in (``x_field``…) and a catalog that describes it.

    The fill is the most favourable one the rules allow: every field on the price/volume
    dataset (where the hard-coded ``returns``/``adv20`` live), unless ``different_dataset_from``
    forces a second dataset. "Can this template fit power_pool?" is a question about its best
    legal fill; a real field set can only do worse.
    """
    names = t.field_slots
    datasets = dict.fromkeys(names, PV_DATASET)
    for _ in names:  # constraints can chain (Z same as Y, Y different from X): settle them
        for n in names:
            s = t.field_slot(n)
            if s.same_dataset_as:
                datasets[n] = datasets[s.same_dataset_as]
            elif s.different_dataset_from:
                other = datasets[s.different_dataset_from]
                datasets[n] = "other1" if other == PV_DATASET else PV_DATASET
    fields = {n: f"{n.lower()}_field" for n in names}
    catalog: dict[str, dict[str, Any]] = {
        f: {"dataset": PV_DATASET, "type": "MATRIX"} for f in t.needs
    }
    for n, f in fields.items():
        catalog[f] = {"dataset": datasets[n], "type": t.field_slot(n).type or "MATRIX"}
    return fields, catalog


def example(t: Template, *, sign: int = 1) -> str:
    """The template rendered on its placeholders at its default params."""
    fields, _ = placeholder_fill(t)
    return render(t, fields, t.default_params(), sign=sign)


def fits(t: Template, alpha_types: Mapping[str, AlphaType] | None = None) -> dict[str, Fit]:
    """Which alpha types this template can satisfy, computed by linting a placeholder fill
    against every alpha type YAML — never read from a label."""
    fields, catalog = placeholder_fill(t)
    target = catalog[fields[t.field_slots[0]]]["dataset"] if fields else PV_DATASET
    out: dict[str, Fit] = {}
    for name, at in (alpha_types or load_all()).items():
        plain = lint(example(t), at, catalog=catalog, target_dataset=target)
        flipped = lint(example(t, sign=-1), at, catalog=catalog, target_dataset=target)
        out[name] = Fit(plain.ok, flipped.ok, tuple(plain.errors), tuple(plain.warnings))
    return out
