"""A first draft of the description BRAIN wants before it takes a Power Pool Alpha.

BRAIN's template is three headings — Idea, Rationale for data used, Rationale for operators
used — each wanting real text. :mod:`alpha_harness.tools.description` owns that format: it
builds the prompt for a model, reads a model's answer back, and :func:`~.short` says which
parts are too thin. This module writes a draft with no model at all, from what the Forge
already knows: the template's own rationale, BRAIN's description of each field, and BRAIN's
description of each operator.

It is a *draft*. It is shown in an editor, saved only when the user saves it, and never
submitted by any code path (CLAUDE.md rule 1) — Phase 10's agent may improve it, and the user
always has the last word. Where the draft is thin, it says so rather than padding: filler
sentences are what BRAIN's reviewers are reading past, and a short honest draft is easier to
finish than a long empty one.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...brain.platform import POWER_POOL_DESCRIPTION_HEADINGS
from ...labs.fastexpr import ParseError, parse
from ...tools.description import ARITHMETIC, operators_in, short
from ..expr import analyse

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .templates import Template


@dataclass(frozen=True, slots=True)
class Draft:
    """The three parts, the assembled text, and what still needs work."""

    idea: str
    data: str
    operators: str
    text: str
    #: Headings whose text is under BRAIN's length, by name. Empty when the draft is enough.
    thin: tuple[str, ...]


def compose(idea: str, data: str, operators: str) -> str:
    """The three parts in BRAIN's template: one heading per line, text after each."""
    parts = (idea, data, operators)
    return "\n".join(
        f"{heading}: {' '.join(part.split())}"
        for heading, part in zip(POWER_POOL_DESCRIPTION_HEADINGS, parts, strict=True)
    )


def _sentence(text: str) -> str:
    """One tidy sentence: collapsed whitespace, ending in a full stop."""
    clean = " ".join(str(text or "").split()).rstrip(".")
    return f"{clean}." if clean else ""


def _field_line(field_id: str, meta: Mapping[str, Any]) -> str:
    """One field as the description names it: what it is, and where it comes from."""
    described = _sentence(meta.get("description") or "")
    dataset = meta.get("dataset") or ""
    where = f" (dataset {dataset})" if dataset else ""
    body = described or "No description is published for it."
    return f"{field_id}{where}: {body}"


def _operator_line(name: str, meta: Mapping[str, Any] | None) -> str:
    described = _sentence((meta or {}).get("description") or "")
    return f"{name}: {described}" if described else f"{name}."


def draft(
    template: Template | None,
    expression: str,
    *,
    fields: Mapping[str, Mapping[str, Any]],
    operators: Mapping[str, Mapping[str, Any]] | None = None,
    idea: str = "",
) -> Draft:
    """Draft a description for one rendered alpha.

    ``fields`` maps the field ids the expression uses to their catalog row (``description``,
    ``dataset``); ``operators`` maps an operator name to BRAIN's entry for it. ``idea``
    overrides the template's rationale, for an alpha written by hand.
    """
    used = analyse(expression)
    rationale = idea or (template.rationale if template else "")

    idea_text = _sentence(rationale) or (
        "This alpha ranks stocks by the signal below and holds the extremes."
    )
    if template is not None and template.angle:
        idea_text += (
            f" It expresses the {template.angle} angle: what the data says now against what it"
            " said before, so the position follows the information rather than the price."
        )

    lines = [_field_line(f, fields.get(f, {})) for f in used.fields]
    if used.grouping:
        lines.append(
            f"Grouping fields ({', '.join(used.grouping)}) set the peer set and are not data"
            " BRAIN counts toward the field limit."
        )
    data_text = " ".join(lines) or "No data fields are described for this alpha."

    try:
        names = operators_in(parse(expression))
    except ParseError:
        names = list(used.operators)
    known = operators or {}
    # Arithmetic needs no explaining, and BRAIN's reviewers do not want it explained.
    interesting = [n for n in names if (known.get(n) or {}).get("category") != ARITHMETIC]
    operator_text = (
        " ".join(_operator_line(n, known.get(n)) for n in interesting)
        or "The alpha uses arithmetic only."
    )

    text = compose(idea_text, data_text, operator_text)
    return Draft(
        idea=idea_text,
        data=data_text,
        operators=operator_text,
        text=text,
        thin=tuple(short(text)),
    )
