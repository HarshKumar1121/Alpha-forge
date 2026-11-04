"""``POST /api/forge/workbench/run``: candidates go through the real ``BatchEngine`` and
``QuotaGovernor`` against a scripted, no-network tracker (never a real BRAIN call — CLAUDE.md
rule 4), tagged so they land in ``SimulationRecord`` under ``forge.reserve`` like any other
hand-approved run, with each candidate's template/field/region kept in
``forge_workbench_run_item`` for the results table.

Harness mirrors ``test_forge_pipelines_api.py``'s, trimmed to what the run route actually touches
(no catalog/field sets — Workbench already has finished expressions, not a field set to expand).
"""

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from sqlalchemy import select

from alpha_harness.api import forge_workbench
from alpha_harness.db.models import SimStatus, SimulationRecord, utcnow
from alpha_harness.db.sqlite import Database
from alpha_harness.engine.lifecycle import record_launch, transition
from alpha_harness.engine.slots import BatchEngine
from alpha_harness.forge.pipelines.brain_adapter import BrainSimulator
from alpha_harness.forge.quota.governor import QuotaGovernor
from alpha_harness.main import create_app
from alpha_harness.tasks import TaskRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from alpha_harness.brain.schemas import SimulationRequest

CLIENT = {"x-harness-client": "1"}
USA = {"region": "USA", "delay": 1, "universe": "TOP3000"}


class ScriptedTracker:
    """Real lifecycle transitions, no network — see ``test_forge_pipelines_brain_adapter.py``."""

    def __init__(self, db: Database) -> None:
        self.db = db
        #: record id -> (status, alpha id), for a test that wants one request to fail.
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
    """Only what ``BrainSimulator.simulate`` needs to turn a finished record into an outcome."""

    async def alpha_body(self, alpha_id: str) -> dict[str, Any]:
        return {
            "id": alpha_id,
            "is": {"sharpe": 1.8},
            "train": {"sharpe": 1.8},
            "test": {"sharpe": 1.1},
        }


async def _no_session() -> bool:
    return False


async def _noop(_payload: dict[str, Any]) -> None:
    return None


class Harness(SimpleNamespace):
    db: Database
    engine: Any
    endpoints: StubEndpoints
    tasks: TaskRegistry


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    db = Database(tmp_path / "harness.db")
    await db.create_all()

    tracker = ScriptedTracker(db)
    engine = BatchEngine(db, object(), tracker, on_unauthorized=_no_session)  # type: ignore[arg-type]
    engine.max_batch = 1
    engine.governor = QuotaGovernor(db)
    engine._last_orphan_sweep = float("inf")  # pyright: ignore[reportPrivateUsage]

    state = Harness(
        db=db, engine=engine, endpoints=StubEndpoints(), tasks=TaskRegistry(on_change=_noop)
    )
    state.tracker = tracker
    try:
        yield state
    finally:
        await db.dispose()


@pytest.fixture
async def client(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx2.AsyncClient]:
    def fast_simulator(engine: Any, db: Any, endpoints: Any, lane: str) -> BrainSimulator:
        return BrainSimulator(engine, db, endpoints, lane, poll_interval=0.01)

    monkeypatch.setattr(forge_workbench, "BrainSimulator", fast_simulator)
    app = create_app()
    app.state.harness = harness
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


async def drain(engine: BatchEngine) -> None:
    while await engine._fill_slots():  # pyright: ignore[reportPrivateUsage]
        pass


async def _wait_for_run(client: httpx2.AsyncClient, task_id: str) -> dict[str, Any]:
    for _ in range(200):
        res = await client.get(f"/api/forge/workbench/run/{task_id}", headers=CLIENT)
        body = res.json()
        if body["status"] == "done":
            return body
        await asyncio.sleep(0)
    raise AssertionError("workbench run never finished")


async def test_run_simulates_every_candidate_and_tags_the_task(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    res = await client.post(
        "/api/forge/workbench/run",
        params=USA,
        json={
            "candidates": [
                {
                    "expression": "rank(close)",
                    "templateId": "core_price_volume",
                    "fieldIds": ["close"],
                },
                {"expression": "rank(volume)", "fieldIds": ["volume"]},
            ]
        },
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    started = res.json()
    assert started["lane"] == "reserve"
    assert started["admitted"] == 2

    await drain(harness.engine)
    result = await _wait_for_run(client, started["taskId"])
    assert len(result["items"]) == 2
    assert {i["status"] for i in result["items"]} == {"done"}
    assert {i["alphaId"] for i in result["items"]} == {"ALPHA1", "ALPHA2"}
    assert result["items"][0]["templateId"] == "core_price_volume"
    assert result["items"][0]["trainSharpe"] == pytest.approx(1.8)
    assert result["items"][1]["templateId"] is None

    async with harness.db.session() as session:
        tasks = (await session.scalars(select(SimulationRecord.task).distinct())).all()
    assert tasks == ["forge.reserve"]


async def test_a_failed_simulation_is_reported_per_item(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    harness.tracker.plan[1] = (SimStatus.FAILED, None)
    res = await client.post(
        "/api/forge/workbench/run",
        params=USA,
        json={"candidates": [{"expression": "rank(close)"}]},
        headers=CLIENT,
    )
    started = res.json()
    await drain(harness.engine)
    result = await _wait_for_run(client, started["taskId"])
    assert result["items"][0]["status"] == "failed"


async def test_get_run_404s_for_an_unknown_task(client: httpx2.AsyncClient) -> None:
    res = await client.get("/api/forge/workbench/run/no-such-task", headers=CLIENT)
    assert res.status_code == 404


async def test_runs_lists_every_run_grouped_for_the_tasks_screen(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    res = await client.post(
        "/api/forge/workbench/run",
        params=USA,
        json={"candidates": [{"expression": "rank(close)"}, {"expression": "rank(volume)"}]},
        headers=CLIENT,
    )
    started = res.json()
    await drain(harness.engine)
    await _wait_for_run(client, started["taskId"])

    res = await client.get("/api/forge/workbench/runs", headers=CLIENT)
    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["taskId"] == started["taskId"]
    assert row["region"] == "USA"
    assert row["total"] == 2
    assert row["done"] == 2
    assert row["failed"] == 0
    assert row["bestSharpe"] == pytest.approx(1.8)
