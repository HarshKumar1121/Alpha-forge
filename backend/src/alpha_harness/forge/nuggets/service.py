"""Nugget domain logic: validating a draft, the default data-treatment, and the snapshot a
version row keeps — kept apart from the API's Pydantic wire shapes the same way
``forge/data/fieldsets.py`` keeps field-set logic apart from ``api/forge_fieldsets.py``.
"""

from dataclasses import dataclass
from typing import Any

from ..research.field_profile import FieldProfile, duplicate_reason
from ..research.templates import BACKFILL_DAYS

#: A nugget with more fields than this is no longer "a handful of related measures" — it is a
#: template's whole field set, and belongs in the Studio instead.
MAX_FIELDS = 8

NUGGET_ID_PATTERN = r"^[a-z][a-z0-9_]*$"


def default_treatment(field_id: str) -> str:
    """``ts_backfill(field, 63)``: carry the last known value forward across gaps, same
    default the template engine uses (:func:`..research.templates.clean`) — a nugget's
    treatment note exists to say where it *overrides* this, not to restate it everywhere."""
    return f"ts_backfill({field_id}, {BACKFILL_DAYS})"


def resolved_treatment(field_id: str, overrides: dict[str, str]) -> str:
    return overrides.get(field_id) or default_treatment(field_id)


@dataclass(frozen=True, slots=True)
class NuggetDraft:
    name: str
    field_ids: list[str]
    data_treatment: dict[str, str]
    rationale: str
    claim: str
    tags: list[str]


def validate_draft(
    draft: NuggetDraft, profiles: dict[str, FieldProfile], *, allow_duplicates: bool = False
) -> list[str]:
    """Every reason this draft cannot be saved, empty when it can.

    A nugget needs 2+ distinct, known fields, a rationale and a falsifiable claim actually
    written (not just whitespace), and — unless the caller explicitly overrides it — no pair
    that looks like the same underlying measure twice (CLAUDE.md rule 7 keeps the *thresholds*
    for this in YAML; what counts as a duplicate at all is the fixed, testable rule in
    ``field_profile.duplicate_reason``, not a tunable).
    """
    problems: list[str] = []
    ids = list(dict.fromkeys(f.strip() for f in draft.field_ids if f.strip()))
    if len(ids) < 2:
        problems.append("A nugget needs at least 2 distinct fields.")
    if len(ids) > MAX_FIELDS:
        problems.append(f"A nugget holds at most {MAX_FIELDS} fields; this has {len(ids)}.")
    missing = [f for f in ids if f not in profiles]
    if missing:
        problems.append(f"Not in the catalog for this scope: {', '.join(missing)}.")
    if not draft.rationale.strip():
        problems.append("Write the economic rationale: what each field measures, and why.")
    if not draft.claim.strip():
        problems.append("Write a falsifiable claim: what this predicts, and what would be wrong.")
    if not allow_duplicates:
        known = [profiles[f] for f in ids if f in profiles]
        for i, a in enumerate(known):
            for b in known[i + 1 :]:
                reason = duplicate_reason(a, b)
                if reason is not None:
                    problems.append(reason)
    return problems


def snapshot(row: Any) -> dict[str, Any]:
    """A plain-JSON copy of a nugget row, for a version row and for export."""
    return {
        "nugget_id": row.nugget_id,
        "name": row.name,
        "instrument_type": row.instrument_type,
        "region": row.region,
        "delay": row.delay,
        "universe": row.universe,
        "field_ids": list(row.field_ids),
        "data_treatment": dict(row.data_treatment),
        "rationale": row.rationale,
        "claim": row.claim,
        "tags": list(row.tags),
        "version": row.version,
    }
