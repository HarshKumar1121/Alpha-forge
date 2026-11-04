"""``/api/forge/studio``: bind a template to a field set, judge what it renders, write it up.

Read-only against BRAIN and free: the previews, the eligibility and the description draft are
all computed here from the synced catalog and the alpha type YAMLs. Nothing in this module
simulates, and nothing submits (CLAUDE.md rules 1 and 4) — Phase 6's pipelines take what the
Studio produced and spend quota on it through the Quota Governor.
"""

import asyncio
import hashlib
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import select

from ..brain.platform import GROUPING_FIELDS
from ..catalog.queries import Tuple4
from ..forge.alpha_types import load_all
from ..forge.data.fieldsets import resolve, scope_of
from ..forge.data.models import FieldSet
from ..forge.data.scope import Scope as ScopeModel
from ..forge.nuggets.models import Nugget
from ..forge.nuggets.service import resolved_treatment
from ..forge.research.binding import (
    MAX_RENDERED,
    ScopeData,
    available_groups,
    bind,
    field_infos,
    scope_catalog,
)
from ..forge.research.description import compose, draft
from ..forge.research.eligibility import judge_all, summarize
from ..forge.research.templates import Template, example, fits, get
from ..forge.studio.models import AlphaDescription, TemplateVersion, UserTemplate
from ..forge.studio.user_templates import (
    TemplateTextError,
    clone_text,
    parse,
    problems,
    to_yaml,
)
from ..schemas import Out
from ..tools.description import short
from .deps import State, refuse

router = APIRouter(prefix="/api/forge/studio", tags=["forge-studio"])


# --- wire shapes --------------------------------------------------------------


class EligibilityOut(Out):
    alpha_type: str
    ok: bool
    #: Why it does NOT qualify. Empty when it does.
    reasons: list[str]
    warnings: list[str]


class RenderedAlpha(Out):
    expression: str
    #: Slot name -> the field that filled it.
    fields: dict[str, str]
    #: Slot name -> the window or group chosen.
    params: dict[str, Any]
    operator_count: int
    data_fields: list[str]
    datasets: list[str]
    #: True when every alpha type fits — show one flag, not a badge per type.
    fits_all: bool
    #: Only the types it does NOT fit, each with why. Empty when ``fits_all``.
    eligibility: dict[str, EligibilityOut]
    #: The alpha types it does fit, for the summary line.
    fits: list[str]


class PreviewRequest(Out):
    template_id: str = Field(min_length=1, max_length=80)
    #: Exactly one of these names what to fill the template's field slots from.
    field_set_id: int | None = None
    #: A Data Nugget instead of a field set — its fields fill the slots, each wrapped in its
    #: own resolved data-treatment (override or default), not the template's own cleaning.
    nugget_id: str | None = None
    # forge: fields picked on the spot (the selection-first Template Lab flow) — same idea as
    # CompatibleFieldsRequest, so a template can be previewed and its slots swapped live without
    # first saving a named field set.
    field_ids: list[str] | None = Field(default=None, max_length=500)
    region: str | None = None
    universe: str | None = None
    delay: int | None = None
    instrument_type: str | None = None
    #: Slot name -> a pinned value (a field id, a window, a group), or a list of them.
    choices: dict[str, Any] = Field(default_factory=dict[str, Any])
    limit: int = Field(default=50, ge=1, le=MAX_RENDERED)


class NuggetSummaryOut(Out):
    nugget_id: str
    name: str
    rationale: str
    claim: str


class PreviewResponse(Out):
    template_id: str
    field_set: str
    scope: str
    rendered: list[RenderedAlpha]
    #: How many the template renders in all; ``rendered`` may be the first page of them.
    total: int
    #: Why nothing could be rendered.
    blockers: list[str]
    notes: list[str]
    #: Slot name -> the values this market can actually run.
    offered: dict[str, list[Any]]
    #: Field ids the set names that this market does not carry.
    missing_fields: list[str]
    #: How many of the rendered alphas fit each type.
    fit_counts: dict[str, int]
    #: Set when bound to a Data Nugget rather than a field set — its rationale and claim, for
    #: the Studio to show above the rendered alphas.
    nugget: NuggetSummaryOut | None = None


class DescriptionDraft(Out):
    expression: str
    idea: str
    data_rationale: str
    operator_rationale: str
    #: The three parts in BRAIN's template, which is what gets pasted into BRAIN.
    text: str
    #: Headings whose text is still too short for BRAIN.
    thin: list[str]
    #: True when this is a saved description rather than a fresh draft.
    saved: bool
    edited: bool
    #: The Data Nugget's own rationale and claim, shown above the expression, when the alpha
    #: was bound to one — never BRAIN's description text, which stays the three parts above.
    nugget_rationale: str | None = None
    nugget_claim: str | None = None


