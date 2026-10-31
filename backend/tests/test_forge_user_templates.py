"""Templates of your own: validation, the YAML round-trip, cloning, and merging."""

import pytest
import yaml

from alpha_harness.forge.research.templates import library
from alpha_harness.forge.studio.user_templates import (
    TemplateTextError,
    builtin_ids,
    clone_text,
    merged,
    parse,
    problems,
    to_yaml,
)

GOOD = """
templates:
  - id: my_revision
    name: My revision idea
    category: analyst/revision
    complexity: simple
    angle: change
    slots:
      X: {kind: field, archetype: [slow, medium]}
      W: {kind: window, values: [21, 63]}
      G: {kind: group, values: [industry]}
    expr: group_rank(ts_delta($X, $W), $G)
    rationale: Estimates are revised in steps, so a fast riser keeps rising.
"""


def test_a_good_template_parses() -> None:
    parsed = parse(GOOD)
    assert parsed.template.id == "my_revision"
    assert parsed.template.field_slots == ["X"]
    assert problems(GOOD) == []


def test_a_bare_document_without_the_templates_key_also_parses() -> None:
    bare = yaml.safe_dump(yaml.safe_load(GOOD)["templates"][0])
    assert parse(bare).template.id == "my_revision"


def test_broken_yaml_is_reported_not_raised_as_a_crash() -> None:
    found = problems("templates: [:")
    assert found
    assert "not valid YAML" in found[0]


def test_every_schema_problem_is_listed_at_once() -> None:
    found = problems(
        """
        id: bad
        name: Bad
        category: x
        complexity: enormous
        slots:
          X: {kind: field, archtype: [slow]}
        expr: rank($X) + $MISSING
        rationale: Something.
        """
    )
    assert any("complexity" in p for p in found)
    assert any("archtype" in p for p in found)
    assert any("$MISSING" in p for p in found)


def test_a_template_that_cannot_render_is_refused() -> None:
    with pytest.raises(TemplateTextError):
        parse(
            """
            id: broken
            name: Broken
            category: x
            complexity: simple
            slots:
              X: {kind: field}
            expr: rank($X
            rationale: Unbalanced brackets.
            """
        )


def test_the_engine_never_lets_a_template_clean_its_own_slot() -> None:
    found = problems(
        """
        id: double
        name: Double clean
        category: x
        complexity: simple
        slots:
          X: {kind: field}
        expr: rank(ts_backfill($X, 63))
        rationale: The engine already backfills every field.
        """
    )
    assert any("wraps a slot" in p for p in found)


def test_yaml_export_round_trips() -> None:
    """What export writes, the editor can read back as the same template."""
    original = parse(GOOD).template
    again = parse(to_yaml(original)).template
    assert again.id == original.id
    assert again.expr == original.expr
    assert again.rationale == original.rationale
    assert again.angle == original.angle
    assert again.complexity == original.complexity
    assert {k: type(v) for k, v in again.slots.items()} == {
        k: type(v) for k, v in original.slots.items()
    }
    assert again.field_slot("X").archetype == original.field_slot("X").archetype


@pytest.mark.parametrize("builtin", [t.id for t in library()][:6])
def test_every_builtin_round_trips_through_export(builtin: str) -> None:
    original = next(t for t in library() if t.id == builtin)
    again = parse(to_yaml(original)).template
    assert again.expr == original.expr
    assert again.slots.keys() == original.slots.keys()
    assert again.needs == original.needs


def test_cloning_a_builtin_gives_it_a_new_id() -> None:
    original = library()[0]
    text = clone_text(original, "my_copy")
    clone = parse(text).template
    assert clone.id == "my_copy"
    assert clone.expr == original.expr
    assert "copy" in clone.name.lower()
    assert original.id in builtin_ids()
    assert "my_copy" not in builtin_ids()


def test_a_cloned_name_can_be_chosen() -> None:
    original = library()[0]
    clone = parse(clone_text(original, "my_copy", "Momentum, slower")).template
    assert clone.name == "Momentum, slower"


def test_merging_adds_user_templates_to_the_builtins() -> None:
    templates, errors = merged([("my_revision", GOOD)])
    assert errors == []
    assert len(templates) == len(library()) + 1
    assert any(t.id == "my_revision" for t in templates)


def test_one_broken_user_template_does_not_take_the_library_down() -> None:
    templates, errors = merged([("my_revision", GOOD), ("broken", "id: [")])
    assert len(templates) == len(library()) + 1
    assert len(errors) == 1
    assert errors[0].startswith("broken:")
