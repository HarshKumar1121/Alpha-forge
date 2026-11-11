"""``/api/forge/nuggets``: Data Nuggets — 2+ fields, a written rationale, a data-treatment note
and a falsifiable claim, saved and versioned so any template can bind to them instead of raw
fields. Everything here is free and offline except the correlation endpoint's optional
``fetch_prod``, the only call that reaches BRAIN (CLAUDE.md rule 1: still never submits).
"""

import re
from datetime import datetime
from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import select

from ..catalog.queries import FieldFilter, Tuple4
from ..forge import analytics as an
from ..forge.data.scope import Scope as ScopeModel
from ..forge.data.scope import check_scope
from ..forge.nuggets import correlation as corr
from ..forge.nuggets import field_search as fs
from ..forge.nuggets.models import Nugget, NuggetVersion
from ..forge.nuggets.service import NuggetDraft, resolved_treatment, snapshot, validate_draft
from ..forge.nuggets.suggest import PairingSuggestion, suggest_pairings
from ..forge.research.field_profile import FieldProfile, profile_field
from ..forge.storage.access import touch_pnl
from ..schemas import Out
from .deps import State, refuse

router = APIRouter(prefix="/api/forge/nuggets", tags=["forge-nuggets"])


# --- wire shapes --------------------------------------------------------------


class NuggetScope(Out):
    instrument_type: str = "EQUITY"
    region: str = Field(min_length=1, max_length=16)
    delay: int = Field(ge=0, le=1)
    universe: str = Field(min_length=1, max_length=32)

    def to_scope_model(self) -> ScopeModel:
        return ScopeModel(
            instrument_type=self.instrument_type,
            region=self.region,
            delay=self.delay,
            universe=self.universe,
        )

    def to_tuple4(self) -> Tuple4:
        return self.to_scope_model().to_tuple4()


class FieldProfileOut(Out):
    field_id: str
    dataset_id: str | None
    dataset_name: str | None
    description: str | None
    field_type: str
    needs_aggregation: bool
    coverage: float | None
    date_coverage: float | None
    cadence: str
    category: str


def _profile_out(p: FieldProfile) -> FieldProfileOut:
    return FieldProfileOut(
        field_id=p.field_id,
        dataset_id=p.dataset_id,
        dataset_name=p.dataset_name,
        description=p.description,
        field_type=p.field_type,
        needs_aggregation=p.needs_aggregation,
        coverage=p.coverage,
        date_coverage=p.date_coverage,
        cadence=p.cadence,
        category=p.category,
    )


class PairingSuggestionOut(Out):
    field_a: str
    field_b: str
    valid: bool
    reason: str


def _suggestion_out(s: PairingSuggestion) -> PairingSuggestionOut:
    return PairingSuggestionOut(
        field_a=s.field_a, field_b=s.field_b, valid=s.valid, reason=s.reason
    )


class ProfileRequest(Out):
    scope: NuggetScope
    field_ids: list[str] = Field(min_length=1, max_length=50)


class ProfileResponse(Out):
    fields: list[FieldProfileOut]
    #: Ids asked for that the catalog has no row for, in this scope.
    missing: list[str]


class SuggestResponse(ProfileResponse):
    suggestions: list[PairingSuggestionOut]


class FieldSearchRequest(Out):
    scope: NuggetScope
    filter: FieldFilter = Field(default_factory=FieldFilter)
    low_crowding_only: bool = False
    #: ``None`` uses ``learning.yaml``'s default (see ``fs.default_crowding_threshold``).
    crowding_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class FieldSearchRow(Out):
    field_id: str
    dataset_id: str | None
    description: str | None
    field_type: str
    crowding: float | None
    dataset_warning: str | None


class NuggetIn(Out):
    name: str = Field(min_length=1, max_length=120)
    scope: NuggetScope
    field_ids: list[str] = Field(min_length=2, max_length=20)
    #: field_id -> override; fields left out get :func:`default_treatment`.
    data_treatment: dict[str, str] = Field(default_factory=dict)
    rationale: str = Field(min_length=1, max_length=4000)
    claim: str = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(default_factory=list)
    #: Save anyway, over a duplicate-pairing rejection — an explicit, logged override.
    allow_duplicates: bool = False
    note: str = Field(default="", max_length=500)