class Deleted(Out):
    deleted: bool


class ExportedTemplate(Out):
    template_id: str
    yaml_text: str
    source: Literal["builtin", "user"]


class DraftRequest(Out):
    expression: str = Field(min_length=1, max_length=8000)
    template_id: str | None = None
    field_set_id: int | None = None
    #: The Data Nugget this alpha was rendered from, if any — its rationale and claim are
    #: echoed back on the draft for the Studio to show above the expression.
    nugget_id: str | None = None


class SaveDescriptionRequest(Out):
    expression: str = Field(min_length=1, max_length=8000)
    idea: str = Field(default="", max_length=8000)
    data_rationale: str = Field(default="", max_length=8000)
    operator_rationale: str = Field(default="", max_length=8000)
    template_id: str | None = None
    scope: str = ""


# --- helpers ------------------------------------------------------------------


def expression_hash(expression: str) -> str:
    return hashlib.sha256(expression.strip().encode()).hexdigest()


async def _field_set(state: State, field_set_id: int) -> FieldSet:
    async with state.db.session() as session:
        row = await session.get(FieldSet, field_set_id)
    if row is None:
        raise refuse(404, "not_found", f"There is no field set {field_set_id}.")
    return row


async def _nugget(state: State, nugget_id: str) -> Nugget:
    async with state.db.session() as session:
        row = (await session.scalars(select(Nugget).where(Nugget.nugget_id == nugget_id))).first()
    if row is None:
        raise refuse(404, "not_found", f"No nugget {nugget_id!r}.")
    return row


def _nugget_summary(row: Nugget) -> NuggetSummaryOut:
    return NuggetSummaryOut(
        nugget_id=row.nugget_id, name=row.name, rationale=row.rationale, claim=row.claim
    )


async def _template(state: State, template_id: str) -> Template:
    """A built-in, or one of the user's own."""
    found = get(template_id)
    if found is not None:
        return found
    async with state.db.session() as session:
        row = (
            await session.scalars(
                select(UserTemplate).where(UserTemplate.template_id == template_id)
            )
        ).first()
    if row is None:
        raise refuse(404, "unknown_template", f"No template {template_id!r}.")
    try:
        return parse(row.yaml_text).template
    except TemplateTextError as exc:
        raise refuse(
            422,
            "template_unreadable",
            f"Your template {template_id!r} cannot be read: {exc}",
        ) from exc


async def _scope_data(state: State, scope: Tuple4, t: Template) -> ScopeData:
    """The market as the binder sees it: the set's fields are added by the caller."""
    wanted = sorted({*GROUPING_FIELDS, *t.needs})
    rows = await state.queries.fields_by_id(scope, wanted)
    catalog = scope_catalog([], rows)
    return ScopeData(catalog=catalog, groups=available_groups(catalog))


def _saved(row: AlphaDescription, nugget: Nugget | None = None) -> DescriptionDraft:
    """A stored description as the editor reads it, re-composed so the text is never stale."""
    text = compose(row.idea, row.data_rationale, row.operator_rationale)
    return DescriptionDraft(
        expression=row.expression,
        idea=row.idea,
        data_rationale=row.data_rationale,
        operator_rationale=row.operator_rationale,
        text=text,
        thin=short(text),
        saved=True,
        edited=row.edited,
        nugget_rationale=nugget.rationale if nugget else None,
        nugget_claim=nugget.claim if nugget else None,
    )


async def _stored_description(state: State, expression: str) -> AlphaDescription | None:
    digest = expression_hash(expression)
    async with state.db.session() as session:
        return (
            await session.scalars(
                select(AlphaDescription).where(AlphaDescription.expression_hash == digest)
            )
        ).first()


async def _operators(state: State) -> dict[str, dict[str, Any]]:
    """BRAIN's operator reference, for the description draft. Empty when signed out."""
    try:
        listed = await state.metadata.cached_operators()
    except Exception:  # noqa: BLE001 - a draft without operator text is still a draft
        return {}
    return {str(o["name"]): o for o in (listed or []) if o.get("name")}


# --- routes -------------------------------------------------------------------


