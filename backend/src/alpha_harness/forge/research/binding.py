"""Binding a template to a field set: real fields, real scope, real alphas.

The Template Library shows what a template *could* be. This module makes it concrete: take a
field set (its fields and the market they were picked in), fill the template's slots with
those fields, and render every alpha that follows. Nothing here simulates — the whole point is
to see the alphas, and what BRAIN would make of them, before spending any of the day's quota.

Three things the expansion has to respect that a placeholder fill does not:

* **The field's type.** A VECTOR field needs an aggregate (``vec_avg``) before an operator can
  read it; the engine's :func:`~.templates.clean` does that, but a slot asking for MATRIX
  still cannot take a VECTOR field.
* **The field's archetype, when it is known.** A field set of real BRAIN fields is not
  profiled, so its behaviour is unknown and an archetype slot cannot rule it out — see
  :func:`~.templates.field_problem`. Once the Real-Price Lab profiles a field, its archetype
  is a fact and the rule applies again.
* **What the scope actually carries.** ``subindustry`` is not a group in every region, and a
  template naming ``adv20`` cannot run where the catalog has no ``adv20``. Both are checked
  against the fields the catalog holds for the scope, so an impossible alpha is never offered.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...brain.platform import GROUPING_FIELDS
from ..expr import analyse
from .templates import (
    ConstraintError,
    FieldInfo,
    Filled,
    GroupSlot,
    Template,
    WindowSlot,
    expand_filled,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: The most alphas one binding renders. A template with three windows, two groups and six
#: fields runs into the thousands; past a screenful the useful answer is "narrow it down".
MAX_RENDERED = 200

#: ``market`` groups every stock together, so it needs no field of its own in the catalog.
ALWAYS_AVAILABLE_GROUPS = frozenset({"market"})


@dataclass(frozen=True, slots=True)
class ScopeData:
    """What one market holds, as the binder needs it."""

    #: Field id -> {dataset, type, coverage, description}. Every field the scope carries that
    #: the binding may name: the field set's own, the template's ``needs``, grouping fields.
    catalog: Mapping[str, Mapping[str, Any]]
    #: Grouping fields the scope carries, e.g. ``industry``, ``country``.
    groups: frozenset[str]


@dataclass(frozen=True, slots=True)
class Rendered:
    """One alpha the template rendered, with the choices that produced it."""

    expression: str
    #: Slot name -> field id.
    fields: dict[str, str]
    #: Slot name -> window or group value.
    params: dict[str, Any]
    operator_count: int
    #: Data fields it names, grouping fields excluded (BRAIN counts these).
    data_fields: tuple[str, ...]
    datasets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Binding:
    """Everything a preview needs: the alphas, and why some combinations were not offered."""

    rendered: tuple[Rendered, ...] = ()
    #: How many the expansion produced before the cap.
    total: int = 0
    #: Why this template cannot be bound at all (missing fields, no legal group…).
    blockers: tuple[str, ...] = ()
    #: Worth saying, but the binding still produced alphas.
    notes: tuple[str, ...] = ()
    #: Slot name -> the values actually offered, after the scope narrowed them.
    offered: dict[str, list[Any]] = field(default_factory=dict[str, list[Any]])


def field_infos(
    rows: Sequence[Mapping[str, Any]], profiles: Mapping[str, str] | None = None
) -> list[FieldInfo]:
    """Catalog rows as template candidates, carrying an archetype only where one is known."""
    known = profiles or {}
    return [
        FieldInfo(
            id=str(r["field_id"]),
            dataset=str(r.get("dataset_id") or ""),
            archetype=known.get(str(r["field_id"])),
            type=str(r.get("field_type") or "MATRIX"),
        )
        for r in rows
    ]


def scope_catalog(
    rows: Sequence[Mapping[str, Any]], extra: Sequence[Mapping[str, Any]] = ()
) -> dict[str, dict[str, Any]]:
    """The lint's view of a market: field id -> dataset, type, coverage, description."""
    out: dict[str, dict[str, Any]] = {}
    for r in (*rows, *extra):
        out[str(r["field_id"])] = {
            "dataset": str(r.get("dataset_id") or ""),
            "type": str(r.get("field_type") or "MATRIX"),
            "coverage": r.get("coverage"),
            "description": r.get("description"),
        }
    return out


