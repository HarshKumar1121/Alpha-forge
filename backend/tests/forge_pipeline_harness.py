"""Shared fixtures for the Forge plan and run tests (the `harness` and `client` fixtures and
the fake engines they wire in). Used by the advisor, agent, board, breakdown and submit-helper
tests. Preview and run are tested through each consumer that needs them."""

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
