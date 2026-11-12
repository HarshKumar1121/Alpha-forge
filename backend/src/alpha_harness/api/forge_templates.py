"""Forge Template Library: browse the typed-slot templates in ``config/forge/templates``.

Read-only and free: nothing here simulates. Alpha-type fit is computed by linting each
template against every alpha type YAML (:func:`~alpha_harness.forge.research.templates.fits`),
and the Gym example is what "Try on Gym" opens the Gym Playground with.
"""

import asyncio
from statistics import median
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select

from ..catalog.queries import FieldFilter
from ..forge.alpha_types import load_all
from ..forge.data.fieldsets import MAX_FIELDS, resolve
from ..forge.data.models import MANUAL, FieldSet
from ..forge.data.scope import Scope as ScopeModel
from ..forge.expr import analyse
from ..forge.local.template_fill import Unavailable, gym_example
from ..forge.research.binding import field_infos
from ..forge.research.templates import (
    FieldInfo,
    FieldSlot,
    GroupSlot,
    Template,
    WindowSlot,
    example,
    fits,
    fits_fields,
    fits_pool,
    get,
    load_library,
    pairing_problem,  # forge: Select pairing reason (State D)
    slot_fit,
)
from ..forge.studio.models import TemplateStats, UserTemplate
from ..forge.studio.user_templates import TemplateTextError, builtin_ids, merged
from ..forge.studio.user_templates import parse as parse_user
from ..schemas import Out
from .deps import State, refuse

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

router = APIRouter(prefix="/api/forge/templates", tags=["forge-templates"])


class ForgeTemplateFit(Out):
    ok: bool
    #: Still fits with the signal negated (one more operator).
    flipped_ok: bool
    errors: list[str]
    warnings: list[str]


class ForgeTemplateStats(Out):
    """What this template has produced, summed over every cut recorded for it.

    Filled by the Phase 6 pipelines; zero everywhere until one has run.
    """

    sims: int = 0
    #: Alphas BRAIN has decided on, and those that passed every check.
    checked: int = 0
    passed: int = 0
    #: ``passed / checked``; ``None`` until BRAIN has decided one.
    pass_rate: float | None = None
    #: The median of the per-cut medians: one lucky alpha cannot carry it.
    median_train_sharpe: float | None = None
    families: int = 0
    #: How many (dataset category x alpha type x scope) cuts are behind these numbers.
    cuts: int = 0


class ForgeTemplateSummary(Out):
    id: str
    name: str
    category: str
    complexity: str
    angle: str
    operator_count: int
    fits: dict[str, ForgeTemplateFit]
    gym_available: bool
    #: ``builtin`` lives in config/forge/templates and is read-only; ``user`` is yours.
    source: Literal["builtin", "user"] = "builtin"
    stats: ForgeTemplateStats = Field(default_factory=ForgeTemplateStats)


class CompatibleFieldsRequest(Out):
    """Fields picked on the spot (e.g. the Workshop's field picker) — no saved field set
    needed, same shape as pipelines' own inline-fields preview."""

    field_ids: list[str] = Field(min_length=1, max_length=500)
    region: str
    universe: str
    delay: int
    instrument_type: str = "EQUITY"


class CompatibleTemplate(Out):
    id: str
    name: str
    category: str
    complexity: str


class CompatibleTemplates(Out):
    #: Templates whose field slots these fields could plausibly fill — the Workshop's
    #: template picker only ever offers from this list, so it never suggests a template that
    #: needs fields nobody has selected.
    templates: list[CompatibleTemplate]


class ForgeTemplateLoadError(Out):
    file: str
    template_id: str | None
    message: str


class ForgeTemplateList(Out):
    templates: list[ForgeTemplateSummary]
    errors: list[ForgeTemplateLoadError]
    alpha_types: list[str]


class ForgeTemplateSlot(Out):
    name: str
    kind: Literal["field", "window", "group"]
    archetype: list[str] = Field(default_factory=list[str])
    type: str | None = None
    aggregate: str | None = None
    same_dataset_as: str | None = None
    different_dataset_from: str | None = None
    hint: str = ""
    values: list[int | str] = Field(default_factory=list[int | str])


class ForgeTemplateGym(Out):
    expression: str | None
    fields: dict[str, str]
    unavailable_reason: str | None


class ForgeTemplateDetail(ForgeTemplateSummary):
    rationale: str
    expr: str
    only_if: str | None
    slots: list[ForgeTemplateSlot]
    needs: list[str]
    example: str
    fields: list[str]
    depth: int
    lookbacks: list[int]
    gym: ForgeTemplateGym