@router.post("/preview")
async def preview(body: PreviewRequest, state: State) -> PreviewResponse:
    """Every alpha a template renders on a field set or a Data Nugget, and what each one
    qualifies for."""
    sources = (
        body.field_set_id is not None,
        body.nugget_id is not None,
        body.field_ids is not None,
    )
    if sum(sources) != 1:
        raise refuse(
            422,
            "bad_source",
            "Give exactly one of fieldSetId, nuggetId or fieldIds to fill the template's field "
            "slots.",
        )
    t = await _template(state, body.template_id)

    nugget_out: NuggetSummaryOut | None = None
    treatments: dict[str, str] | None = None
    if body.field_set_id is not None:
        row = await _field_set(state, body.field_set_id)
        scope = scope_of(row).to_tuple4()
        resolved = await resolve(row, state.queries)
        set_rows = resolved["fields"]
        missing_fields = resolved["missing"]
        field_set_label = row.name
    elif body.field_ids is not None:
        if body.region is None or body.universe is None or body.delay is None:
            raise refuse(
                422,
                "bad_source",
                "fieldIds needs region, universe and delay to know which market to preview on.",
            )
        scope = ScopeModel(
            instrument_type=body.instrument_type or "EQUITY",
            region=body.region,
            delay=body.delay,
            universe=body.universe,
        ).to_tuple4()
        set_rows = await state.queries.fields_by_id(scope, body.field_ids)
        found = {str(r["field_id"]) for r in set_rows}
        missing_fields = [f for f in body.field_ids if f not in found]
        field_set_label = "(fields picked on this screen)"
    else:
        nugget = await _nugget(state, body.nugget_id)  # type: ignore[arg-type]
        scope = ScopeModel(
            instrument_type=nugget.instrument_type,
            region=nugget.region,
            delay=nugget.delay,
            universe=nugget.universe,
        ).to_tuple4()
        set_rows = await state.queries.fields_by_id(scope, list(nugget.field_ids))
        found = {str(r["field_id"]) for r in set_rows}
        missing_fields = [f for f in nugget.field_ids if f not in found]
        field_set_label = f"Nugget: {nugget.name}"
        treatments = {f: resolved_treatment(f, nugget.data_treatment) for f in nugget.field_ids}
        nugget_out = _nugget_summary(nugget)

    scope_data = await _scope_data(state, scope, t)
    # The field source's own fields join the market's grouping fields and the template's needs.
    catalog = {**scope_catalog(set_rows), **scope_data.catalog}
    scope_data = ScopeData(catalog=catalog, groups=available_groups(catalog))

    types = load_all()
    target = next((str(r.get("dataset_id") or "") for r in set_rows), None)

    def work() -> PreviewResponse:
        result = bind(
            t,
            field_infos(set_rows),
            scope_data,
            choices=body.choices or None,
            limit=body.limit,
            treatments=treatments,
        )
        rendered: list[RenderedAlpha] = []
        counts: dict[str, int] = dict.fromkeys(types, 0)
        for alpha in result.rendered:
            verdicts = judge_all(alpha.expression, types, catalog=catalog, target_dataset=target)
            summary = summarize(verdicts)
            for name in summary.fits:
                counts[name] += 1
            rendered.append(
                RenderedAlpha(
                    expression=alpha.expression,
                    fields=alpha.fields,
                    params=alpha.params,
                    operator_count=alpha.operator_count,
                    data_fields=list(alpha.data_fields),
                    datasets=list(alpha.datasets),
                    fits_all=summary.fits_all,
                    eligibility={
                        name: EligibilityOut(
                            alpha_type=v.alpha_type,
                            ok=v.ok,
                            reasons=list(v.reasons),
                            warnings=list(v.warnings),
                        )
                        for name, v in summary.failing.items()
                    },
                    fits=list(summary.fits),
                )
            )
        return PreviewResponse(
            template_id=t.id,
            field_set=field_set_label,
            scope=scope.label,
            rendered=rendered,
            total=result.total,
            blockers=list(result.blockers),
            notes=list(result.notes),
            offered=result.offered,
            missing_fields=list(missing_fields),
            fit_counts=counts,
            nugget=nugget_out,
        )

    # Expanding and linting a couple of hundred alphas is pure CPU; keep it off the loop.
    return await asyncio.to_thread(work)


