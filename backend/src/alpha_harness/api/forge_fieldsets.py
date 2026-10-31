"""``/api/forge/fieldsets``: named field selections, each with the scope it was picked in.

Create, read, update, delete. Nothing here reaches BRAIN except through the cached settings
schema, which decides whether a scope is one the account can run.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Query
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..catalog.queries import FieldFilter
from ..forge.data.fieldsets import MAX_FIELDS, resolve, saved_filter
from ..forge.data.models import DYNAMIC, MANUAL, FieldSet
from ..forge.data.scope import Scope, check_scope
from ..schemas import Out
from .catalog import DataFieldRow
from .deps import State, refuse

router = APIRouter(prefix="/api/forge/fieldsets", tags=["forge-fieldsets"])


class FieldSetScope(Out):
    instrument_type: str = "EQUITY"
    region: str = Field(min_length=1, max_length=16)
    delay: int = Field(ge=0, le=1)
    universe: str = Field(min_length=1, max_length=32)

    def scope(self) -> Scope:
        return Scope(
            instrument_type=self.instrument_type,
            region=self.region,
            delay=self.delay,
            universe=self.universe,
        )


class FieldSetIn(Out):
    name: str = Field(min_length=1, max_length=80)
    scope: FieldSetScope
    kind: Literal["manual", "dynamic"] = MANUAL
    #: The picked fields, for a manual set.
    field_ids: list[str] = Field(default_factory=list, max_length=MAX_FIELDS)
    #: The Data Explorer filter, for a dynamic set. Paging is dropped.
    filter: FieldFilter | None = None
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("A field set needs a name.")
        if self.kind == MANUAL:
            self.field_ids = list(dict.fromkeys(i.strip() for i in self.field_ids if i.strip()))
            self.filter = None
        else:
            self.field_ids = []
        return self


class FieldSetOut(Out):
    id: int
    name: str
    scope: FieldSetScope
    kind: str
    field_ids: list[str]
    filter: dict[str, Any] | None
    notes: str
    created_at: datetime
    updated_at: datetime


class FieldSetDetail(FieldSetOut):
    #: Fields the set holds now, read from its scope (up to 500).
    fields: list[DataFieldRow]
    #: How many fields match; a dynamic set may match more than it returns.
    total: int
    #: Manual picks this scope's catalog does not hold (not synced, or BRAIN dropped them).
    missing: list[str]


class Deleted(Out):
    deleted: bool


def _out(row: FieldSet) -> FieldSetOut:
    return FieldSetOut(
        id=row.id,
        name=row.name,
        scope=FieldSetScope(
            instrument_type=row.instrument_type,
            region=row.region,
            delay=row.delay,
            universe=row.universe,
        ),
        kind=row.kind,
        field_ids=[str(i) for i in row.field_ids],
        filter=row.filter,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _check(body: FieldSetIn, state: State) -> None:
    """Refuse a scope the account cannot run, or a set with nothing in it."""
    if body.kind == MANUAL and not body.field_ids:
        raise refuse(422, "empty_selection", "Pick at least one field for a manual set.")
    if body.kind == DYNAMIC and body.filter is None:
        raise refuse(422, "empty_selection", "A dynamic set needs the filter it re-runs.")
    schema = await state.metadata.cached_settings_schema()
    if schema is None:
        raise refuse(
            503,
            "no_schema",
            "Sign in to BRAIN first: Forge reads which markets your account can run from it.",
        )
    problems = check_scope(schema, body.scope.scope())
    if problems:
        raise refuse(422, "scope_unavailable", " ".join(problems))


def _write(row: FieldSet, body: FieldSetIn) -> None:
    row.name = body.name
    row.instrument_type = body.scope.instrument_type
    row.region = body.scope.region
    row.delay = body.scope.delay
    row.universe = body.scope.universe
    row.kind = body.kind
    row.field_ids = body.field_ids
    row.filter = saved_filter(body.filter) if body.filter is not None else None
    row.notes = body.notes


def _name_taken(name: str) -> Exception:
    return refuse(409, "name_taken", f"A field set called {name!r} already exists.")


@router.get("")
async def list_fieldsets(
    state: State,
    region: Annotated[str | None, Query()] = None,
    delay: Annotated[int | None, Query(ge=0, le=1)] = None,
    universe: Annotated[str | None, Query()] = None,
) -> list[FieldSetOut]:
    """Every field set, newest first; narrowed to a scope when one is given."""
    query = select(FieldSet).order_by(FieldSet.updated_at.desc(), FieldSet.id.desc())
    if region is not None:
        query = query.where(FieldSet.region == region)
    if delay is not None:
        query = query.where(FieldSet.delay == delay)
    if universe is not None:
        query = query.where(FieldSet.universe == universe)
    async with state.db.session() as s:
        return [_out(r) for r in (await s.scalars(query)).all()]


@router.post("")
async def create_fieldset(body: FieldSetIn, state: State) -> FieldSetOut:
    await _check(body, state)
    row = FieldSet()
    _write(row, body)
    try:
        async with state.db.session() as s:
            s.add(row)
            await s.flush()
            await s.refresh(row)
    except IntegrityError:
        raise _name_taken(body.name) from None
    return _out(row)


async def _get(state: State, fieldset_id: int) -> FieldSet:
    async with state.db.session() as s:
        row = await s.get(FieldSet, fieldset_id)
    if row is None:
        raise refuse(404, "not_found", f"There is no field set {fieldset_id}.")
    return row


@router.get("/{fieldset_id}")
async def get_fieldset(fieldset_id: int, state: State) -> FieldSetDetail:
    """One set with the fields it holds now, read from its own scope."""
    row = await _get(state, fieldset_id)
    resolved = await resolve(row, state.queries)
    return FieldSetDetail(
        **_out(row).model_dump(),
        fields=[DataFieldRow.model_validate(r) for r in resolved["fields"]],
        total=resolved["total"],
        missing=resolved["missing"],
    )


@router.put("/{fieldset_id}")
async def update_fieldset(fieldset_id: int, body: FieldSetIn, state: State) -> FieldSetOut:
    await _check(body, state)
    try:
        async with state.db.session() as s:
            row = await s.get(FieldSet, fieldset_id)
            if row is None:
                raise refuse(404, "not_found", f"There is no field set {fieldset_id}.")
            _write(row, body)
            await s.flush()
            await s.refresh(row)
    except IntegrityError:
        raise _name_taken(body.name) from None
    return _out(row)


@router.delete("/{fieldset_id}")
async def delete_fieldset(fieldset_id: int, state: State) -> Deleted:
    async with state.db.session() as s:
        row = await s.get(FieldSet, fieldset_id)
        if row is None:
            raise refuse(404, "not_found", f"There is no field set {fieldset_id}.")
        await s.delete(row)
    return Deleted(deleted=True)