def _slot(name: str, s: object) -> ForgeTemplateSlot:
    if isinstance(s, FieldSlot):
        return ForgeTemplateSlot(
            name=name,
            kind="field",
            archetype=list(s.archetype),
            type=s.type,
            aggregate=s.aggregate,
            same_dataset_as=s.same_dataset_as,
            different_dataset_from=s.different_dataset_from,
            hint=s.hint,
        )
    kind: Literal["window", "group"] = "group" if isinstance(s, GroupSlot) else "window"
    return ForgeTemplateSlot(name=name, kind=kind, values=list(getattr(s, "values", ())))


# forge: the Template Lab's selection-first flow picks a market and a dataset category before
# any field — so eligibility has to be checked against the scope's whole field catalog, not a
# field_ids list the user has not picked yet.
class ScopeTemplate(Out):
    id: str
    name: str
    #: The template author's own taxonomy, e.g. "fundamental/value-quality" — the part before
    #: the slash is what the Template Lab offers as a dataset category.
    category: str
    dataset_category: str
    complexity: str
    angle: str
    rationale: str
    #: False when nothing in this market's field catalog could fill every field slot.
    scope_eligible: bool
    slots: list[ForgeTemplateSlot]


class ScopeTemplates(Out):
    templates: list[ScopeTemplate]
    #: Every dataset category any template declares, for the picker's chips — not filtered to
    #: this scope, so switching market never hides a category the user had open.
    dataset_categories: list[str]


def _summary(
    t: Template,
    *,
    source: Literal["builtin", "user"] = "builtin",
    stats: ForgeTemplateStats | None = None,
) -> ForgeTemplateSummary:
    return ForgeTemplateSummary(
        id=t.id,
        name=t.name,
        category=t.category,
        complexity=t.complexity,
        angle=t.angle,
        operator_count=analyse(example(t)).operator_count,
        fits={
            k: ForgeTemplateFit(
                ok=f.ok, flipped_ok=f.flipped_ok, errors=list(f.errors), warnings=list(f.warnings)
            )
            for k, f in fits(t).items()
        },
        gym_available=not isinstance(gym_example(t), Unavailable),
        source=source,
        stats=stats or ForgeTemplateStats(),
    )


def summarise_stats(rows: Sequence[TemplateStats]) -> ForgeTemplateStats:
    """Every recorded cut of one template, as one line for the library.

    Sims and alphas add up; the Sharpe does not, so the median of the cuts' medians is used —
    a template that worked on one dataset and failed on five should not read as a success.
    """
    if not rows:
        return ForgeTemplateStats()
    checked = sum(r.checked for r in rows)
    passed = sum(r.passed for r in rows)
    medians = sorted(r.median_train_sharpe for r in rows if r.median_train_sharpe is not None)
    middle = median(medians) if medians else None
    return ForgeTemplateStats(
        sims=sum(r.sims for r in rows),
        checked=checked,
        passed=passed,
        pass_rate=round(passed / checked, 4) if checked else None,
        median_train_sharpe=round(middle, 4) if middle is not None else None,
        families=sum(r.families for r in rows),
        cuts=len(rows),
    )


def _list(
    user: Sequence[tuple[str, str]] = (),
    stats: Mapping[str, list[TemplateStats]] | None = None,
) -> ForgeTemplateList:
    """The library: the repository's templates, then the user's own."""
    loaded = load_library()
    by_template = stats or {}
    templates = [
        _summary(t, stats=summarise_stats(by_template.get(t.id, []))) for t in loaded.templates
    ]
    errors = [
        ForgeTemplateLoadError(file=e.file, template_id=e.template_id, message=e.message)
        for e in loaded.errors
    ]
    for template_id, text in user:
        try:
            t = parse_user(text).template
        except TemplateTextError as exc:
            errors.append(
                ForgeTemplateLoadError(
                    file="your templates", template_id=template_id, message=str(exc)
                )
            )
            continue
        templates.append(
            _summary(t, source="user", stats=summarise_stats(by_template.get(t.id, [])))
        )
    return ForgeTemplateList(
        templates=templates,
        errors=errors,
        alpha_types=sorted(load_all()),
    )


