"""``forge_pipelines._scoped_to``: a field set's own market overrides the alpha type's defaults."""

from alpha_harness.api import forge_pipelines
from alpha_harness.forge.data.models import FieldSet


def test_scoped_to_overrides_settings_scope_from_the_field_sets_own_row() -> None:
    """The alpha type's YAML settings describe decay/neutralization/truncation; the scope
    (region, universe, delay, instrument type) must come from the field set picked in the UI,
    never the type's own default market — otherwise a field set built in one market would be
    simulated under another market's settings."""
    from alpha_harness.forge.alpha_types import load_all as load_types

    regular = load_types()["regular"]
    assert regular.settings["region"] == "USA"
    assert regular.settings["universe"] == "TOP3000"

    row = FieldSet(
        name="eur",
        instrument_type="EQUITY",
        region="EUR",
        delay=0,
        universe="TOP1200",
        kind="manual",
        field_ids=["close"],
    )
    scoped = forge_pipelines._scoped_to(regular, row)
    assert scoped.settings["region"] == "EUR"
    assert scoped.settings["universe"] == "TOP1200"
    assert scoped.settings["delay"] == 0
    assert scoped.settings["instrumentType"] == "EQUITY"
    # Everything that is not scope is untouched.
    assert scoped.settings["decay"] == regular.settings["decay"]
    assert scoped.settings["neutralization"] == regular.settings["neutralization"]
    # The cached, shared type is never mutated by scoping a copy of it.
    assert regular.settings["region"] == "USA"
