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

from ..forge.alpha_types import load_all
from ..forge.expr import analyse
from ..forge.local.template_fill import Unavailable, gym_example
from ..forge.research.templates import (
    FieldSlot,
    GroupSlot,
    Template,
    example,
    fits,
    get,
    load_library,
)
from ..forge.studio.models import TemplateStats, UserTemplate
from ..forge.studio.user_templates import TemplateTextError
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