def _detail(t: Template, source: Literal["builtin", "user"] = "builtin") -> ForgeTemplateDetail:
    info = analyse(example(t))
    gym = gym_example(t)
    return ForgeTemplateDetail(
        **_summary(t, source=source).model_dump(),
        rationale=t.rationale,
        expr=t.expr,
        only_if=t.only_if,
        slots=[_slot(k, s) for k, s in t.slots.items()],
        needs=sorted(t.needs),
        example=example(t),
        fields=list(info.fields),
        depth=info.depth,
        lookbacks=list(info.lookbacks),
        gym=(
            ForgeTemplateGym(expression=None, fields={}, unavailable_reason=gym.reason)
            if isinstance(gym, Unavailable)
            else ForgeTemplateGym(
                expression=gym.expression, fields=gym.fields, unavailable_reason=None
            )
        ),
    )


@router.get("")
async def list_templates(state: State) -> ForgeTemplateList:
    """Every template — the repository's and your own — with computed alpha-type fit, what it
    has produced so far, and the entries that failed to load."""
    async with state.db.session() as session:
        mine = [
            (r.template_id, r.yaml_text)
            for r in (
                await session.scalars(select(UserTemplate).order_by(UserTemplate.template_id))
            ).all()
        ]
        stats: dict[str, list[TemplateStats]] = {}
        for row in (await session.scalars(select(TemplateStats))).all():
            stats.setdefault(row.template_id, []).append(row)
    # The first call profiles the Gym's fields (a few seconds): keep it off the event loop.
    return await asyncio.to_thread(_list, mine, stats)


async def _picked_fields(
    body: CompatibleFieldsRequest, state: State
) -> tuple[list[FieldInfo], list[str]]:
    """The picked fields as template candidates, and the ids the catalog does not have.

    Builds a transient (never-persisted) field set the same way pipelines' inline-fields
    preview does, and resolves it against the real catalog so each field's dataset is known
    (the same-dataset/semantic-pairing rules in :func:`~..forge.research.templates.bindings`
    need that).
    """
    transient = FieldSet(
        name="(fields picked on this screen)",
        instrument_type=body.instrument_type,
        region=body.region,
        delay=body.delay,
        universe=body.universe,
        kind=MANUAL,
        field_ids=body.field_ids,
        filter=None,
    )
    resolved = await resolve(transient, state.queries)
    return field_infos(resolved["fields"]), resolved["missing"]


@router.post("/compatible")
async def compatible_templates(body: CompatibleFieldsRequest, state: State) -> CompatibleTemplates:
    """Which templates these fields could bind to, right now — free and offline.

    Keeps only the templates :func:`~..forge.research.templates.fits_fields` says these fields
    can actually fill.
    """
    fields, _ = await _picked_fields(body, state)
    return CompatibleTemplates(
        templates=[
            CompatibleTemplate(id=t.id, name=t.name, category=t.category, complexity=t.complexity)
            for t in load_library().templates
            if fits_fields(t, fields)
        ]
    )


class FitTemplatesRequest(CompatibleFieldsRequest):
    """A Select pool: the same picked fields, capped at the 50 that the pairing check takes."""

    field_ids: list[str] = Field(min_length=1, max_length=50)


class FitSlot(Out):
    name: str
    #: ``field`` | ``window`` | ``group``
    kind: str
    type: str | None = None
    archetype: list[str] = Field(default_factory=list)
    aggregate: str | None = None
    values: list[int | str] = Field(default_factory=list)
    #: Pool fields that could fill this field slot on their own; empty for window/group slots.
    candidates: list[str] = Field(default_factory=list)
    #: Why no pool field can fill this slot, when none can.
    problem: str | None = None


class FitTemplate(Out):
    id: str
    name: str
    source: Literal["builtin", "user"]
    category: str
    dataset_category: str
    complexity: str
    angle: str
    rationale: str
    expr: str
    fits: bool
    #: Set when ``fits`` is False because the check was declined, not because it failed.
    note: str | None = None
    #: Set when the pool cannot pair this template's slots, judged from each slot's candidates
    #: alone: a same-dataset slot pair whose datasets differ or are unknown, or a hint pair
    #: (mean/std, ...) that no candidate pair satisfies. Names the reason; null otherwise.
    pairing_problem: str | None = None
    slots: list[FitSlot]


class FitTemplates(Out):
    templates: list[FitTemplate]
    #: Picked ids the catalog does not have for this market.
    missing: list[str]
    #: Your own templates that no longer parse, with the reason.
    errors: list[str]


