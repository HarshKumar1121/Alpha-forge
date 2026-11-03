"""``/api/forge/pipelines``: plan preview (free, offline) and a real run (CLAUDE.md rule 4 —
the only BRAIN surface here is a fake transport, so nothing spends a real simulation).

Preview is tested against a ``SimpleNamespace`` state with a real SQLite/DuckDB pair and a real
``QuotaGovernor``, the same minimal shape ``forge_data_harness`` uses for the field-set routes.
Run additionally needs a real ``BatchEngine`` wired to a scripted, no-network tracker — the same
pattern ``test_forge_pipelines_brain_adapter.py`` uses to prove the adapter without ever reaching
BRAIN.
"""

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from forge_data_harness import USA_FIELDS, _seed
from sqlalchemy import select

from alpha_harness.api import forge_pipelines
from alpha_harness.catalog.queries import CatalogQueries
from alpha_harness.db.duck import Catalog
from alpha_harness.db.models import SimStatus, SimulationRecord, utcnow
from alpha_harness.db.sqlite import Database
from alpha_harness.engine.lifecycle import record_launch, transition
from alpha_harness.engine.slots import BatchEngine
from alpha_harness.forge.data.models import FieldSet
from alpha_harness.forge.pipelines.brain_adapter import BrainSimulator
from alpha_harness.forge.quota.governor import QuotaGovernor
from alpha_harness.forge.studio.models import UserTemplate
from alpha_harness.main import create_app
from alpha_harness.tasks import TaskRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from alpha_harness.brain.schemas import SimulationRequest

CLIENT = {"x-harness-client": "1"}

SIMPLE_TEMPLATE = """
templates:
  - id: pipeline_test_simple
    name: Pipeline test
    category: level
    complexity: simple
    angle: level
    slots:
      X: {kind: field}
    expr: rank($X)
    rationale: A plain level factor, for exercising the pipeline route end to end.
"""