@router.post("/description/draft")
async def description_draft(body: DraftRequest, state: State) -> DescriptionDraft:
    """A first draft of BRAIN's Power Pool description for one rendered alpha.

    Returns the saved description instead when one exists, so opening an alpha a second time
    shows what was written rather than overwriting it with a fresh draft.
    """
    nugget = await _nugget(state, body.nugget_id) if body.nugget_id else None

    if (stored := await _stored_description(state, body.expression)) is not None:
        return _saved(stored, nugget)

    t = await _template(state, body.template_id) if body.template_id else None
    fields: dict[str, dict[str, Any]] = {}
    if body.field_set_id is not None:
        row = await _field_set(state, body.field_set_id)
        resolved = await resolve(row, state.queries)
        fields = scope_catalog(resolved["fields"])
    elif nugget is not None:
        scope = ScopeModel(
            instrument_type=nugget.instrument_type,
            region=nugget.region,
            delay=nugget.delay,
            universe=nugget.universe,
        ).to_tuple4()
        rows = await state.queries.fields_by_id(scope, list(nugget.field_ids))
        fields = scope_catalog(rows)
    operators = await _operators(state)
    made = draft(t, body.expression, fields=fields, operators=operators)
    return DescriptionDraft(
        expression=body.expression,
        idea=made.idea,
        data_rationale=made.data,
        operator_rationale=made.operators,
        text=made.text,
        thin=list(made.thin),
        saved=False,
        edited=False,
        nugget_rationale=nugget.rationale if nugget else None,
        nugget_claim=nugget.claim if nugget else None,
    )


@router.put("/description")
async def save_description(body: SaveDescriptionRequest, state: State) -> DescriptionDraft:
    """Keep an edited description with its expression. Never submitted by any code path."""
    digest = expression_hash(body.expression)
    async with state.db.session() as session:
        row = (
            await session.scalars(
                select(AlphaDescription).where(AlphaDescription.expression_hash == digest)
            )
        ).first()
        if row is None:
            row = AlphaDescription(expression_hash=digest, expression=body.expression)
            session.add(row)
        row.idea = body.idea
        row.data_rationale = body.data_rationale
        row.operator_rationale = body.operator_rationale
        row.template_id = body.template_id
        row.scope = body.scope
        row.edited = True
        await session.flush()
        await session.refresh(row)
    return _saved(row)


@router.get("/description")
async def get_description(
    state: State, expression: Annotated[str, Query(min_length=1, max_length=8000)]
) -> DescriptionDraft | None:
    """The saved description for an expression, or ``null`` when none was written."""
    row = await _stored_description(state, expression)
    return None if row is None else _saved(row)


# --- templates of your own ----------------------------------------------------


class MyTemplate(Out):
    id: int
    template_id: str
    name: str
    yaml_text: str
    version: int
    cloned_from: str | None
    created_at: datetime
    updated_at: datetime


class MyTemplateVersion(Out):
    version: int
    yaml_text: str
    note: str
    created_at: datetime


class TemplateTextIn(Out):
    yaml_text: str = Field(min_length=1, max_length=20_000)
    #: What changed, kept with the version this save creates.
    note: str = Field(default="", max_length=500)


class CloneRequest(Out):
    #: The built-in to copy. Built-ins themselves are never edited.
    template_id: str = Field(min_length=1, max_length=80)
    new_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(default="", max_length=120)


class LintRequest(Out):
    yaml_text: str = Field(min_length=1, max_length=20_000)


class LintResponse(Out):
    ok: bool
    problems: list[str]
    #: The template rendered on placeholder fields, when it parses.
    example: str | None
    #: Which alpha types the template could fit at its best legal fill.
    fits: dict[str, bool]


