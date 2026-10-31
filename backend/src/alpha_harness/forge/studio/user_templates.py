"""Templates of your own: validated, versioned, exported, and merged into the library.

A built-in template is a file in ``config/forge/templates`` and stays read-only — it is part
of the repository, and an edit that only existed in one person's database would be lost on the
next clone and invisible in a diff. Cloning one copies its YAML into the database under a new
id, where it can be edited freely.

Every save validates the YAML the same way the loader validates a file
(:func:`~alpha_harness.forge.research.templates.validate` and :func:`parse_template`), so a
template that cannot render is refused at the point it is written rather than at the point it
would have spent a simulation.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from ..research.templates import Template, library, parse_template, validate

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

#: What a single-template YAML document looks like, as the editor shows it.
TOP_KEY = "templates"


class TemplateTextError(ValueError):
    """The YAML a user wrote is not a usable template. Carries every problem found."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(problems))


@dataclass(frozen=True, slots=True)
class Parsed:
    """A user's YAML, read as one template."""

    template: Template
    raw: dict[str, Any]


def _entry(text: str) -> dict[str, Any]:
    """The single template entry in a document, written either bare or under ``templates:``."""
    try:
        data: object = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TemplateTextError([f"not valid YAML: {exc}"]) from exc
    if isinstance(data, dict) and TOP_KEY in data:
        entries: object = data[TOP_KEY]
        if not isinstance(entries, list) or len(entries) != 1:
            raise TemplateTextError([f"{TOP_KEY}: write exactly one template here"])
        data = entries[0]
    if not isinstance(data, dict):
        raise TemplateTextError(["a template is a mapping, e.g. `id: my_idea`"])
    return {str(k): v for k, v in data.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def parse(text: str) -> Parsed:
    """Read one template from the editor's text, or raise with every problem in it."""
    raw = _entry(text)
    if problems := validate(raw):
        raise TemplateTextError(problems)
    try:
        return Parsed(parse_template(raw), raw)
    except ValueError as exc:
        raise TemplateTextError([str(exc)]) from exc


def problems(text: str) -> list[str]:
    """Everything wrong with this text, for the editor's live lint. Empty means it is fine."""
    try:
        parse(text)
    except TemplateTextError as exc:
        return exc.problems
    return []


def to_yaml(t: Template) -> str:
    """A template as the library file spells it — what cloning starts from, and what export
    writes out. Round-trips: :func:`parse` reads this back."""
    slots: dict[str, Any] = {}
    for name, slot in t.slots.items():
        values = getattr(slot, "values", None)
        if values is not None:
            kind = "window" if all(isinstance(v, int) for v in values) else "group"
            slots[name] = {"kind": kind, "values": list(values)}
            continue
        entry: dict[str, Any] = {"kind": "field"}
        if slot.archetype:  # pyright: ignore[reportAttributeAccessIssue] - a FieldSlot by elimination
            entry["archetype"] = list(slot.archetype)  # pyright: ignore[reportAttributeAccessIssue]
        for key in ("type", "same_dataset_as", "different_dataset_from", "hint"):
            value = getattr(slot, key, None)
            if value:
                entry[key] = value
        if (agg := getattr(slot, "aggregate", "vec_avg")) != "vec_avg":
            entry["aggregate"] = agg
        slots[name] = entry

    document: dict[str, Any] = {
        "id": t.id,
        "name": t.name,
        "category": t.category,
        "complexity": t.complexity,
        "angle": t.angle,
        "slots": slots,
        "expr": t.expr,
        "rationale": t.rationale,
    }
    if t.only_if:
        document["only_if"] = t.only_if
    return yaml.safe_dump({TOP_KEY: [document]}, sort_keys=False, allow_unicode=True, width=96)


def clone_text(t: Template, new_id: str, name: str = "") -> str:
    """A built-in's YAML under a new id, ready to edit."""
    text = to_yaml(t)
    raw = _entry(text)
    raw["id"] = new_id
    raw["name"] = name or f"{t.name} (copy)"
    return yaml.safe_dump({TOP_KEY: [raw]}, sort_keys=False, allow_unicode=True, width=96)


def builtin_ids() -> set[str]:
    """Ids the repository's own files already use."""
    return {t.id for t in library()}


def merged(user: Iterable[tuple[str, str]]) -> tuple[list[Template], list[str]]:
    """The built-ins plus the user's templates, and a problem line for each unreadable one.

    A user template that no longer parses (an operator renamed, a slot mistyped) is reported
    and left out rather than taking the library down with it — the same rule the file loader
    follows.
    """
    out = list(library())
    errors: list[str] = []
    for template_id, text in user:
        try:
            out.append(parse(text).template)
        except TemplateTextError as exc:
            errors.append(f"{template_id}: {exc}")
    return out, errors
