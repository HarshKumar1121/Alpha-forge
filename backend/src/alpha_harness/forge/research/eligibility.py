"""Which alpha types one RENDERED alpha qualifies for, and why it does not.

The Template Library answers "could this template ever fit Power Pool?" from a placeholder
fill (:func:`~alpha_harness.forge.research.templates.fits`). That is a question about the
template. This module answers the question that decides whether a simulation is worth
spending: *this expression, on these real fields, in this scope* — is it a Power Pool alpha?

The rules come from the alpha type YAML (CLAUDE.md rule 7) through
:func:`alpha_harness.forge.alpha_types.lint`, plus two BRAIN facts that a field catalog alone
cannot express:

* **Grouping fields are not data.** ``industry``, ``country``, ``split``… never count toward
  Power Pool's three fields or ATOM's single dataset. :class:`~alpha_harness.forge.expr.ExprInfo`
  already keeps them apart, so the lint never sees them as fields.
* **Some operators read pv1 whatever you pass them.** ``inst_pnl`` and ``convert``
  (:data:`~alpha_harness.brain.platform.PV1_OPERATORS`) reach into price/volume data on their
  own, so BRAIN counts the alpha as using pv1 even when no pv1 field is written down. This
  module adds that usage before the dataset and field limits are checked.

The second rule is the conservative reading: an alpha this module calls ineligible might be
accepted by BRAIN, but one it calls eligible should not be refused for a reason that was
knowable offline. BRAIN's own ``/check`` remains the only authority (CLAUDE.md: submittable
means BRAIN says so) — this is here to stop a simulation being spent on a certain refusal.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from ...brain.platform import PV1_OPERATORS
from ..alpha_types import lint
from ..expr import ParseError, analyse
from .templates import PV_DATASET

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..alpha_types import AlphaType


@dataclass(frozen=True, slots=True)
class Eligibility:
    """One alpha judged against one alpha type."""

    alpha_type: str
    ok: bool
    #: Why it does NOT qualify, in plain sentences. Empty when it does.
    reasons: tuple[str, ...] = ()
    #: Worth knowing, but not disqualifying.
    warnings: tuple[str, ...] = ()


def pv1_operators_used(expression: str) -> tuple[str, ...]:
    """The operators in this expression that read pv1 on their own."""
    try:
        info = analyse(expression)
    except ParseError:
        return ()
    return tuple(sorted(set(info.operators) & PV1_OPERATORS))


def datasets_used(
    expression: str, catalog: Mapping[str, Mapping[str, Any]]
) -> tuple[set[str], tuple[str, ...]]:
    """The datasets an alpha reads, and the pv1 operators that added pv1 to them.

    Grouping fields are not data, so they bring no dataset with them; a field the catalog
    does not describe cannot be attributed and is left out (the lint reports it separately).
    """
    try:
        info = analyse(expression)
    except ParseError:
        return set(), ()
    used = {
        str(catalog[f]["dataset"])
        for f in info.fields
        if f in catalog and catalog[f].get("dataset")
    }
    pv1 = pv1_operators_used(expression)
    if pv1:
        used.add(PV_DATASET)
    return used, pv1


def data_field_count(expression: str) -> int:
    """How many data fields BRAIN counts: the named ones, plus pv1 when an operator reads it.

    A pv1 operator is counted as one field however many times it appears, and is not counted
    twice when the expression already names a pv1 field — see the note above on why this
    errs toward "not eligible".
    """
    try:
        info = analyse(expression)
    except ParseError:
        return 0
    return len(info.fields) + (1 if pv1_operators_used(expression) else 0)


def _pv1_note(operators: tuple[str, ...]) -> str:
    joined = ", ".join(operators)
    return f"{joined} reads pv1 whatever it is given, so BRAIN counts this alpha as using pv1"


def judge(
    expression: str,
    alpha_type: AlphaType,
    *,
    catalog: Mapping[str, Mapping[str, Any]],
    target_dataset: str | None = None,
    operators: set[str] | None = None,
) -> Eligibility:
    """Judge one rendered alpha against one alpha type.

    ``catalog`` maps every field the expression may name to ``{dataset, type, coverage}``;
    ``target_dataset`` is the dataset the research is about, for the types that require a
    field from it.
    """
    plain = dict(catalog)
    result = lint(
        expression,
        alpha_type,
        catalog=plain,  # pyright: ignore[reportArgumentType] - lint takes the same shape, mutably typed
        operators=operators,
        target_dataset=target_dataset,
    )
    reasons = list(result.errors)
    warnings = list(result.warnings)

    rules = alpha_type.lint_rules
    used, pv1 = datasets_used(expression, catalog)
    if pv1:
        note = _pv1_note(pv1)
        if rules.get("single_dataset") and len(used) > 1:
            others = sorted(used - {PV_DATASET})
            reasons.append(
                f"{note}, so its fields span pv1 and {', '.join(others)} — "
                f"{alpha_type.name} takes one dataset"
            )
        # The lint counted the fields the expression names; pv1 adds one it does not.
        written = len(analyse(expression).fields)
        counted = written + 1
        if (mx := rules.get("max_fields")) is not None:
            if written <= int(mx) < counted:
                reasons.append(
                    f"{counted} data fields > {mx} allowed for {alpha_type.name}: {note}"
                )
            elif counted <= int(mx):
                warnings.append(f"{note}; that is {counted} of {mx} data fields")

    return Eligibility(
        alpha_type=alpha_type.name,
        ok=not reasons,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


def judge_all(
    expression: str,
    alpha_types: Mapping[str, AlphaType],
    *,
    catalog: Mapping[str, Mapping[str, Any]],
    target_dataset: str | None = None,
    operators: set[str] | None = None,
) -> dict[str, Eligibility]:
    """Every alpha type this alpha fits, and why it misses the others."""
    return {
        name: judge(
            expression,
            at,
            catalog=catalog,
            target_dataset=target_dataset,
            operators=operators,
        )
        for name, at in alpha_types.items()
    }


#: FITS: clean pass. FITS_BUT_WEAK: passes the lint but is a poor fit in practice — a note
#: a researcher should read before spending a simulation on it, not a reason to refuse it.
#: DOESNT_FIT: :class:`Eligibility.ok` is false.
Verdict = Literal["FITS", "FITS_BUT_WEAK", "DOESNT_FIT"]


def quality_note(
    expression: str,
    alpha_type: AlphaType,
    *,
    catalog: Mapping[str, Mapping[str, Any]],
) -> str | None:
    """Why a *passing* alpha is a weak choice for ``alpha_type``, or ``None`` when it isn't.

    BRAIN's own limits (field/operator counts, single-dataset) say what an alpha type will
    RUN. They say nothing about what makes a good one — a two-dataset expression can sit well
    under Power Pool's three-field cap and still not be the clean single-dataset residual the
    type is meant for. These heuristics are a second, softer layer of judgment on top of
    :func:`judge`, not a platform rule (CLAUDE.md rule 7 covers the hard rules; this is
    research taste), so they live here as Python rather than in the alpha type YAML.
    """
    rules = alpha_type.lint_rules
    used, _pv1 = datasets_used(expression, catalog)

    # A type that wants most of its fields from one dataset (Power Pool: "a clean residual on
    # the target dataset") but doesn't outright forbid a second one — crossing datasets is
    # legal, but it stops being the archetype the type is for.
    wants_single_source = rules.get("min_fields_from_target_dataset") and not rules.get(
        "single_dataset"
    )
    if wants_single_source and len(used) > 1:
        others = sorted(used)
        return (
            f"reads {len(used)} datasets ({', '.join(others)}) — fits the rule, but "
            f"{alpha_type.name} alphas read best as one dataset's own residual"
        )

    # Using every field the type allows leaves no room to refine without hitting the limit.
    max_fields = rules.get("max_fields")
    if max_fields is not None and data_field_count(expression) == int(max_fields):
        return (
            f"uses all {max_fields} fields {alpha_type.name} allows — no slack left to "
            "refine this idea without dropping one"
        )

    return None


def judge_quality(
    expression: str,
    alpha_type: AlphaType,
    *,
    catalog: Mapping[str, Mapping[str, Any]],
    target_dataset: str | None = None,
    operators: set[str] | None = None,
) -> tuple[Verdict, str | None]:
    """:func:`judge`, read down to one 3-way verdict and its one-line reason."""
    verdict = judge(
        expression,
        alpha_type,
        catalog=catalog,
        target_dataset=target_dataset,
        operators=operators,
    )
    if not verdict.ok:
        return "DOESNT_FIT", verdict.reasons[0] if verdict.reasons else "does not fit"
    note = quality_note(expression, alpha_type, catalog=catalog)
    return ("FITS_BUT_WEAK", note) if note else ("FITS", None)


def judge_quality_all(
    expression: str,
    alpha_types: Mapping[str, AlphaType],
    *,
    catalog: Mapping[str, Mapping[str, Any]],
    target_dataset: str | None = None,
    operators: set[str] | None = None,
) -> dict[str, tuple[Verdict, str | None]]:
    """Every alpha type's 3-way verdict for this alpha, keyed by type name."""
    return {
        name: judge_quality(
            expression, at, catalog=catalog, target_dataset=target_dataset, operators=operators
        )
        for name, at in alpha_types.items()
    }


@dataclass(frozen=True, slots=True)
class EligibilitySummary:
    """A :func:`judge_all` result condensed for display: one flag when everything fits, so
    a screen shows "fits all 12 types" instead of twelve identical green badges, and the
    failing types (with their reasons) only when there is something to explain."""

    fits_all: bool
    #: The types it fits, regardless of ``fits_all`` — the planner still needs the names.
    fits: tuple[str, ...]
    #: Only the types it does NOT fit, each with why.
    failing: dict[str, Eligibility]


def summarize(verdicts: Mapping[str, Eligibility]) -> EligibilitySummary:
    fits = tuple(sorted(name for name, v in verdicts.items() if v.ok))
    failing = {name: v for name, v in verdicts.items() if not v.ok}
    return EligibilitySummary(fits_all=not failing, fits=fits, failing=failing)