def _mine(row: UserTemplate) -> MyTemplate:
    return MyTemplate(
        id=row.id,
        template_id=row.template_id,
        name=row.name,
        yaml_text=row.yaml_text,
        version=row.version,
        cloned_from=row.cloned_from,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _checked(text: str) -> Template:
    """Parse the user's YAML or refuse with every problem in it."""
    try:
        return parse(text).template
    except TemplateTextError as exc:
        raise refuse(422, "invalid_template", "; ".join(exc.problems)) from exc


@router.get("/my-templates")
async def list_my_templates(state: State) -> list[MyTemplate]:
    """Every template you wrote, most recently changed first."""
    async with state.db.session() as session:
        rows = (
            await session.scalars(
                select(UserTemplate).order_by(
                    UserTemplate.updated_at.desc(), UserTemplate.id.desc()
                )
            )
        ).all()
    return [_mine(r) for r in rows]


@router.post("/my-templates")
async def create_my_template(body: TemplateTextIn, state: State) -> MyTemplate:
    """Save a new template of your own. Its id may not be one the repository already uses."""
    t = _checked(body.yaml_text)
    if get(t.id) is not None:
        raise refuse(
            409,
            "builtin_id",
            f"{t.id!r} is a built-in template. Clone it instead, or choose another id.",
        )
    async with state.db.session() as session:
        clash = (
            await session.scalars(select(UserTemplate).where(UserTemplate.template_id == t.id))
        ).first()
        if clash is not None:
            raise refuse(409, "id_taken", f"You already have a template called {t.id!r}.")
        row = UserTemplate(template_id=t.id, name=t.name, yaml_text=body.yaml_text, version=1)
        session.add(row)
        session.add(
            TemplateVersion(
                template_id=t.id, version=1, yaml_text=body.yaml_text, note=body.note or "Created"
            )
        )
        await session.flush()
        await session.refresh(row)
    return _mine(row)


@router.put("/my-templates/{template_id}")
async def update_my_template(template_id: str, body: TemplateTextIn, state: State) -> MyTemplate:
    """Save an edit as a new version. The id inside the YAML may not change."""
    t = _checked(body.yaml_text)
    if t.id != template_id:
        raise refuse(
            422,
            "id_changed",
            f"This template's id is {template_id!r}; the YAML says {t.id!r}. "
            "Clone it if you want a second template.",
        )
    async with state.db.session() as session:
        row = (
            await session.scalars(
                select(UserTemplate).where(UserTemplate.template_id == template_id)
            )
        ).first()
        if row is None:
            raise refuse(404, "not_found", f"You have no template {template_id!r}.")
        if row.yaml_text != body.yaml_text:
            row.version += 1
            row.yaml_text = body.yaml_text
            row.name = t.name
            session.add(
                TemplateVersion(
                    template_id=template_id,
                    version=row.version,
                    yaml_text=body.yaml_text,
                    note=body.note,
                )
            )
        await session.flush()
        await session.refresh(row)
    return _mine(row)


@router.get("/my-templates/{template_id}/versions")
async def template_versions(template_id: str, state: State) -> list[MyTemplateVersion]:
    """Every saved version, newest first — what produced an alpha stays readable."""
    async with state.db.session() as session:
        rows = (
            await session.scalars(
                select(TemplateVersion)
                .where(TemplateVersion.template_id == template_id)
                .order_by(TemplateVersion.version.desc())
            )
        ).all()
    return [
        MyTemplateVersion(
            version=r.version, yaml_text=r.yaml_text, note=r.note, created_at=r.created_at
        )
        for r in rows
    ]


@router.delete("/my-templates/{template_id}")
async def delete_my_template(template_id: str, state: State) -> Deleted:
    """Remove a template of your own. Its versions go with it; built-ins are untouched."""
    async with state.db.session() as session:
        row = (
            await session.scalars(
                select(UserTemplate).where(UserTemplate.template_id == template_id)
            )
        ).first()
        if row is None:
            raise refuse(404, "not_found", f"You have no template {template_id!r}.")
        await session.delete(row)
        for version in (
            await session.scalars(
                select(TemplateVersion).where(TemplateVersion.template_id == template_id)
            )
        ).all():
            await session.delete(version)
    return Deleted(deleted=True)


@router.post("/my-templates/clone")
async def clone_template(body: CloneRequest, state: State) -> MyTemplate:
    """Copy a built-in under a new id, ready to edit. The built-in itself stays read-only."""
    original = get(body.template_id)
    if original is None:
        raise refuse(404, "unknown_template", f"No built-in template {body.template_id!r}.")
    if get(body.new_id) is not None:
        raise refuse(409, "builtin_id", f"{body.new_id!r} is already a built-in template.")
    text = clone_text(original, body.new_id, body.name)
    return await create_my_template(
        TemplateTextIn(yaml_text=text, note=f"Cloned from {original.id}"), state
    )


@router.post("/lint")
async def lint_template(body: LintRequest, state: State) -> LintResponse:  # noqa: ARG001
    """What is wrong with this YAML, for the editor's live lint. Free and offline."""
    found = problems(body.yaml_text)
    if found:
        return LintResponse(ok=False, problems=found, example=None, fits={})
    t = parse(body.yaml_text).template
    return LintResponse(
        ok=True,
        problems=[],
        example=example(t),
        fits={name: fit.ok for name, fit in fits(t).items()},
    )


@router.get("/my-templates/{template_id}/export")
async def export_template(template_id: str, state: State) -> ExportedTemplate:
    """A template as a library file, built-in or your own: what you paste into config/forge."""
    builtin = get(template_id)
    if builtin is not None:
        return ExportedTemplate(
            template_id=template_id, yaml_text=to_yaml(builtin), source="builtin"
        )
    async with state.db.session() as session:
        row = (
            await session.scalars(
                select(UserTemplate).where(UserTemplate.template_id == template_id)
            )
        ).first()
    if row is None:
        raise refuse(404, "not_found", f"No template {template_id!r}.")
    return ExportedTemplate(template_id=template_id, yaml_text=row.yaml_text, source="user")