class NuggetOut(Out):
    id: int
    nugget_id: str
    name: str
    scope: NuggetScope
    field_ids: list[str]
    data_treatment: dict[str, str]
    rationale: str
    claim: str
    tags: list[str]
    version: int
    created_at: datetime
    updated_at: datetime


class NuggetDetail(NuggetOut):
    fields: list[FieldProfileOut]
    #: Each field's resolved data-treatment expression (override or default), in field order.
    resolved_treatment: dict[str, str]


class NuggetVersionOut(Out):
    version: int
    snapshot: dict[str, Any]
    note: str
    created_at: datetime


class Deleted(Out):
    deleted: bool


class CorrelationStageOut(Out):
    label: str
    value: float | None
    against: str | None
    caption: str


class NuggetCorrelationOut(Out):
    self_prescreen: CorrelationStageOut
    #: ``None`` until ``fetch_prod=true`` asks BRAIN for its verdict.
    prod_final: CorrelationStageOut | None


# --- helpers ------------------------------------------------------------------


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") or "nugget"
    return base if base[0].isalpha() else f"n_{base}"


def _out(row: Nugget) -> NuggetOut:
    return NuggetOut(
        id=row.id,
        nugget_id=row.nugget_id,
        name=row.name,
        scope=NuggetScope(
            instrument_type=row.instrument_type,
            region=row.region,
            delay=row.delay,
            universe=row.universe,
        ),
        field_ids=list(row.field_ids),
        data_treatment=dict(row.data_treatment),
        rationale=row.rationale,
        claim=row.claim,
        tags=list(row.tags),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _profiles(
    state: State, scope: NuggetScope, field_ids: list[str]
) -> tuple[dict[str, FieldProfile], list[str]]:
    ids = list(dict.fromkeys(f.strip() for f in field_ids if f.strip()))
    rows = await state.queries.fields_by_id(scope.to_tuple4(), ids)
    found = {str(r["field_id"]): profile_field(r) for r in rows}
    missing = [f for f in ids if f not in found]
    return found, missing


async def _check_scope(state: State, scope: NuggetScope) -> None:
    schema = await state.metadata.cached_settings_schema()
    if schema is None:
        raise refuse(
            503,
            "no_schema",
            "Sign in to BRAIN first: Forge reads which markets your account can run from it.",
        )
    problems = check_scope(schema, scope.to_scope_model())
    if problems:
        raise refuse(422, "scope_unavailable", " ".join(problems))


async def _load(state: State, nugget_id: str) -> Nugget:
    async with state.db.session() as session:
        row = (await session.scalars(select(Nugget).where(Nugget.nugget_id == nugget_id))).first()
    if row is None:
        raise refuse(404, "not_found", f"No nugget {nugget_id!r}.")
    return row


# --- reading fields, free and offline ------------------------------------------


@router.post("/profile")
async def profile_fields(body: ProfileRequest, state: State) -> ProfileResponse:
    """What each field looks like, read without simulating: type, cadence, category."""
    profiles, missing = await _profiles(state, body.scope, body.field_ids)
    return ProfileResponse(fields=[_profile_out(p) for p in profiles.values()], missing=missing)


@router.post("/suggest")
async def suggest(body: ProfileRequest, state: State) -> SuggestResponse:
    """Which pairs among these fields are structurally sane to write a nugget rationale for.

    Advisory only: a valid pairing still needs the user's own rationale and claim, and an
    invalid one can still be saved with ``allow_duplicates`` if the rejection is wrong.
    """
    profiles, missing = await _profiles(state, body.scope, body.field_ids)
    suggestions = suggest_pairings(list(profiles.values()))
    return SuggestResponse(
        fields=[_profile_out(p) for p in profiles.values()],
        missing=missing,
        suggestions=[_suggestion_out(s) for s in suggestions],
    )


@router.post("/field-search")
async def field_search(body: FieldSearchRequest, state: State) -> list[FieldSearchRow]:
    """Data Explorer fields for building a nugget, each with its crowding and a dataset
    heavy-use warning; ``low_crowding_only`` narrows to fields under the threshold."""
    results = await fs.search_fields_for_nugget(
        state.catalog,
        body.scope.to_tuple4(),
        body.filter,
        low_crowding_only=body.low_crowding_only,
        crowding_threshold=body.crowding_threshold,
    )
    return [
        FieldSearchRow(
            field_id=r.field_id,
            dataset_id=r.dataset_id,
            description=r.description,
            field_type=r.field_type,
            crowding=r.crowding,
            dataset_warning=r.dataset_warning,
        )
        for r in results
    ]


# --- CRUD + versioning ----------------------------------------------------------


@router.get("")
async def list_nuggets(
    state: State,
    region: Annotated[str | None, Query()] = None,
    universe: Annotated[str | None, Query()] = None,
) -> list[NuggetOut]:
    """Every nugget, newest first; narrowed to a scope when one is given."""
    query = select(Nugget).order_by(Nugget.updated_at.desc(), Nugget.id.desc())
    if region is not None:
        query = query.where(Nugget.region == region)
    if universe is not None:
        query = query.where(Nugget.universe == universe)
    async with state.db.session() as s:
        return [_out(r) for r in (await s.scalars(query)).all()]


@router.post("")
async def create_nugget(body: NuggetIn, state: State) -> NuggetOut:
    """Save a new nugget. Refused if a pair of its fields looks like one measure twice, unless
    ``allow_duplicates`` says to save it anyway."""
    await _check_scope(state, body.scope)
    profiles, missing = await _profiles(state, body.scope, body.field_ids)
    draft = NuggetDraft(
        name=body.name,
        field_ids=body.field_ids,
        data_treatment=body.data_treatment,
        rationale=body.rationale,
        claim=body.claim,
        tags=body.tags,
    )
    problems = validate_draft(draft, profiles, allow_duplicates=body.allow_duplicates)
    if missing:
        problems.append(f"Not in the catalog for this scope: {', '.join(missing)}.")
    if problems:
        raise refuse(422, "invalid_nugget", " ".join(problems))

    nugget_id = _slug(body.name)
    async with state.db.session() as session:
        clash = (await session.scalars(select(Nugget).where(Nugget.nugget_id == nugget_id))).first()
        if clash is not None:
            raise refuse(409, "name_taken", f"A nugget called {nugget_id!r} already exists.")
        ids = list(dict.fromkeys(f.strip() for f in body.field_ids if f.strip()))
        row = Nugget(
            nugget_id=nugget_id,
            name=body.name,
            instrument_type=body.scope.instrument_type,
            region=body.scope.region,
            delay=body.scope.delay,
            universe=body.scope.universe,
            field_ids=ids,
            data_treatment=dict(body.data_treatment),
            rationale=body.rationale,
            claim=body.claim,
            tags=list(dict.fromkeys(body.tags)),
            version=1,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        session.add(
            NuggetVersion(
                nugget_id=row.nugget_id,
                version=1,
                snapshot=snapshot(row),
                note=body.note or "Created",
            )
        )
    return _out(row)


@router.get("/{nugget_id}")
async def nugget_detail(nugget_id: str, state: State) -> NuggetDetail:
    row = await _load(state, nugget_id)
    scope = NuggetScope(
        instrument_type=row.instrument_type,
        region=row.region,
        delay=row.delay,
        universe=row.universe,
    )
    profiles, _missing = await _profiles(state, scope, list(row.field_ids))
    return NuggetDetail(
        **_out(row).model_dump(),
        fields=[_profile_out(profiles[f]) for f in row.field_ids if f in profiles],
        resolved_treatment={f: resolved_treatment(f, row.data_treatment) for f in row.field_ids},
    )


@router.put("/{nugget_id}")
async def update_nugget(nugget_id: str, body: NuggetIn, state: State) -> NuggetOut:
    """Save an edit as a new version. The scope and nugget id do not change here — clone it
    (save as a new nugget) to move it to a different market."""
    row = await _load(state, nugget_id)
    profiles, missing = await _profiles(
        state,
        NuggetScope(
            instrument_type=row.instrument_type,
            region=row.region,
            delay=row.delay,
            universe=row.universe,
        ),
        body.field_ids,
    )
    draft = NuggetDraft(
        name=body.name,
        field_ids=body.field_ids,
        data_treatment=body.data_treatment,
        rationale=body.rationale,
        claim=body.claim,
        tags=body.tags,
    )
    problems = validate_draft(draft, profiles, allow_duplicates=body.allow_duplicates)
    if missing:
        problems.append(f"Not in the catalog for this scope: {', '.join(missing)}.")
    if problems:
        raise refuse(422, "invalid_nugget", " ".join(problems))

    async with state.db.session() as session:
        row = (await session.scalars(select(Nugget).where(Nugget.nugget_id == nugget_id))).first()
        if row is None:
            raise refuse(404, "not_found", f"No nugget {nugget_id!r}.")
        ids = list(dict.fromkeys(f.strip() for f in body.field_ids if f.strip()))
        row.name = body.name
        row.field_ids = ids
        row.data_treatment = dict(body.data_treatment)
        row.rationale = body.rationale
        row.claim = body.claim
        row.tags = list(dict.fromkeys(body.tags))
        row.version += 1
        session.add(row)
        await session.flush()
        await session.refresh(row)
        session.add(
            NuggetVersion(
                nugget_id=row.nugget_id, version=row.version, snapshot=snapshot(row), note=body.note
            )
        )
    return _out(row)


@router.get("/{nugget_id}/versions")
async def nugget_versions(nugget_id: str, state: State) -> list[NuggetVersionOut]:
    async with state.db.session() as session:
        rows = (
            await session.scalars(
                select(NuggetVersion)
                .where(NuggetVersion.nugget_id == nugget_id)
                .order_by(NuggetVersion.version.desc())
            )
        ).all()
    return [
        NuggetVersionOut(
            version=r.version, snapshot=r.snapshot, note=r.note, created_at=r.created_at
        )
        for r in rows
    ]


@router.delete("/{nugget_id}")
async def delete_nugget(nugget_id: str, state: State) -> Deleted:
    async with state.db.session() as session:
        row = (await session.scalars(select(Nugget).where(Nugget.nugget_id == nugget_id))).first()
        if row is None:
            raise refuse(404, "not_found", f"No nugget {nugget_id!r}.")
        await session.delete(row)
        for version in (
            await session.scalars(select(NuggetVersion).where(NuggetVersion.nugget_id == nugget_id))
        ).all():
            await session.delete(version)
    return Deleted(deleted=True)


# --- correlation, two stages, never merged -------------------------------------


@router.get("/{nugget_id}/correlation/{alpha_id}")
async def nugget_correlation(
    nugget_id: str,
    alpha_id: str,
    state: State,
    fetch_prod: Annotated[bool, Query()] = False,
    prod_kind: Annotated[str, Query()] = "self",
) -> NuggetCorrelationOut:
    """The two correlation stages for an alpha this nugget produced: the free local estimate
    against your synced pool, and — only when ``fetch_prod`` asks for it — BRAIN's own
    verdict. The two are never averaged into one figure."""
    await _load(state, nugget_id)  # 404s on an unknown nugget before spending anything

    pnl_rows = await state.alphas.pnl_series(alpha_id)
    if not pnl_rows:
        raise refuse(404, "no_pnl", f"No synced PnL for alpha {alpha_id!r} yet.")
    await touch_pnl(state.db, [alpha_id])
    candidate = an.to_series([(r["date"], r["pnl"]) for r in pnl_rows])

    pool_rows = await state.alphas.submitted_members()
    pool_ids = [str(r["alpha_id"]) for r in pool_rows if str(r["alpha_id"]) != alpha_id]
    pool_pnl = await state.alphas.daily_pnl(pool_ids) if pool_ids else {}
    pool_series = {aid: pd.Series(rows).sort_index() for aid, rows in pool_pnl.items() if rows}
    self_stage = corr.self_prescreen(candidate, pool_series)

    prod_stage = None
    if fetch_prod:
        prod = await corr.prod_final(state.endpoints, alpha_id, kind=prod_kind)
        prod_stage = CorrelationStageOut(
            label=prod.label, value=prod.value, against=prod.against, caption=prod.caption
        )

    return NuggetCorrelationOut(
        self_prescreen=CorrelationStageOut(
            label=self_stage.label,
            value=self_stage.value,
            against=self_stage.against,
            caption=self_stage.caption,
        ),
        prod_final=prod_stage,
    )
