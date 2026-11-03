"""``BrainSimulator`` over a real ``BatchEngine`` with a fake network (CLAUDE.md rule 4).

Follows the same fake-tracker pattern as ``test_forge_quota.py``: the engine, packer and
lifecycle transitions are the real ones; only the HTTP edge (tracker.submit, alpha_body,
get_recordset) is stubbed, so the adapter is proven against the engine's real semantics.
"""

import asyncio
import itertools
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import select

from alpha_harness.db.models import SimStatus, SimulationRecord, utcnow
from alpha_harness.db.sqlite import Database
from alpha_harness.engine.lifecycle import SubmissionFailed, record_launch, transition
from alpha_harness.engine.slots import BatchEngine
from alpha_harness.forge import config as forge_config
from alpha_harness.forge.pipelines.brain_adapter import BrainSimulator
from alpha_harness.forge.quota.governor import QuotaGovernor
from alpha_harness.forge.simulator import request_for

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path

    from alpha_harness.brain.schemas import SimulationRequest
    from alpha_harness.forge.simulator import SimOutcome

SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1, "decay": 0}
LANES: dict[str, Any] = {
    "daily_limit": 100,
    "safety_margin": 0,
    "timezone": "UTC",
    "pacing": {"max_per_hour": 1000, "quiet_hours": []},
    "allocation": "fixed",
    "default_lane": "explore",
    "task_lanes": {},
    "lanes": {
        "explore": {"about": "probes", "share": 1.0, "bounds": [0.1, 1.0], "alpha_types": []}
    },
}

_counter = itertools.count()


def a_request() -> SimulationRequest:
    return request_for(f"rank(ts_delta(close, {next(_counter) + 2}))", SETTINGS)


@pytest.fixture(autouse=True)
def lanes_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    folder = tmp_path / "forge-config"
    folder.mkdir()
    monkeypatch.setenv("AH_FORGE_CONFIG_DIR", str(folder))
    (folder / "lanes.yaml").write_text(yaml.safe_dump(LANES))
    forge_config.reload()
    yield
    monkeypatch.undo()
    forge_config.reload()


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "harness.db")
    await database.create_all()
    yield database
    await database.dispose()


class ScriptedTracker:
    """Stands in for ``SimulationTracker``: real lifecycle transitions, no network.

    Each record id can be told in advance (``plan``) to land as COMPLETE with an alpha id, as
    an error status, or to never finish (for the timeout test) — everything else behaves like
    the real tracker's submit path (QUEUED -> PENDING -> RUNNING -> terminal).
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self.plan: dict[int, tuple[SimStatus, str | None]] = {}
        self.stalled: set[int] = set()

    async def submit(self, request: SimulationRequest, record_id: int) -> None:  # noqa: ARG002
        async with self.db.session() as session:
            if not await transition(
                session,
                record_id,
                from_=[SimStatus.QUEUED],
                status=SimStatus.PENDING,
                sent_at=utcnow(),
            ):
                raise SubmissionFailed("No longer queued.")
        await asyncio.sleep(0)
        if record_id in self.stalled:
            return  # left RUNNING forever, to exercise the adapter's timeout
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
    """The two ``BrainEndpoints`` methods ``BrainSimulator`` needs, stubbed."""

    def __init__(self) -> None:
        self.bodies: dict[str, dict[str, Any]] = {}
        self.pnl_rows: dict[str, list[dict[str, Any]]] = {}
        self.turnover_rows: dict[str, list[dict[str, Any]]] = {}
        self.yearly_rows: dict[str, list[dict[str, Any]]] = {}
        self.fetched: list[str] = []

    async def alpha_body(self, alpha_id: str) -> dict[str, Any]:
        self.fetched.append(alpha_id)
        if alpha_id not in self.bodies:
            raise RuntimeError(f"no fixture body for {alpha_id}")
        return self.bodies[alpha_id]

    async def get_recordset(self, alpha_id: str, name: str) -> Any:
        assert name in ("pnl", "turnover", "yearly-stats")
        by_name = {
            "pnl": self.pnl_rows,
            "turnover": self.turnover_rows,
            "yearly-stats": self.yearly_rows,
        }
        rows = by_name[name].get(alpha_id, [])

        class _RecordSet:
            def rows(self) -> list[dict[str, Any]]:
                return rows

        return _RecordSet()


async def no_session() -> bool:
    return False


async def wait_for_rows(db: Database, n: int) -> list[int]:
    """Spin until ``n`` simulation rows exist, as enqueue's own write takes a few loop turns."""
    ids: Sequence[int] = ()
    for _ in range(200):
        async with db.session() as session:
            ids = (
                await session.scalars(select(SimulationRecord.id).order_by(SimulationRecord.id))
            ).all()
        if len(ids) >= n:
            return list(ids)
        await asyncio.sleep(0)
    raise AssertionError(f"only {len(ids)} of {n} expected rows appeared")


