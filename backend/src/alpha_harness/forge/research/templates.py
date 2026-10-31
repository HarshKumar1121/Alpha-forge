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

Ported from alpha-copilot ``research/templates.py`` and upgraded from its single ``$f`` slot to
the typed-slot format.
"""

import itertools
from dataclasses import dataclass, replace
from functools import cache
from typing import TYPE_CHECKING, Any

from .. import config
from ..alpha_types import lint
from ..expr import ParseError, analyse, on_result, render_template

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from ..alpha_types import AlphaType

ANGLES = ("level", "change", "history")
ARCHETYPES = ("slow", "medium", "persistent", "fast")
#: How far a sparse field is carried forward before it counts as missing.
BACKFILL_DAYS = 63

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


@cache
def library() -> tuple[Template, ...]:
    """Every template under ``config/forge/templates``, in file then listed order."""
    out: list[Template] = []
    seen: set[str] = set()
    for path in sorted((config.config_dir() / "templates").glob("*.yaml")):
        for raw in config.read_yaml(path).get("templates") or []:
            t = parse_template(raw)
            if t.id in seen:
                raise ValueError(f"duplicate template id {t.id!r} in {path.name}")
            seen.add(t.id)
            out.append(t)
    return tuple(out)


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


def _fits(slot: FieldSlot, f: FieldInfo) -> bool:
    if slot.archetype and f.archetype not in slot.archetype:
        return False
    return slot.type is None or slot.type == f.type


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


def expand(t: Template, fields: Sequence[FieldInfo]) -> list[str]:
    """Every expression the template yields over ``fields`` and its window/group values."""
    vector = {f.id for f in fields if f.type == "VECTOR"}
    return [
        render(t, {k: f.id for k, f in b.items()}, p, vector=vector)
        for b in bindings(t, fields)
        for p in t.all_params()
    ]