class ScriptedTracker:
    """Real lifecycle transitions, no network — see ``test_forge_pipelines_brain_adapter.py``."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.plan: dict[int, tuple[SimStatus, str | None]] = {}

    async def submit(self, request: SimulationRequest, record_id: int) -> None:  # noqa: ARG002
        async with self.db.session() as session:
            await transition(
                session,
                record_id,
                from_=[SimStatus.QUEUED],
                status=SimStatus.PENDING,
                sent_at=utcnow(),
            )
        await asyncio.sleep(0)
        async with self.db.session() as session:
            await record_launch(session, record_id, f"PLATFORM{record_id}", None)
        status, alpha_id = self.plan.get(record_id, (SimStatus.COMPLETE, f"ALPHA{record_id}"))
        async with self.db.session() as session:
            await transition(
                session,
                record_id,
                from_=[SimStatus.RUNNING],
                status=status,
                alpha_id=alpha_id,
                finished_at=utcnow(),
            )

    async def notify(self) -> None:
        return None


class StubEndpoints:
    """Every BRAIN call the pipeline route touches: alpha bodies, checks, correlations."""

    def __init__(self) -> None:
        self.checked: list[str] = []
        self.correlated: list[str] = []

    async def alpha_body(self, alpha_id: str) -> dict[str, Any]:
        # Clears every ``regular`` gate (config/forge/alpha_types/regular.yaml) so the
        # pipeline's own train-gate filter lets this candidate through to check/correlate.
        block = {"sharpe": 2.0, "fitness": 1.5, "turnover": 0.3}
        return {
            "id": alpha_id,
            "is": {"checks": [], **block},
            "train": block,
            "test": {"sharpe": 1.2},
        }

    async def get_recordset(self, _alpha_id: str, name: str) -> Any:
        assert name == "pnl"
        rows = [
            {"date": "2026-01-01", "pnl": 10.0},
            {"date": "2026-01-02", "pnl": 25.0},
        ]

        class _RecordSet:
            def rows(self) -> list[dict[str, Any]]:
                return rows

        return _RecordSet()

    async def check_alpha(self, alpha_id: str) -> dict[str, Any]:
        self.checked.append(alpha_id)
        return {"is": {"checks": []}}

    async def correlations(self, alpha_id: str, kind: str = "self") -> dict[str, Any]:  # noqa: ARG002
        self.correlated.append(alpha_id)
        return {"max": 0.1}


async def drain(engine: BatchEngine) -> None:
    while await engine._fill_slots():  # pyright: ignore[reportPrivateUsage]
        pass


async def wait_for_rows(db: Database, n: int) -> list[int]:
    for _ in range(200):
        async with db.session() as session:
            ids = (
                await session.scalars(select(SimulationRecord.id).order_by(SimulationRecord.id))
            ).all()
        if len(ids) >= n:
            return list(ids)
        await asyncio.sleep(0)
    raise AssertionError("simulation rows never appeared")


class Harness(SimpleNamespace):
    db: Database
    catalog: Catalog
    queries: CatalogQueries
    engine: Any
    endpoints: StubEndpoints
    tasks: TaskRegistry


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    db = Database(tmp_path / "harness.db")
    await db.create_all()
    catalog = Catalog(tmp_path / "catalog.duckdb")
    await catalog.open()
    await _seed(catalog)

    tracker = ScriptedTracker(db)
    endpoints = StubEndpoints()
    engine = BatchEngine(db, object(), tracker, on_unauthorized=_no_session)  # type: ignore[arg-type]
    engine.max_batch = 1
    engine.governor = QuotaGovernor(db)
    engine._last_orphan_sweep = float("inf")  # pyright: ignore[reportPrivateUsage]

    state = Harness(
        db=db,
        catalog=catalog,
        queries=CatalogQueries(catalog),
        engine=engine,
        endpoints=endpoints,
        tasks=TaskRegistry(on_change=_noop),
    )
    state.tracker = tracker
    try:
        yield state
    finally:
        await catalog.close()
        await db.dispose()


async def _no_session() -> bool:
    return False


async def _noop(_payload: dict[str, Any]) -> None:
    return None


@pytest.fixture
async def client(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx2.AsyncClient]:
    # The adapter's default poll interval (2s) is right for production; a test waits in real
    # wall-clock time, so it is given a fast one here instead of slowing every run down.
    def fast_simulator(engine: Any, db: Any, endpoints: Any, lane: str) -> BrainSimulator:
        return BrainSimulator(engine, db, endpoints, lane, poll_interval=0.01)

    monkeypatch.setattr(forge_pipelines, "BrainSimulator", fast_simulator)
    app = create_app()
    app.state.harness = harness
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


async def make_field_set(harness: Harness) -> int:
    async with harness.db.session() as session:
        row = FieldSet(
            name="pv",
            instrument_type="EQUITY",
            region="USA",
            delay=1,
            universe="TOP3000",
            kind="manual",
            field_ids=[f[0] for f in USA_FIELDS if f[1] == "pv1"],
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        return row.id


async def make_template(harness: Harness) -> None:
    async with harness.db.session() as session:
        session.add(
            UserTemplate(
                template_id="pipeline_test_simple",
                name="Pipeline test",
                yaml_text=SIMPLE_TEMPLATE,
                version=1,
            )
        )


# --- list -----------------------------------------------------------------------


async def test_lists_every_pipeline_config(client: httpx2.AsyncClient) -> None:
    res = await client.get("/api/forge/pipelines")
    assert res.status_code == 200, res.text
    names = {p["name"] for p in res.json()}
    assert {"regular", "power_pool", "atom", "region_agnostic"} <= names
    regular = next(p for p in res.json() if p["name"] == "regular")
    assert regular["lane"] == "forge_regular"
    assert regular["defaultBudget"] == 60


# --- preview ----------------------------------------------------------------------


async def test_preview_reports_generated_eligible_and_admission(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    await make_template(harness)
    field_set_id = await make_field_set(harness)

    res = await client.post(
        "/api/forge/pipelines/preview",
        json={
            "pipeline": "regular",
            "fieldSetId": field_set_id,
            "templateIds": ["pipeline_test_simple"],
            "budget": 3,
        },
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["pipeline"] == "regular"
    assert body["generated"] >= 1
    assert body["eligible"] <= body["generated"]
    assert body["admission"]["lane"] == "forge_regular"
    assert body["admission"]["admitted"] <= 3
    assert body["budget"] == 3


async def test_preview_refuses_an_unknown_pipeline(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    await make_template(harness)
    field_set_id = await make_field_set(harness)
    res = await client.post(
        "/api/forge/pipelines/preview",
        json={
            "pipeline": "not_a_pipeline",
            "fieldSetId": field_set_id,
            "templateIds": ["pipeline_test_simple"],
        },
        headers=CLIENT,
    )
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "unknown_pipeline"


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


async def make_named_field_set(harness: Harness, name: str, field_ids: list[str]) -> int:
    async with harness.db.session() as session:
        row = FieldSet(
            name=name,
            instrument_type="EQUITY",
            region="USA",
            delay=1,
            universe="TOP3000",
            kind="manual",
            field_ids=field_ids,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        return row.id


async def test_preview_uses_the_chosen_field_sets_own_fields(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    """Two field sets, same scope, disjoint fields: the preview for each must generate
    candidates only from the fields *that* field set resolves to, never a stand-in or the
    other set's fields."""
    await make_template(harness)
    field_set_a = await make_named_field_set(harness, "A", ["close"])
    field_set_b = await make_named_field_set(
        harness, "B", ["anl4_eps_mean", "anl4_eps_low", "anl4_rev_mean"]
    )

    res_a = await client.post(
        "/api/forge/pipelines/preview",
        json={
            "pipeline": "regular",
            "fieldSetId": field_set_a,
            "templateIds": ["pipeline_test_simple"],
            "budget": 3,
        },
        headers=CLIENT,
    )
    assert res_a.status_code == 200, res_a.text
    body_a = res_a.json()
    assert body_a["fieldSet"] == "A"
    assert body_a["generated"] == 1

    res_b = await client.post(
        "/api/forge/pipelines/preview",
        json={
            "pipeline": "regular",
            "fieldSetId": field_set_b,
            "templateIds": ["pipeline_test_simple"],
            "budget": 3,
        },
        headers=CLIENT,
    )
    assert res_b.status_code == 200, res_b.text
    body_b = res_b.json()
    assert body_b["fieldSet"] == "B"
    assert body_b["generated"] == 3