def available_groups(catalog: Mapping[str, Any]) -> frozenset[str]:
    """The grouping fields this scope carries. ``market`` is always one."""
    return frozenset({g for g in GROUPING_FIELDS if g in catalog} | set(ALWAYS_AVAILABLE_GROUPS))


def narrow_to_scope(t: Template, scope: ScopeData) -> tuple[dict[str, list[Any]], list[str]]:
    """The window and group values this scope can actually run, and what was dropped.

    Windows are days and run anywhere; groups are fields, and a region without ``subindustry``
    cannot run a template that insists on it.
    """
    offered: dict[str, list[Any]] = {}
    notes: list[str] = []
    for name, slot in t.slots.items():
        if isinstance(slot, WindowSlot):
            offered[name] = list(slot.values)
        elif isinstance(slot, GroupSlot):
            kept = [g for g in slot.values if g in scope.groups]
            dropped = [g for g in slot.values if g not in scope.groups]
            offered[name] = kept
            if dropped:
                notes.append(
                    f"slot ${name}: this market has no {', '.join(dropped)}, "
                    f"so {'only ' + ', '.join(kept) if kept else 'nothing'} is offered"
                )
    return offered, notes


def bind(
    t: Template,
    fields: Sequence[FieldInfo],
    scope: ScopeData,
    *,
    choices: Mapping[str, Any] | None = None,
    limit: int = MAX_RENDERED,
) -> Binding:
    """Render every alpha this template yields on this field set, in this market."""
    blockers: list[str] = []
    notes: list[str] = []

    if missing := sorted(t.needs - set(scope.catalog)):
        blockers.append(
            f"this market has no {', '.join(missing)}, which the template's expression names"
        )

    offered, narrowing = narrow_to_scope(t, scope)
    notes += narrowing
    for name, values in offered.items():
        if not values:
            blockers.append(f"slot ${name} has no value this market can run")

    if not fields and t.field_slots:
        blockers.append("the field set holds no fields in this market")

    if blockers:
        return Binding(blockers=tuple(blockers), notes=tuple(notes), offered=offered)

    # The scope's narrowing is itself a choice; anything the caller pinned narrows it further.
    wanted: dict[str, Any] = {k: v for k, v in offered.items() if v}
    wanted.update(choices or {})
    try:
        filled = expand_filled(t, fields, wanted, require_archetype=False)
    except ConstraintError as exc:
        return Binding(blockers=(str(exc),), notes=tuple(notes), offered=offered)

    if not filled:
        blockers.append(
            "no combination of these fields fits the template's slot rules "
            "(field type, or the same/different dataset rules)"
        )
        return Binding(blockers=tuple(blockers), notes=tuple(notes), offered=offered)

    rendered = [_describe(f, scope.catalog) for f in filled[:limit]]
    if len(filled) > limit:
        notes.append(
            f"{len(filled)} alphas render; showing the first {limit}. "
            "Pin a field, a window or a group to narrow them."
        )
    return Binding(
        rendered=tuple(rendered),
        total=len(filled),
        notes=tuple(notes),
        offered=offered,
    )


def _describe(f: Filled, catalog: Mapping[str, Mapping[str, Any]]) -> Rendered:
    info = analyse(f.expression)
    datasets = sorted(
        {
            str(catalog[name]["dataset"])
            for name in info.fields
            if name in catalog and catalog[name].get("dataset")
        }
    )
    return Rendered(
        expression=f.expression,
        fields=dict(f.fields),
        params=dict(f.params),
        operator_count=info.operator_count,
        data_fields=info.fields,
        datasets=tuple(datasets),
    )
