"""``POST /api/forge/probe/field``: the 3 cheap behaviour probes run through the real
``BatchEngine``/``QuotaGovernor`` against a scripted, no-network tracker (never a real BRAIN
call — CLAUDE.md rule 4), tagged ``forge.explore`` like any other probe spend, and the outcome
is classified into an archetype via ``forge/research/profiler.py``.

Harness mirrors ``test_forge_workbench_run.py``'s, except the route here awaits the simulator
synchronously (3 sims, no background task), so the client call and draining the engine must run
concurrently.
"""

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from sqlalchemy import select

from alpha_harness.api import forge_probe
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

#: Keyed by record id, in the order ``probes()`` builds them: level, change, history.
#: Level's turnover (3%) sits below the "slow" threshold (4%, config/forge/court.yaml); history
#: has the strongest train Sharpe, so the archetype should land on slow / history / sign +1.
ALPHAS = {
    1: {
        "is": {"sharpe": 0.4, "turnover": 0.03, "longCount": 50, "shortCount": 50},
        "train": {"sharpe": 0.4},
    },
    2: {"is": {"sharpe": 0.2}, "train": {"sharpe": 0.2}},
    3: {"is": {"sharpe": 0.9}, "train": {"sharpe": 0.9}},
}


class ScriptedTracker:
    """Real lifecycle transitions, no network — see ``test_forge_pipelines_brain_adapter.py``."""

    def __init__(self, db: Database) -> None:
        self.db = db

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
        async with self.db.session() as session:
            await transition(
                session,
                record_id,
                from_=[SimStatus.RUNNING],
                status=SimStatus.COMPLETE,
                alpha_id=f"ALPHA{record_id}",
                finished_at=utcnow(),
            )

    async def notify(self) -> None:
        return None


class StubEndpoints:
    async def alpha_body(self, alpha_id: str) -> dict[str, Any]:
        record_id = int(alpha_id.removeprefix("ALPHA"))
        return {"id": alpha_id, **ALPHAS[record_id]}


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

    monkeypatch.setattr(forge_probe, "BrainSimulator", fast_simulator)
    app = create_app()
    app.state.harness = harness
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


async def drain(engine: BatchEngine) -> None:
    while await engine._fill_slots():  # pyright: ignore[reportPrivateUsage]
        pass


async def _post_while_draining(
    client: httpx2.AsyncClient, harness: Harness, **kwargs: Any
) -> httpx2.Response:
    """The route awaits the simulator in-request, so nothing drains the engine on its own."""
    post = asyncio.ensure_future(client.post("/api/forge/probe/field", headers=CLIENT, **kwargs))
    while not post.done():
        await drain(harness.engine)
        await asyncio.sleep(0)
    return await post


async def test_probe_field_runs_three_sims_and_classifies_archetype(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    res = await _post_while_draining(client, harness, params=USA, json={"fieldId": "close"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["lane"] == "explore"
    assert body["admitted"] == 3
    assert body["asked"] == 3
    assert body["archetype"] == "slow"
    assert body["bestAngle"] == "history"
    assert body["sign"] == 1
    assert len(body["angles"]) == 3
    assert {a["name"] for a in body["angles"]} == {"level", "change", "history"}
    assert all(a["alphaId"] for a in body["angles"])

    async with harness.db.session() as session:
        tasks = (await session.scalars(select(SimulationRecord.task).distinct())).all()
    assert tasks == ["forge.explore"]


async def test_probe_field_without_a_governor_refuses(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    harness.engine.governor = None
    res = await client.post(
        "/api/forge/probe/field", params=USA, json={"fieldId": "close"}, headers=CLIENT
    )
    assert res.status_code == 503