async def test_run_candidates_reference_exactly_the_chosen_field_sets_fields(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    await make_template(harness)
    field_set_b = await make_named_field_set(
        harness, "B", ["anl4_eps_mean", "anl4_eps_low", "anl4_rev_mean"]
    )

    started = await client.post(
        "/api/forge/pipelines/run",
        json={
            "pipeline": "regular",
            "fieldSetId": field_set_b,
            "templateIds": ["pipeline_test_simple"],
            "budget": 3,
        },
        headers=CLIENT,
    )
    assert started.status_code == 200, started.text
    task_id = started.json()["taskId"]

    for _ in range(50):
        await wait_for_rows(harness.db, 1)
        await drain(harness.engine)
        await asyncio.sleep(0)
        got = await client.get(f"/api/forge/pipelines/runs/{task_id}")
        if got.status_code == 200:
            break
        if got.status_code not in (404, 409):
            pytest.fail(got.text)
        await asyncio.sleep(0.01)
    else:
        pytest.fail("pipeline run never finished")

    body = got.json()
    expressions = {c["expression"] for c in body["candidates"]}
    allowed = {"anl4_eps_mean", "anl4_eps_low", "anl4_rev_mean"}
    assert expressions, "no candidates were produced"
    for expr in expressions:
        assert any(f in expr for f in allowed), (
            f"{expr!r} does not reference field set B's own fields"
        )
    assert "close" not in " ".join(expressions)


async def test_preview_never_queues_a_simulation(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    await make_template(harness)
    field_set_id = await make_field_set(harness)
    await client.post(
        "/api/forge/pipelines/preview",
        json={
            "pipeline": "regular",
            "fieldSetId": field_set_id,
            "templateIds": ["pipeline_test_simple"],
            "budget": 5,
        },
        headers=CLIENT,
    )
    async with harness.db.session() as session:
        rows = (await session.scalars(select(SimulationRecord))).all()
    assert rows == []


# --- run --------------------------------------------------------------------------


async def test_run_spends_through_the_governor_and_reports_candidates(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    await make_template(harness)
    field_set_id = await make_field_set(harness)

    started = await client.post(
        "/api/forge/pipelines/run",
        json={
            "pipeline": "regular",
            "fieldSetId": field_set_id,
            "templateIds": ["pipeline_test_simple"],
            "budget": 2,
        },
        headers=CLIENT,
    )
    assert started.status_code == 200, started.text
    task_id = started.json()["taskId"]

    # Drive the fake BRAIN edge: let the engine enqueue, then finish every row as a hit.
    for _ in range(50):
        await wait_for_rows(harness.db, 1)
        await drain(harness.engine)
        await asyncio.sleep(0)
        got = await client.get(f"/api/forge/pipelines/runs/{task_id}")
        if got.status_code == 200:
            break
        if got.status_code not in (404, 409):
            pytest.fail(got.text)
        await asyncio.sleep(0.01)
    else:
        pytest.fail("pipeline run never finished")

    body = got.json()
    assert body["status"] == "done"
    assert body["spent"] >= 1
    assert body["candidates"]
    assert all("rank(" in c["expression"] for c in body["candidates"])
    # The train-gate passers were checked and correlated — never submitted (CLAUDE.md rule 1).
    assert harness.endpoints.checked
    assert harness.endpoints.correlated

    # The run is on the Submittable Board, its paid-for checks kept for every screen.
    board = (await client.get("/api/forge/board")).json()
    on_board = [
        a
        for d in board["days"]
        for t in d["types"]
        for f in t["families"]
        for a in [f["leader"], *f["members"]]
    ]
    assert {a["alphaId"] for a in on_board} == {
        c["alphaId"] for c in body["candidates"] if c["alphaId"]
    }
    checked = {a["alphaId"]: a["checksSource"] for a in on_board}
    assert all(checked[a] == "check" for a in harness.endpoints.checked)

    # The run's own history row survives past the finished report — the history panel's data.
    runs = (await client.get("/api/forge/pipelines/runs")).json()
    row = next(r for r in runs if r["taskId"] == task_id)
    assert row["status"] == "done"
    assert row["pipeline"] == "regular"
    assert row["spent"] == body["spent"]
    assert row["candidatesCount"] == len(body["candidates"])


async def test_run_report_404s_for_an_unknown_task(client: httpx2.AsyncClient) -> None:
    res = await client.get("/api/forge/pipelines/runs/does-not-exist")
    assert res.status_code == 404


# --- fields picked on the spot, without a saved field set ---------------------------------


async def test_preview_accepts_fields_picked_on_the_spot_without_a_saved_field_set(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    """The Pipelines screen's field picker can search and tick fields directly — a plan must
    work from those fields alone, with no `FieldSet` row ever created."""
    await make_template(harness)

    res = await client.post(
        "/api/forge/pipelines/preview",
        json={
            "pipeline": "regular",
            "fields": {
                "fieldIds": ["anl4_eps_mean", "anl4_rev_mean"],
                "region": "USA",
                "universe": "TOP3000",
                "delay": 1,
                "instrumentType": "EQUITY",
            },
            "templateIds": ["pipeline_test_simple"],
            "budget": 3,
        },
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["generated"] == 2
    expressions = {c["expression"] for c in body["candidatesSample"]}
    assert all("anl4_eps_mean" in e or "anl4_rev_mean" in e for e in expressions)
    assert len(expressions) == 2

    async with harness.db.session() as session:
        rows = (await session.scalars(select(FieldSet))).all()
    assert rows == []  # nothing was persisted just to preview


async def test_preview_refuses_with_neither_a_field_set_nor_fields(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    await make_template(harness)
    res = await client.post(
        "/api/forge/pipelines/preview",
        json={
            "pipeline": "regular",
            "templateIds": ["pipeline_test_simple"],
            "budget": 3,
        },
        headers=CLIENT,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "missing_fields"


# --- per-alpha-type verdicts: FITS / FITS_BUT_WEAK / DOESNT_FIT, not a blanket "fits all" ---


async def test_preview_candidates_carry_a_different_verdict_per_alpha_type(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    await make_template(harness)
    field_set_id = await make_named_field_set(harness, "pv", ["close"])

    res = await client.post(
        "/api/forge/pipelines/preview",
        json={
            "pipeline": "regular",
            "fieldSetId": field_set_id,
            "templateIds": ["pipeline_test_simple"],
            "budget": 3,
        },
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    sample = res.json()["candidatesSample"]
    assert sample
    verdicts = sample[0]["verdicts"]
    # Judged against every alpha type, not just "regular" — this pipeline's own.
    assert {"regular", "power_pool", "atom"} <= set(verdicts)
    assert verdicts["regular"]["verdict"] == "FITS"
    # "close" is a single pv1 field, well under Power Pool's limits — also a clean FITS, not
    # the blanket true/false this replaces.
    assert verdicts["power_pool"]["verdict"] == "FITS"


# --- semantic pairing: what this screen previews is what the fixed `bindings()` produces -------
#
# Same incident as `test_forge_templates_engine.py`'s `analyst_dispersion` regression: a stray
# same-dataset field (`anl4_afv4_eps_high`) and an unrelated-dataset field
# (`actual_eps_value_quarterly`) must never be bound together into a candidate, here exercised
# through the actual `/preview` and `/run` routes rather than the engine function directly.


async def _seed_dispersion_fields(harness: Harness) -> None:
    rows = [
        ("anl4_eps_mean", "analyst4", "analyst", "MATRIX", 0.72, 300, 2000),
        ("anl4_eps_std", "analyst4", "analyst", "MATRIX", 0.60, 250, 1500),
        ("anl4_rev_mean", "analyst4", "analyst", "MATRIX", 0.70, 200, 900),
        ("anl4_rev_std", "analyst4", "analyst", "MATRIX", 0.55, 150, 700),
        # Real analyst4 field that is not a mean/std pair at all.
        ("anl4_afv4_eps_high", "analyst4", "analyst", "MATRIX", 0.50, 100, 400),
        # Same dataset in a naive id-only reading, but not an estimate field.
        ("actual_eps_value_quarterly", "analyst4", "analyst", "MATRIX", 0.50, 100, 400),
    ]
    for f, d, cat, t, cov, users, alphas in rows:
        await harness.catalog.execute(
            "INSERT INTO data_field (field_id, dataset_id, category_id, field_type, coverage,"
            " user_count, alpha_count, instrument_type, region, delay, universe, synced_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'EQUITY', 'USA', 1, 'TOP3000', now())",
            [f, d, cat, t, cov, users, alphas],
        )


async def test_preview_only_pairs_the_true_mean_std_fields(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    await _seed_dispersion_fields(harness)
    field_set_id = await make_named_field_set(
        harness,
        "dispersion",
        [
            "anl4_eps_mean",
            "anl4_eps_std",
            "anl4_rev_mean",
            "anl4_rev_std",
            "anl4_afv4_eps_high",
            "actual_eps_value_quarterly",
        ],
    )

    res = await client.post(
        "/api/forge/pipelines/preview",
        json={
            "pipeline": "regular",
            "fieldSetId": field_set_id,
            "templateIds": ["analyst_dispersion"],
            "budget": 5,
        },
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    expressions = {c["expression"] for c in body["candidatesSample"]}
    assert len(expressions) == 2
    assert any("anl4_eps_std" in e and "anl4_eps_mean" in e for e in expressions)
    assert any("anl4_rev_std" in e and "anl4_rev_mean" in e for e in expressions)
    for expr in expressions:
        assert "anl4_afv4_eps_high" not in expr
        assert "actual_eps_value_quarterly" not in expr