def make_engine(db: Database) -> tuple[BatchEngine, ScriptedTracker]:
    tracker = ScriptedTracker(db)
    endpoints = object()  # the engine's own endpoints are never called: tracker stubs submission
    engine = BatchEngine(db, endpoints, tracker, on_unauthorized=no_session)  # pyright: ignore[reportArgumentType]
    engine.max_batch = 1
    engine.governor = QuotaGovernor(db)
    engine._last_orphan_sweep = float("inf")  # pyright: ignore[reportPrivateUsage]
    return engine, tracker


async def drain(engine: BatchEngine) -> None:
    while await engine._fill_slots():  # pyright: ignore[reportPrivateUsage]
        pass


async def test_simulate_returns_index_aligned_outcomes(db: Database) -> None:
    engine, tracker = make_engine(db)
    stub = StubEndpoints()
    stub.bodies["ALPHA_A"] = {"id": "ALPHA_A", "is": {"checks": []}, "train": {}, "test": {}}
    stub.bodies["ALPHA_B"] = {"id": "ALPHA_B", "is": {"checks": []}, "train": {}, "test": {}}
    adapter = BrainSimulator(engine, db, stub, lane="explore", poll_interval=0.01)

    good_1, bad, good_2 = a_request(), a_request(), a_request()

    async def run() -> list[SimOutcome]:
        return await adapter.simulate([good_1, bad, good_2])

    task = asyncio.create_task(run())
    # Let the requests reach QUEUED, mark the middle one as a BRAIN error, then dispatch.
    ids = await wait_for_rows(db, 3)
    tracker.plan[ids[0]] = (SimStatus.COMPLETE, "ALPHA_A")
    tracker.plan[ids[1]] = (SimStatus.ERROR, None)
    tracker.plan[ids[2]] = (SimStatus.COMPLETE, "ALPHA_B")
    await drain(engine)

    outcomes = await task
    assert [o.request for o in outcomes] == [good_1, bad, good_2]
    assert outcomes[0].ok and outcomes[0].alpha_id == "ALPHA_A"
    assert not outcomes[1].ok and outcomes[1].error is not None
    assert outcomes[2].ok and outcomes[2].alpha_id == "ALPHA_B"


async def test_a_cache_hit_is_not_polled(db: Database) -> None:
    from alpha_harness.db.models import DedupEntry
    from alpha_harness.engine.lifecycle import request_hash

    engine, _tracker = make_engine(db)
    stub = StubEndpoints()
    stub.bodies["CACHED"] = {"id": "CACHED", "is": {"checks": []}, "train": {}, "test": {}}
    adapter = BrainSimulator(engine, db, stub, lane="explore", poll_interval=0.01)

    request = a_request()
    async with db.session() as session:
        session.add(DedupEntry(request_hash=request_hash(request), alpha_id="CACHED"))

    [outcome] = await adapter.simulate([request])
    assert (outcome.cached, outcome.alpha_id, outcome.ok) == (True, "CACHED", True)


