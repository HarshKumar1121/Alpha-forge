"""``/api/forge/studio``: preview, eligibility, description drafts and your own templates.

Everything runs against throwaway databases seeded by ``forge_data_harness``; nothing reaches
BRAIN and nothing simulates (CLAUDE.md rules 1 and 4).
"""

from typing import TYPE_CHECKING, Any

import pytest
from forge_data_harness import CLIENT, make_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import httpx2

USA = {"region": "USA", "delay": 1, "universe": "TOP3000"}
ANALYST = ["anl4_eps_mean", "anl4_rev_mean", "anl4_eps_low"]

MY_TEMPLATE = """
templates:
  - id: my_revision
    name: My revision idea
    category: analyst/revision
    complexity: simple
    angle: change
    slots:
      X: {kind: field}
      W: {kind: window, values: [21, 63]}
    expr: rank(ts_delta($X, $W))
    rationale: Estimates are revised in steps, so a fast riser tends to keep rising.
"""


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx2.AsyncClient]:
    # With the grouping fields: a template that compares within an industry has to find one.
    async for c, _ in make_client(tmp_path, scope_fields=True):
        yield c


async def make_field_set(
    client: httpx2.AsyncClient, fields: list[str] = ANALYST, name: str = "analyst"
) -> int:
    res = await client.post(
        "/api/forge/fieldsets",
        json={"name": name, "scope": USA, "kind": "manual", "fieldIds": fields},
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    return int(res.json()["id"])


async def preview(client: httpx2.AsyncClient, **body: Any) -> dict[str, Any]:
    res = await client.post("/api/forge/studio/preview", json=body, headers=CLIENT)
    assert res.status_code == 200, res.text
    return res.json()


# --- binding a template to a field set ----------------------------------------


async def test_a_template_renders_real_alphas_from_a_field_set(
    client: httpx2.AsyncClient,
) -> None:
    body = await preview(
        client, templateId="analyst_revision", fieldSetId=await make_field_set(client)
    )
    assert body["blockers"] == []
    assert body["scope"] == "EQUITY/USA/D1/TOP3000"
    assert body["fieldSet"] == "analyst"
    assert body["rendered"]
    for alpha in body["rendered"]:
        assert alpha["expression"].startswith("rank(")
        # The field set's fields, cleaned by the engine, are what fills the slot.
        assert any(f in alpha["expression"] for f in ANALYST)
        assert "ts_backfill" in alpha["expression"]


async def test_each_rendered_alpha_names_the_field_and_window_that_made_it(
    client: httpx2.AsyncClient,
) -> None:
    body = await preview(
        client,
        templateId="analyst_revision",
        fieldSetId=await make_field_set(client, ["anl4_eps_mean"]),
    )
    alpha = body["rendered"][0]
    assert alpha["fields"] == {"X": "anl4_eps_mean"}
    assert "W" in alpha["params"]
    assert alpha["dataFields"] == ["anl4_eps_mean"]
    assert alpha["datasets"] == ["analyst4"]


async def test_a_pinned_slot_narrows_the_preview(client: httpx2.AsyncClient) -> None:
    field_set = await make_field_set(client)
    everything = await preview(client, templateId="analyst_revision", fieldSetId=field_set)
    pinned = await preview(
        client,
        templateId="analyst_revision",
        fieldSetId=field_set,
        choices={"X": "anl4_eps_mean"},
    )
    assert pinned["total"] < everything["total"]
    assert {a["fields"]["X"] for a in pinned["rendered"]} == {"anl4_eps_mean"}


async def test_an_illegal_pin_is_a_blocker_with_a_reason(client: httpx2.AsyncClient) -> None:
    body = await preview(
        client,
        templateId="analyst_revision",
        fieldSetId=await make_field_set(client),
        choices={"X": "not_in_the_set"},
    )
    assert body["blockers"]
    assert "not_in_the_set" in body["blockers"][0]
    assert body["rendered"] == []


async def test_the_preview_is_capped_and_says_so(client: httpx2.AsyncClient) -> None:
    body = await preview(
        client, templateId="analyst_revision", fieldSetId=await make_field_set(client), limit=2
    )
    assert len(body["rendered"]) == 2
    assert body["total"] > 2
    assert any("showing the first 2" in n for n in body["notes"])


async def test_fields_the_market_lacks_are_reported(client: httpx2.AsyncClient) -> None:
    field_set = await make_field_set(client, [*ANALYST, "not_synced_here"], name="with-missing")
    body = await preview(client, templateId="analyst_revision", fieldSetId=field_set)
    assert body["missingFields"] == ["not_synced_here"]
    assert body["rendered"]


# --- eligibility, per rendered alpha ------------------------------------------


async def test_every_alpha_is_judged_against_every_type(client: httpx2.AsyncClient) -> None:
    body = await preview(
        client,
        templateId="analyst_revision",
        fieldSetId=await make_field_set(client, ["anl4_eps_mean"]),
    )
    alpha = body["rendered"][0]
    assert {"regular", "power_pool", "atom"} <= set(alpha["eligibility"])
    assert alpha["eligibility"]["power_pool"]["ok"] is True
    assert "power_pool" in alpha["fits"]
    assert body["fitCounts"]["power_pool"] == len(body["rendered"])


async def test_an_alpha_that_misses_a_type_says_why(client: httpx2.AsyncClient) -> None:
    """``complex_decay_blend`` is deliberately too long for Power Pool."""
    body = await preview(
        client,
        templateId="complex_decay_blend",
        fieldSetId=await make_field_set(client),
        limit=3,
    )
    if body["blockers"]:
        pytest.skip(f"template cannot bind to this field set: {body['blockers']}")
    verdict = body["rendered"][0]["eligibility"]["power_pool"]
    assert verdict["ok"] is False
    assert verdict["reasons"]
    assert "power_pool" not in body["rendered"][0]["fits"]


# --- the Power Pool description -----------------------------------------------


async def test_a_draft_follows_brains_template(client: httpx2.AsyncClient) -> None:
    field_set = await make_field_set(client)
    body = await preview(client, templateId="analyst_revision", fieldSetId=field_set)
    expression = body["rendered"][0]["expression"]

    res = await client.post(
        "/api/forge/studio/description/draft",
        json={"expression": expression, "templateId": "analyst_revision", "fieldSetId": field_set},
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    draft = res.json()
    assert draft["saved"] is False
    lines = draft["text"].splitlines()
    assert lines[0].startswith("Idea: ")
    assert lines[1].startswith("Rationale for data used: ")
    assert lines[2].startswith("Rationale for operators used: ")
    # The field's catalog description is what the data part is built from.
    assert "anl4" in draft["dataRationale"]


async def test_a_description_is_saved_and_read_back(client: httpx2.AsyncClient) -> None:
    expression = "rank(ts_delta(ts_backfill(anl4_eps_mean, 63), 21))"
    mine = {
        "expression": expression,
        "idea": "I " + "think revisions drift. " * 6,
        "dataRationale": "The " + "analyst mean estimate is revised in steps. " * 4,
        "operatorRationale": "ts_delta " + "measures the change over a quarter. " * 4,
        "templateId": "analyst_revision",
        "scope": "EQUITY/USA/D1/TOP3000",
    }
    res = await client.put("/api/forge/studio/description", json=mine, headers=CLIENT)
    assert res.status_code == 200, res.text
    assert res.json()["edited"] is True
    assert res.json()["thin"] == []

    got = await client.get("/api/forge/studio/description", params={"expression": expression})
    assert got.json()["idea"].startswith("I think revisions drift")

    # A second draft must not overwrite what was written.
    again = await client.post(
        "/api/forge/studio/description/draft",
        json={"expression": expression, "templateId": "analyst_revision"},
        headers=CLIENT,
    )
    assert again.json()["saved"] is True
    assert again.json()["idea"].startswith("I think revisions drift")


async def test_no_description_yet_reads_as_null(client: httpx2.AsyncClient) -> None:
    res = await client.get("/api/forge/studio/description", params={"expression": "rank(close)"})
    assert res.status_code == 200
    assert res.json() is None


# --- templates of your own ----------------------------------------------------


async def test_writing_editing_and_versioning_a_template(client: httpx2.AsyncClient) -> None:
    made = await client.post(
        "/api/forge/studio/my-templates",
        json={"yamlText": MY_TEMPLATE, "note": "First go"},
        headers=CLIENT,
    )
    assert made.status_code == 200, made.text
    assert made.json()["templateId"] == "my_revision"
    assert made.json()["version"] == 1

    edited = MY_TEMPLATE.replace("[21, 63]", "[21, 63, 126]")
    second = await client.put(
        "/api/forge/studio/my-templates/my_revision",
        json={"yamlText": edited, "note": "Added a half-year window"},
        headers=CLIENT,
    )
    assert second.status_code == 200, second.text
    assert second.json()["version"] == 2

    versions = (await client.get("/api/forge/studio/my-templates/my_revision/versions")).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["note"] == "Added a half-year window"
    assert "126" in versions[0]["yamlText"]
    assert "126" not in versions[1]["yamlText"]


async def test_saving_the_same_text_twice_does_not_make_a_version(
    client: httpx2.AsyncClient,
) -> None:
    await client.post(
        "/api/forge/studio/my-templates", json={"yamlText": MY_TEMPLATE}, headers=CLIENT
    )
    again = await client.put(
        "/api/forge/studio/my-templates/my_revision",
        json={"yamlText": MY_TEMPLATE},
        headers=CLIENT,
    )
    assert again.json()["version"] == 1


async def test_a_broken_template_is_refused_with_every_problem(
    client: httpx2.AsyncClient,
) -> None:
    res = await client.post(
        "/api/forge/studio/my-templates",
        json={"yamlText": "id: bad\nname: Bad\ncategory: x\ncomplexity: huge\nexpr: rank($X)\n"},
        headers=CLIENT,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "invalid_template"
    assert "complexity" in res.json()["detail"]["message"]


async def test_a_builtin_id_cannot_be_taken(client: httpx2.AsyncClient) -> None:
    text = MY_TEMPLATE.replace("my_revision", "analyst_revision")
    res = await client.post(
        "/api/forge/studio/my-templates", json={"yamlText": text}, headers=CLIENT
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "builtin_id"


async def test_the_id_in_the_yaml_may_not_drift(client: httpx2.AsyncClient) -> None:
    await client.post(
        "/api/forge/studio/my-templates", json={"yamlText": MY_TEMPLATE}, headers=CLIENT
    )
    res = await client.put(
        "/api/forge/studio/my-templates/my_revision",
        json={"yamlText": MY_TEMPLATE.replace("my_revision", "renamed")},
        headers=CLIENT,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "id_changed"


async def test_a_builtin_is_cloned_rather_than_edited(client: httpx2.AsyncClient) -> None:
    res = await client.post(
        "/api/forge/studio/my-templates/clone",
        json={"templateId": "analyst_revision", "newId": "my_analyst", "name": "Mine"},
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    assert res.json()["templateId"] == "my_analyst"
    assert res.json()["clonedFrom"] is None or res.json()["clonedFrom"] == "analyst_revision"

    # The clone is usable straight away, on the same field set as a built-in.
    body = await preview(client, templateId="my_analyst", fieldSetId=await make_field_set(client))
    assert body["rendered"]


async def test_your_templates_appear_in_the_library(client: httpx2.AsyncClient) -> None:
    before = (await client.get("/api/forge/templates")).json()
    await client.post(
        "/api/forge/studio/my-templates", json={"yamlText": MY_TEMPLATE}, headers=CLIENT
    )
    after = (await client.get("/api/forge/templates")).json()
    assert len(after["templates"]) == len(before["templates"]) + 1
    mine = next(t for t in after["templates"] if t["id"] == "my_revision")
    assert mine["source"] == "user"
    assert mine["stats"]["sims"] == 0
    assert all(t["source"] == "builtin" for t in before["templates"])


async def test_a_template_of_yours_is_deleted_with_its_versions(
    client: httpx2.AsyncClient,
) -> None:
    await client.post(
        "/api/forge/studio/my-templates", json={"yamlText": MY_TEMPLATE}, headers=CLIENT
    )
    gone = await client.delete("/api/forge/studio/my-templates/my_revision", headers=CLIENT)
    assert gone.json() == {"deleted": True}
    assert (await client.get("/api/forge/studio/my-templates")).json() == []
    assert (await client.get("/api/forge/studio/my-templates/my_revision/versions")).json() == []


async def test_the_live_lint_answers_without_saving(client: httpx2.AsyncClient) -> None:
    good = await client.post(
        "/api/forge/studio/lint", json={"yamlText": MY_TEMPLATE}, headers=CLIENT
    )
    assert good.json()["ok"] is True
    assert good.json()["example"]
    assert "power_pool" in good.json()["fits"]

    bad = await client.post(
        "/api/forge/studio/lint",
        json={"yamlText": MY_TEMPLATE.replace("kind: field", "kind: feild")},
        headers=CLIENT,
    )
    assert bad.json()["ok"] is False
    assert bad.json()["problems"]
    assert (await client.get("/api/forge/studio/my-templates")).json() == []


async def test_a_template_exports_as_a_library_file(client: httpx2.AsyncClient) -> None:
    builtin = await client.get("/api/forge/studio/my-templates/analyst_revision/export")
    assert builtin.status_code == 200, builtin.text
    assert builtin.json()["source"] == "builtin"
    assert "templates:" in builtin.json()["yamlText"]

    # What it exports is what the editor can read back.
    again = await client.post(
        "/api/forge/studio/lint", json={"yamlText": builtin.json()["yamlText"]}, headers=CLIENT
    )
    assert again.json()["ok"] is True, again.json()["problems"]


# --- what the market itself decides -------------------------------------------


async def test_a_group_template_uses_the_markets_own_grouping_fields(
    client: httpx2.AsyncClient,
) -> None:
    """The groups come from the catalog, not from a list in the code."""
    body = await preview(
        client,
        templateId="analyst_revision_group",
        fieldSetId=await make_field_set(client, ["anl4_eps_mean"]),
    )
    assert body["blockers"] == [], body["blockers"]
    assert set(body["offered"]["G"]) <= {"industry", "subindustry", "sector", "market"}
    assert body["offered"]["G"]
    for alpha in body["rendered"]:
        assert alpha["params"]["G"] in body["offered"]["G"]
        assert alpha["params"]["G"] in alpha["expression"]
        # A grouping field is not data: it never counts toward the field limit.
        assert alpha["dataFields"] == ["anl4_eps_mean"]


async def test_a_market_without_the_group_cannot_run_the_template(tmp_path: Path) -> None:
    async for c, _ in make_client(tmp_path):  # no grouping fields seeded
        field_set = await make_field_set(c, ["anl4_eps_mean"])
        body = await preview(c, templateId="analyst_revision_group", fieldSetId=field_set)
        assert body["blockers"]
        assert "$G" in body["blockers"][0]


async def test_a_template_needing_a_field_the_market_lacks_is_blocked(
    tmp_path: Path,
) -> None:
    """``pv_vwap_pressure`` names ``vwap``; this market has none."""
    async for c, _ in make_client(tmp_path, scope_fields=True):
        field_set = await make_field_set(c, ["close"])
        body = await preview(c, templateId="pv_vwap_pressure", fieldSetId=field_set)
        assert body["blockers"]
        assert "vwap" in body["blockers"][0]
