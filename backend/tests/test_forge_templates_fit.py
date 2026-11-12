"""``POST /api/forge/templates/fit``: every template read against a Select pool, through the app.

Free and offline: the catalog is a throwaway fake (``forge_data_harness``), no BRAIN session,
no simulation. The pool is seeded with the same analyst rows the ``/compatible`` tests use, so
the two routes can be compared on one fixture.
"""

from typing import TYPE_CHECKING, Any

import pytest
from forge_data_harness import CLIENT, make_client

from alpha_harness.forge.research.templates import (
    FieldInfo,
    FieldSlot,
    Template,
    fits_pool,
    get,
    pairing_problem,
    slot_fit,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import httpx2


@pytest.fixture
async def client_and_state(tmp_path: Path) -> AsyncIterator[tuple[httpx2.AsyncClient, Any]]:
    async for c, state in make_client(tmp_path):
        yield c, state


async def _seed_analyst_fields(catalog: Any) -> None:
    rows = [
        ("anl4_eps_mean", "analyst4", "analyst", "MATRIX", 0.72, 300, 2000),
        ("anl4_eps_std", "analyst4", "analyst", "MATRIX", 0.60, 250, 1500),
        ("anl4_afv4_eps_high", "analyst4", "analyst", "MATRIX", 0.50, 100, 400),
    ]
    for f, d, cat, t, cov, users, alphas in rows:
        await catalog.execute(
            "INSERT INTO data_field (field_id, dataset_id, category_id, field_type, coverage,"
            " user_count, alpha_count, instrument_type, region, delay, universe, synced_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'EQUITY', 'USA', 1, 'TOP3000', now())",
            [f, d, cat, t, cov, users, alphas],
        )


def _body(field_ids: list[str]) -> dict[str, Any]:
    return {"fieldIds": field_ids, "region": "USA", "universe": "TOP3000", "delay": 1}


async def _fit(client: httpx2.AsyncClient, field_ids: list[str]) -> dict[str, Any]:
    res = await client.post("/api/forge/templates/fit", json=_body(field_ids), headers=CLIENT)
    assert res.status_code == 200, res.text
    out: dict[str, Any] = res.json()
    return out


async def test_fit_lists_every_template_the_library_lists(
    client_and_state: tuple[httpx2.AsyncClient, Any],
) -> None:
    client, state = client_and_state
    await _seed_analyst_fields(state.catalog)

    fit = await _fit(client, ["anl4_eps_mean", "anl4_eps_std"])
    library = (await client.get("/api/forge/templates")).json()["templates"]

    assert {t["id"] for t in fit["templates"]} == {t["id"] for t in library}
    assert fit["missing"] == []
    assert fit["errors"] == []
    assert all("pairingProblem" in t for t in fit["templates"])


async def test_fit_reports_each_slots_candidates_for_a_fitting_template(
    client_and_state: tuple[httpx2.AsyncClient, Any],
) -> None:
    client, state = client_and_state
    await _seed_analyst_fields(state.catalog)

    fit = await _fit(client, ["anl4_eps_mean", "anl4_eps_std"])
    dispersion = next(t for t in fit["templates"] if t["id"] == "analyst_dispersion")

    assert dispersion["fits"] is True
    assert dispersion["note"] is None
    assert dispersion["source"] == "builtin"
    assert dispersion["datasetCategory"] == "analyst"
    slots = {s["name"]: s for s in dispersion["slots"]}
    assert set(slots) == {"X", "Y"}
    assert "anl4_eps_std" in slots["X"]["candidates"]
    assert "anl4_eps_mean" in slots["Y"]["candidates"]
    assert slots["X"]["problem"] is None


async def test_fit_keeps_a_template_that_does_not_fit_and_says_why(
    client_and_state: tuple[httpx2.AsyncClient, Any],
) -> None:
    client, state = client_and_state
    await _seed_analyst_fields(state.catalog)

    fit = await _fit(client, ["anl4_eps_std", "anl4_afv4_eps_high"])
    dispersion = next(t for t in fit["templates"] if t["id"] == "analyst_dispersion")

    assert dispersion["fits"] is False
    assert dispersion["note"] is None
    # Both slots draw on analyst4, so the dataset rule passes; the hint rule blocks it. Y needs
    # a mean field and neither pooled field names one, so the reason says exactly that.
    assert dispersion["pairingProblem"] == (
        "none of your fields has one of these words in its id (average/avg/mean), as slot Y needs"
    )
    assert {s["name"] for s in dispersion["slots"]} == {"X", "Y"}


async def test_fit_explains_a_matrix_pool_against_a_vector_slot(
    client_and_state: tuple[httpx2.AsyncClient, Any],
) -> None:
    client, state = client_and_state
    await _seed_analyst_fields(state.catalog)

    fit = await _fit(client, ["anl4_eps_mean"])
    intensity = next(t for t in fit["templates"] if t["id"] == "vector_intensity")
    (slot,) = [s for s in intensity["slots"] if s["kind"] == "field"]

    assert slot["kind"] == "field"
    assert slot["type"] == "VECTOR"
    assert slot["aggregate"] == "vec_sum"
    assert slot["candidates"] == []
    assert "anl4_eps_mean" in (slot["problem"] or "")
    assert "VECTOR" in (slot["problem"] or "")
    assert intensity["fits"] is False


async def test_fit_names_picked_ids_the_catalog_does_not_have(
    client_and_state: tuple[httpx2.AsyncClient, Any],
) -> None:
    client, state = client_and_state
    await _seed_analyst_fields(state.catalog)

    fit = await _fit(client, ["anl4_eps_mean", "anl4_not_a_field"])

    assert fit["missing"] == ["anl4_not_a_field"]


async def test_fit_refuses_more_than_fifty_fields(
    client_and_state: tuple[httpx2.AsyncClient, Any],
) -> None:
    client, _ = client_and_state
    res = await client.post(
        "/api/forge/templates/fit",
        json=_body([f"f{i}" for i in range(51)]),
        headers=CLIENT,
    )
    assert res.status_code == 422


def _template_with_field_slots(count: int) -> Template:
    return Template(
        id="many_slots",
        name="many slots",
        category="test/many",
        complexity="complex",
        slots={f"S{i}": FieldSlot() for i in range(count)},
        expr="",
        rationale="",
    )


def test_a_template_too_big_to_walk_is_declined_not_reported_as_unfit() -> None:
    pool = [FieldInfo(id=f"f{i}", dataset=f"d{i % 5}") for i in range(50)]
    template = _template_with_field_slots(5)

    fits, note = fits_pool(template, pool)

    assert fits is False
    assert note is not None
    assert "too many" in note


def test_a_template_within_the_walk_budget_is_checked() -> None:
    pool = [FieldInfo(id=f"f{i}", dataset=f"d{i}") for i in range(4)]
    template = _template_with_field_slots(2)

    fits, note = fits_pool(template, pool)

    assert fits is True
    assert note is None


def _same_dataset_pair() -> Template:
    """X and Y must come from one dataset (``Y.same_dataset_as = X``), nothing else."""
    return Template(
        id="pair",
        name="pair",
        category="test/pair",
        complexity="simple",
        slots={"X": FieldSlot(), "Y": FieldSlot(same_dataset_as="X")},
        expr="",
        rationale="",
    )


def test_pairing_names_a_real_dataset_mismatch() -> None:
    candidates = {
        "X": [FieldInfo(id="a", dataset="analyst4")],
        "Y": [FieldInfo(id="b", dataset="fundamental6")],
    }

    problem = pairing_problem(_same_dataset_pair(), candidates)

    assert problem is not None
    assert "must come from the same dataset" in problem
    assert "none of your candidate pairs" in problem
    assert "don't record" not in problem  # a mismatch is not an unknown dataset


def test_pairing_says_unknown_when_a_candidate_has_no_dataset_id() -> None:
    candidates = {
        "X": [FieldInfo(id="a", dataset="analyst4")],
        "Y": [FieldInfo(id="b", dataset="")],
    }

    problem = pairing_problem(_same_dataset_pair(), candidates)

    assert problem is not None
    assert "don't record which dataset" in problem
    assert "none of your candidate pairs" not in problem  # must not claim they differ


def test_pairing_is_silent_when_one_candidate_pair_shares_a_dataset() -> None:
    candidates = {
        "X": [FieldInfo(id="a", dataset="analyst4"), FieldInfo(id="c", dataset="fundamental6")],
        "Y": [FieldInfo(id="b", dataset="analyst4")],
    }

    assert pairing_problem(_same_dataset_pair(), candidates) is None


def test_pairing_is_left_to_the_slot_when_one_side_has_no_candidates() -> None:
    candidates = {"X": [FieldInfo(id="a", dataset="analyst4")], "Y": []}

    assert pairing_problem(_same_dataset_pair(), candidates) is None


def _dispersion(pool: list[FieldInfo]) -> tuple[Template, dict[str, list[FieldInfo]]]:
    """The library's analyst_dispersion template, with each slot's candidates from ``pool``."""
    template = get("analyst_dispersion")
    assert template is not None
    candidates = {
        n: list(slot_fit(template.field_slot(n), pool).candidates) for n in template.field_slots
    }
    return template, {n: [f for f in pool if f.id in ids] for n, ids in candidates.items()}


def test_pairing_names_the_missing_hint_for_analyst_dispersion() -> None:
    """Both fields are analyst4, so the dataset rule passes. X (std) takes anl4_eps_std; Y
    needs a mean field, and neither pooled field has a mean word in its id, so the reason
    says that instead of the generic stopgap."""
    pool = [
        FieldInfo(id="anl4_eps_std", dataset="analyst4", type="MATRIX"),
        FieldInfo(id="anl4_afv4_eps_high", dataset="analyst4", type="MATRIX"),
    ]
    template, candidates = _dispersion(pool)

    assert pairing_problem(template, candidates) == (
        "none of your fields has one of these words in its id (average/avg/mean), as slot Y needs"
    )


def test_pairing_is_silent_when_a_hint_pair_matches_the_statistic_and_stem() -> None:
    pool = [
        FieldInfo(id="anl4_eps_std", dataset="analyst4", type="MATRIX"),
        FieldInfo(id="anl4_eps_mean", dataset="analyst4", type="MATRIX"),
    ]
    template, candidates = _dispersion(pool)

    assert pairing_problem(template, candidates) is None


def test_pairing_names_a_stem_mismatch_when_both_halves_exist_but_measure_different_things() -> (
    None
):
    pool = [
        FieldInfo(id="anl4_eps_std", dataset="analyst4", type="MATRIX"),
        FieldInfo(id="anl4_rev_mean", dataset="analyst4", type="MATRIX"),
    ]
    template, candidates = _dispersion(pool)

    problem = pairing_problem(template, candidates)

    assert problem is not None
    assert "share a name stem" in problem
    assert "pair fields by what they measure, not just dataset" in problem


def test_pairing_is_silent_for_a_template_without_same_dataset_slots() -> None:
    template = _template_with_field_slots(2)
    candidates = {"S0": [FieldInfo(id="a", dataset="d1")], "S1": [FieldInfo(id="b", dataset="d2")]}

    assert pairing_problem(template, candidates) is None