def _fit_template(
    t: Template, fields: Sequence[FieldInfo], source: Literal["builtin", "user"]
) -> FitTemplate:
    slots: list[FitSlot] = []
    by_id = {f.id: f for f in fields}
    candidates: dict[str, list[FieldInfo]] = {}
    for name, slot in t.slots.items():
        if isinstance(slot, FieldSlot):
            sf = slot_fit(slot, fields)
            candidates[name] = [by_id[i] for i in sf.candidates]
            slots.append(
                FitSlot(
                    name=name,
                    kind="field",
                    type=slot.type,
                    archetype=list(slot.archetype),
                    aggregate=slot.aggregate,
                    candidates=list(sf.candidates),
                    problem=sf.problem,
                )
            )
        elif isinstance(slot, WindowSlot):
            slots.append(FitSlot(name=name, kind="window", values=list(slot.values)))
        else:
            slots.append(FitSlot(name=name, kind="group", values=list(slot.values)))
    fits_ok, note = fits_pool(t, fields)
    pairing = None if fits_ok else pairing_problem(t, candidates)  # forge: State D reason
    return FitTemplate(
        id=t.id,
        name=t.name,
        source=source,
        category=t.category,
        dataset_category=t.category.split("/", 1)[0],
        complexity=t.complexity,
        angle=t.angle,
        rationale=t.rationale,
        expr=t.expr,
        fits=fits_ok,
        note=note,
        pairing_problem=pairing,
        slots=slots,
    )


@router.post("/fit")
async def fit_templates(body: FitTemplatesRequest, state: State) -> FitTemplates:
    """Every template, read against these fields: which slots they could fill and why not.

    Unlike ``/compatible``, nothing is dropped — a template that does not fit comes back with
    the problem line for each slot, so the Select screen can show why rather than hide it.
    Built-ins and your own templates are both checked; your own are read from the database.
    """
    fields, missing = await _picked_fields(body, state)
    async with state.db.session() as session:
        mine = [
            (r.template_id, r.yaml_text)
            for r in (
                await session.scalars(select(UserTemplate).order_by(UserTemplate.template_id))
            ).all()
        ]
    templates, errors = merged(mine)
    builtin = builtin_ids()
    fitted = await asyncio.to_thread(
        lambda: [
            _fit_template(t, fields, "builtin" if t.id in builtin else "user") for t in templates
        ]
    )
    return FitTemplates(templates=fitted, missing=missing, errors=errors)


@router.get("/by-scope")
async def templates_by_scope(
    region: str,
    universe: str,
    delay: int,
    state: State,
    instrument_type: str = "EQUITY",
    dataset_category: str | None = None,
) -> ScopeTemplates:
    """Templates for a market, picked before any field — the Template Lab's own order.

    Reads every field this scope's catalog carries (up to the same 500-field cap a field set
    resolves to) and keeps the templates whose slots that catalog could fill at all
    (:func:`~..forge.research.templates.fits_fields`), the same check ``/compatible`` runs
    against a user's own field_ids. Templates outside ``dataset_category`` (the template's own
    ``category`` prefix, e.g. ``fundamental``) are left out entirely rather than marked
    ineligible — the Lab only ever shows templates for the category in hand.
    """
    scope = ScopeModel(
        instrument_type=instrument_type, region=region, delay=delay, universe=universe
    ).to_tuple4()
    page = await state.queries.fields(scope, FieldFilter(limit=MAX_FIELDS))
    fields = field_infos(page["results"])
    all_templates = load_library().templates
    categories = sorted({t.category.split("/", 1)[0] for t in all_templates})
    templates = [
        ScopeTemplate(
            id=t.id,
            name=t.name,
            category=t.category,
            dataset_category=t.category.split("/", 1)[0],
            complexity=t.complexity,
            angle=t.angle,
            rationale=t.rationale,
            scope_eligible=fits_fields(t, fields),
            slots=[_slot(k, s) for k, s in t.slots.items()],
        )
        for t in all_templates
        if dataset_category is None or t.category.split("/", 1)[0] == dataset_category
    ]
    return ScopeTemplates(templates=templates, dataset_categories=categories)


@router.get("/{template_id}")
async def template_detail(template_id: str, state: State) -> ForgeTemplateDetail:
    """One template, built-in or your own."""
    t = get(template_id)
    source: Literal["builtin", "user"] = "builtin"
    if t is None:
        async with state.db.session() as session:
            row = (
                await session.scalars(
                    select(UserTemplate).where(UserTemplate.template_id == template_id)
                )
            ).first()
        if row is None:
            raise refuse(404, "unknown_template", f"No template {template_id!r}")
        try:
            t = parse_user(row.yaml_text).template
        except TemplateTextError as exc:
            raise refuse(
                422, "template_unreadable", f"{template_id!r} cannot be read: {exc}"
            ) from exc
        source = "user"
    return await asyncio.to_thread(_detail, t, source)
