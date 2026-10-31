"""Forge Template Library: browse the typed-slot templates in ``config/forge/templates``.

Read-only and free: nothing here simulates. Alpha-type fit is computed by linting each
template against every alpha type YAML (:func:`~alpha_harness.forge.research.templates.fits`),
and the Gym example is what "Try on Gym" opens the Gym Playground with.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter
from pydantic import Field

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
from ..schemas import Out
from .deps import refuse

router = APIRouter(prefix="/api/forge/templates", tags=["forge-templates"])


class ForgeTemplateFit(Out):
    ok: bool
    #: Still fits with the signal negated (one more operator).
    flipped_ok: bool
    errors: list[str]
    warnings: list[str]


class ForgeTemplateSummary(Out):
    id: str
    name: str
    category: str
    complexity: str
    angle: str
    operator_count: int
    fits: dict[str, ForgeTemplateFit]
    gym_available: bool


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


def _summary(t: Template) -> ForgeTemplateSummary:
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
    )


def _list() -> ForgeTemplateList:
    loaded = load_library()
    return ForgeTemplateList(
        templates=[_summary(t) for t in loaded.templates],
        errors=[
            ForgeTemplateLoadError(file=e.file, template_id=e.template_id, message=e.message)
            for e in loaded.errors
        ],
        alpha_types=sorted(load_all()),
    )


def _detail(t: Template) -> ForgeTemplateDetail:
    info = analyse(example(t))
    gym = gym_example(t)
    return ForgeTemplateDetail(
        **_summary(t).model_dump(),
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
async def list_templates() -> ForgeTemplateList:
    """Every template, with computed alpha-type fit, plus the files that failed to load."""
    # The first call profiles the Gym's fields (a few seconds): keep it off the event loop.
    return await asyncio.to_thread(_list)


@router.get("/{template_id}")
async def template_detail(template_id: str) -> ForgeTemplateDetail:
    t = get(template_id)
    if t is None:
        raise refuse(404, "unknown_template", f"No template {template_id!r}")
    return await asyncio.to_thread(_detail, t)