async def test_a_never_finishing_simulation_times_out(db: Database) -> None:
    engine, tracker = make_engine(db)
    adapter = BrainSimulator(
        engine, db, StubEndpoints(), lane="explore", poll_interval=0.01, timeout=0.05
    )

    request = a_request()

    async def run() -> list[SimOutcome]:
        return await adapter.simulate([request])

    task = asyncio.create_task(run())
    [record_id] = await wait_for_rows(db, 1)
    tracker.stalled.add(record_id)
    await drain(engine)

    [outcome] = await task
    assert not outcome.ok
    assert outcome.error is not None and "Timed out" in outcome.error


async def test_enqueues_tagged_with_the_given_lane(db: Database) -> None:
    engine, tracker = make_engine(db)
    stub = StubEndpoints()
    stub.bodies["ALPHA1"] = {"id": "ALPHA1", "is": {"checks": []}, "train": {}, "test": {}}
    adapter = BrainSimulator(engine, db, stub, lane="explore", poll_interval=0.01)

    request = a_request()
    task = asyncio.create_task(adapter.simulate([request]))
    [record_id] = await wait_for_rows(db, 1)
    tracker.plan[record_id] = (SimStatus.COMPLETE, "ALPHA1")
    await drain(engine)
    await task

    async with db.session() as session:
        [row] = (await session.scalars(select(SimulationRecord))).all()
    assert row.task == "forge.explore"


async def test_empty_request_list_returns_empty(db: Database) -> None:
    engine, _tracker = make_engine(db)
    adapter = BrainSimulator(engine, db, StubEndpoints(), lane="explore")
    assert await adapter.simulate([]) == []


async def test_pnl_reads_the_cumulative_recordset(db: Database) -> None:
    engine, _tracker = make_engine(db)
    stub = StubEndpoints()
    stub.pnl_rows["ALPHA1"] = [
        {"date": "2026-01-01", "pnl": 10.0},
        {"date": "2026-01-02", "pnl": 25.0},
    ]
    adapter = BrainSimulator(engine, db, stub, lane="explore")
    rows = await adapter.pnl("ALPHA1")
    assert rows == [(date(2026, 1, 1), 10.0), (date(2026, 1, 2), 15.0)]


async def test_turnover_wires_the_pnl_and_turnover_recordsets_through(db: Database) -> None:
    from alpha_harness.vault.metrics import daily_pnl, with_turnover

    engine, _tracker = make_engine(db)
    stub = StubEndpoints()
    stub.pnl_rows["ALPHA1"] = [
        {"date": "2026-01-01", "pnl": 10.0},
        {"date": "2026-01-02", "pnl": 25.0},
    ]
    stub.turnover_rows["ALPHA1"] = [
        {"date": "2026-01-01", "turnover": 1.0},
        {"date": "2026-01-02", "turnover": 0.5},
    ]
    adapter = BrainSimulator(engine, db, stub, lane="explore")
    rows = await adapter.turnover("ALPHA1")

    # Same shift/edge-case arithmetic vault.metrics.with_turnover already owns; this adapter
    # method only has to thread the two recordsets into it correctly.
    expected = with_turnover(daily_pnl(stub.pnl_rows["ALPHA1"]), stub.turnover_rows["ALPHA1"])
    assert rows == [(d, t) for d, _, t in expected]


async def test_yearly_stats_passes_the_recordset_rows_through(db: Database) -> None:
    engine, _tracker = make_engine(db)
    stub = StubEndpoints()
    stub.yearly_rows["ALPHA1"] = [{"year": "2025", "stage": "IS", "sharpe": 1.2}]
    adapter = BrainSimulator(engine, db, stub, lane="explore")
    assert await adapter.yearly_stats("ALPHA1") == [{"year": "2025", "stage": "IS", "sharpe": 1.2}]


def test_protocol_properties() -> None:
    adapter = BrainSimulator(engine=object(), db=object(), endpoints=object(), lane="explore")  # type: ignore[arg-type]
    assert (adapter.name, adapter.costs_quota, adapter.ephemeral, adapter.batch_size) == (
        "brain",
        True,
        False,
        10,
    )
