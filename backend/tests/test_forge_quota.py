"""The Quota Governor, offline: admission, pacing, the reset boundary, concurrency, cache hits.

Nothing here reaches BRAIN (CLAUDE.md rule 4). The engine tests drive alpha-harness's real
``BatchEngine`` on a throwaway SQLite database with a fake tracker standing in for the network:
it moves a row through PENDING -> RUNNING -> COMPLETE exactly as the real one does, calling the
same ``record_launch`` — which is where the ledger is charged.

The lanes come from a small ``lanes.yaml`` written per test (limit 100, margin 10), so every
number below can be checked by hand.
"""

import asyncio
import itertools
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
import pytest_asyncio
import yaml
from conftest import make_endpoints, route
from sqlalchemy import func, select

from alpha_harness.db.models import DedupEntry, QuotaSnapshot, SimStatus, SimulationRecord, utcnow
from alpha_harness.db.sqlite import Database
from alpha_harness.engine.lifecycle import (
    SubmissionFailed,
    new_record,
    record_launch,
    request_hash,
    transition,
)
from alpha_harness.engine.packer import WorkItem, key_of
from alpha_harness.engine.slots import BatchEngine
from alpha_harness.forge import config as forge_config
from alpha_harness.forge.quota import config as quota_config
from alpha_harness.forge.quota import ledger
from alpha_harness.forge.quota.governor import QuotaGovernor, effective_caps, fit_prefix
from alpha_harness.forge.quota.models import LaneState, QuotaLedger
from alpha_harness.forge.simulator import request_for
from alpha_harness.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from alpha_harness.brain.schemas import SimulationRequest

SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1, "decay": 0}

#: 2026-10-20 is in daylight time: BRAIN's day starts at 04:00 UTC (00:00 US Eastern).
DAY_START = datetime(2026, 10, 20, 4, 0, tzinfo=UTC)

LANES: dict[str, Any] = {
    "daily_limit": 100,
    "safety_margin": 10,
    "timezone": "Asia/Kolkata",
    "pacing": {"max_per_hour": 1000, "quiet_hours": []},
    "allocation": "fixed",
    "default_lane": "reserve",
    "task_lanes": {"sampler": "explore"},
    "lanes": {
        # budget = 100 - 10 = 90 -> explore 45, validate 27, reserve 18
        "explore": {"about": "probes", "share": 0.5, "bounds": [0.1, 0.6], "alpha_types": []},
        "validate": {"about": "checks", "share": 0.3, "bounds": [0.1, 0.4], "alpha_types": []},
        "reserve": {"about": "by hand", "share": 0.2, "bounds": [0.1, 0.3], "alpha_types": []},
    },
}


@pytest.fixture(autouse=True)
def lanes_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Point Forge at a scratch config dir; returns a writer for its ``lanes.yaml``."""
    folder = tmp_path / "forge-config"
    folder.mkdir()
    monkeypatch.setenv("AH_FORGE_CONFIG_DIR", str(folder))

    def write(**overrides: Any) -> None:
        (folder / "lanes.yaml").write_text(yaml.safe_dump({**LANES, **overrides}))
        forge_config.reload()

    write()
    yield write
    monkeypatch.undo()
    forge_config.reload()


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "harness.db")
    await database.create_all()
    yield database
    await database.dispose()


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


_counter = itertools.count()


def a_request() -> SimulationRequest:
    """A distinct request each call, so the dedup memory never matches by accident."""
    return request_for(f"rank(ts_delta(close, {next(_counter) + 2}))", SETTINGS)


async def queue(db: Database, task: str, n: int, *, sim_type: str | None = None) -> list[int]:
    async with db.session() as session:
        rows = [new_record(a_request(), task=task, status=SimStatus.QUEUED) for _ in range(n)]
        for row in rows:
            if sim_type:
                row.sim_type = sim_type
                row.payload = {**row.payload, "type": sim_type}
        session.add_all(rows)
        await session.flush()
        return [row.id for row in rows]


async def spend(db: Database, task: str, n: int, at: datetime) -> None:
    """``n`` simulations of ``task`` that BRAIN accepted at ``at``."""
    ids = await queue(db, task, n)
    async with db.session() as session:
        for record_id in ids:
            await transition(session, record_id, from_=[SimStatus.QUEUED], status=SimStatus.RUNNING)
        await ledger.record_spend(session, ids, now=at)


async def ledger_rows(db: Database) -> list[QuotaLedger]:
    async with db.session() as session:
        return list(await session.scalars(select(QuotaLedger).order_by(QuotaLedger.id)))


async def work_items(db: Database) -> list[WorkItem]:
    async with db.session() as session:
        rows = await session.scalars(
            select(SimulationRecord)
            .where(SimulationRecord.status == SimStatus.QUEUED)
            .order_by(SimulationRecord.id)
        )
        return [
            WorkItem(record_id=r.id, key=key_of(r.payload), task=r.task, payload=r.payload)
            for r in rows
        ]


# -- config ---------------------------------------------------------------------


def test_lane_of_reads_the_task_name() -> None:
    config = quota_config.load()
    assert config.lane_of("forge.validate") == "validate"
    assert config.lane_of("forge.validate.court") == "validate"
    assert config.lane_of("sampler") == "explore"  # task_lanes
    # Every existing lab screen and manual run lands in reserve.
    assert config.lane_of("manual") == "reserve"
    assert config.lane_of("template_lab:42") == "reserve"
    assert config.lane_of("forge.no_such_lane") == "reserve"


def test_config_refuses_bandit_and_shares_that_do_not_add_up() -> None:
    with pytest.raises(ValueError, match="bandit"):
        quota_config.parse({**LANES, "allocation": "bandit"})
    lanes = {**LANES["lanes"], "reserve": {"share": 0.5}}
    with pytest.raises(ValueError, match="sum to"):
        quota_config.parse({**LANES, "lanes": lanes})
    with pytest.raises(ValueError, match="not defined"):
        quota_config.parse({**LANES, "default_lane": "nowhere"})


def test_the_repo_lanes_yaml_is_valid() -> None:
    config = quota_config.parse(
        forge_config.read_yaml(forge_config.DEFAULT_CONFIG_DIR / "lanes.yaml")
    )
    assert config.default_lane == "reserve"
    assert all(lane.about for lane in config.lanes.values())


def test_quiet_hours_wrap_midnight() -> None:
    window = quota_config._quiet("22:00-06:00")  # pyright: ignore[reportPrivateUsage]
    assert window.contains(time(23, 30))
    assert window.contains(time(5, 59))
    assert not window.contains(time(6, 0))
    assert not window.contains(time(12, 0))


# -- linear release (pure) ------------------------------------------------------


PLANNED = {"explore": 45.0, "validate": 27.0, "reserve": 18.0}


def test_at_the_start_of_the_day_each_lane_has_exactly_its_share() -> None:
    assert effective_caps(90, PLANNED, {}, {}, 0.0) == PLANNED


def test_idle_shares_are_released_as_the_day_passes() -> None:
    # Halfway: explore and validate each still protect half their share.
    assert effective_caps(90, PLANNED, {}, {}, 0.5)["reserve"] == 90 - 22.5 - 13.5
    # At the end of the day an idle day's whole budget is open to the busy lane.
    assert effective_caps(90, PLANNED, {}, {}, 1.0)["reserve"] == 90


def test_queued_work_keeps_its_claim_but_only_up_to_its_share() -> None:
    caps = effective_caps(90, PLANNED, {}, {"explore": 1000}, 1.0)
    assert caps["reserve"] == 90 - 45  # explore's queue protects its whole share, no more


@pytest.mark.parametrize("elapsed", [0.0, 0.3, 0.7, 1.0])
def test_lanes_can_never_jointly_send_past_the_budget(elapsed: float) -> None:
    # Greedy worst case: every lane in turn spends all its room.
    spent: dict[str, int] = {}
    for lane in PLANNED:
        caps = effective_caps(90, PLANNED, spent, {}, elapsed)
        spent[lane] = int(caps[lane])
    assert sum(spent.values()) <= 90


def test_fit_prefix_trims_the_tail() -> None:
    assert fit_prefix([1, 1, 1, 1], 2.5) == (2, 2)
    assert fit_prefix([4, 4, 4], 9) == (2, 8)
    assert fit_prefix([], 10) == (0, 0)


# -- admission ------------------------------------------------------------------


async def test_admission_trims_a_plan_to_its_lane(db: Database) -> None:
    governor = QuotaGovernor(db, clock=Clock(DAY_START + timedelta(seconds=1)))
    admission = await governor.admit("reserve", [1] * 30)
    assert (admission.admitted, admission.trimmed) == (18, 12)
    assert "reserve" in admission.reason

    # Work already queued in the lane counts against what a new plan may take.
    await queue(db, "manual", 5)
    assert (await governor.admit("reserve", [1] * 30)).admitted == 13


async def test_admission_never_passes_the_daily_budget(lanes_yaml: Any, db: Database) -> None:
    # One lane holding the whole day, so the day's budget (not a share) is what binds.
    lanes_yaml(lanes={"reserve": {"about": "all", "share": 1.0}}, task_lanes={})
    governor = QuotaGovernor(db, clock=Clock(DAY_START + timedelta(hours=12)))
    await spend(db, "manual", 20, DAY_START + timedelta(hours=1))
    admission = await governor.admit("reserve", [1] * 500)
    assert admission.admitted == 90 - 20
    assert admission.global_remaining == 70


async def test_region_agnostic_costs_four(db: Database) -> None:
    governor = QuotaGovernor(db, clock=Clock(DAY_START + timedelta(seconds=1)))
    admission = await governor.admit("reserve", [4] * 10)
    assert (admission.admitted, admission.admitted_cost) == (4, 16)  # 18 // 4

    ids = await queue(db, "manual", 1, sim_type="REGION_AGNOSTIC")
    async with db.session() as session:
        assert await ledger.record_spend(session, ids, now=DAY_START + timedelta(minutes=5)) == 4


# -- pacing ---------------------------------------------------------------------


async def test_pacing_caps_the_rolling_hour(lanes_yaml: Any, db: Database) -> None:
    lanes_yaml(pacing={"max_per_hour": 5, "quiet_hours": []})
    now = DAY_START + timedelta(hours=20)
    await spend(db, "manual", 3, now - timedelta(minutes=30))  # inside the hour
    await spend(db, "manual", 4, now - timedelta(hours=2))  # outside it
    await queue(db, "manual", 10)
    governor = QuotaGovernor(db, clock=Clock(now))
    allowed = await governor.pace(await work_items(db))
    assert len(allowed) == 2  # 5 per hour - 3 in the last 60 minutes


async def test_pacing_holds_a_lane_at_its_room(db: Database) -> None:
    await queue(db, "manual", 30)
    governor = QuotaGovernor(db, clock=Clock(DAY_START + timedelta(seconds=1)))
    assert len(await governor.pace(await work_items(db))) == 18


@pytest.mark.parametrize(
    ("ist", "sends"),
    [("23:30", False), ("05:59", False), ("06:00", True), ("14:00", True)],
)
async def test_quiet_hours_send_nothing(
    lanes_yaml: Any, db: Database, ist: str, sends: bool
) -> None:
    lanes_yaml(pacing={"max_per_hour": 1000, "quiet_hours": ["22:00-06:00"]})
    await queue(db, "manual", 3)
    hour, minute = map(int, ist.split(":"))
    # Asia/Kolkata is UTC+05:30 all year.
    at = datetime(2026, 10, 20, hour, minute, tzinfo=UTC) - timedelta(hours=5, minutes=30)
    governor = QuotaGovernor(db, clock=Clock(at))
    assert bool(await governor.pace(await work_items(db))) is sends


# -- reset boundary -------------------------------------------------------------


async def test_spend_resets_at_midnight_eastern(db: Database) -> None:
    await spend(db, "manual", 1, DAY_START - timedelta(minutes=1))  # 23:59 ET, yesterday
    await spend(db, "manual", 1, DAY_START + timedelta(minutes=1))  # 00:01 ET, today
    rows = await ledger_rows(db)
    assert [r.day for r in rows] == ["2026-10-19", "2026-10-20"]

    today = await QuotaGovernor(db, clock=Clock(DAY_START + timedelta(minutes=30))).state()
    assert today.spent == 1
    assert today.lanes["reserve"].spent == 1
    assert today.resets_in_seconds == pytest.approx(timedelta(hours=23, minutes=30).total_seconds())


async def test_a_25_hour_day_still_ends_at_midnight_eastern(db: Database) -> None:
    # 2026-11-01: clocks go back, so BRAIN's day is 25 hours long.
    start = datetime(2026, 11, 1, 4, 0, tzinfo=UTC)
    state = await QuotaGovernor(db, clock=Clock(start)).state()
    assert state.day == "2026-11-01"
    assert state.resets_in_seconds == timedelta(hours=25).total_seconds()


async def test_the_lower_remaining_wins(db: Database) -> None:
    now = DAY_START + timedelta(hours=3)
    await spend(db, "forge.validate", 5, now - timedelta(hours=1))
    async with db.session() as session:
        # Yesterday's reading says almost nothing is left; it is about another day.
        session.add(
            QuotaSnapshot(
                limit_total=100, remaining=1, observed_at=DAY_START - timedelta(minutes=5)
            )
        )
    state = await QuotaGovernor(db, clock=Clock(now)).state()
    assert (state.exact, state.spent) == (False, 5)

    async with db.session() as session:
        # Today BRAIN says 40 are used (sims run on the website): more than the ledger knows.
        session.add(QuotaSnapshot(limit_total=100, remaining=60, observed_at=now))
    state = await QuotaGovernor(db, clock=Clock(now)).state()
    assert state.exact is True
    assert (state.spent, state.unrecorded) == (40, 35)
    assert state.lanes["validate"].spent == 5
    assert state.lanes["reserve"].spent == 35  # what nobody here sent was done by hand

    async with db.session() as session:
        # A lower limit from BRAIN shrinks the budget too.
        session.add(
            QuotaSnapshot(limit_total=80, remaining=70, observed_at=now + timedelta(seconds=1))
        )
    state = await QuotaGovernor(db, clock=Clock(now)).state()
    assert (state.limit, state.budget, state.spent) == (80, 70, 10)


async def test_margin_warning(db: Database) -> None:
    now = DAY_START + timedelta(hours=23)
    governor = QuotaGovernor(db, clock=Clock(now))
    assert not (await governor.state()).margin_warning
    await spend(db, "manual", 85, now - timedelta(hours=1))
    assert not (await governor.state()).margin_warning  # 15 left: still outside the margin
    await queue(db, "manual", 5)
    assert (await governor.state()).margin_warning  # queued work reaches into the last 10


# -- the real engine, with a fake network ----------------------------------------


class FakeTracker:
    """Stands in for ``SimulationTracker``: no network, same lifecycle, same ``record_launch``."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.sent = 0

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
        await asyncio.sleep(0)  # the POST: let other coroutines in, as the network would
        async with self.db.session() as session:
            await record_launch(session, record_id, f"FAKE{record_id}", None)
        async with self.db.session() as session:
            await transition(
                session,
                record_id,
                from_=[SimStatus.RUNNING],
                status=SimStatus.COMPLETE,
                finished_at=utcnow(),
            )
        self.sent += 1

    async def notify(self) -> None:
        return None


async def no_session() -> bool:
    return False


def make_engine(
    db: Database, clock: Callable[[], datetime] = utcnow
) -> tuple[BatchEngine, FakeTracker]:
    endpoints, _ = make_endpoints(route({}))  # any request to BRAIN fails the test
    tracker = FakeTracker(db)
    engine = BatchEngine(db, endpoints, tracker, on_unauthorized=no_session)  # pyright: ignore[reportArgumentType]
    engine.max_batch = 1  # single sends: the batch path would need BRAIN's multi-sim endpoint
    engine.governor = QuotaGovernor(db, clock=clock)
    # The orphan sweep cancels queued work whose task has no lab study; these tests queue
    # under bare task names on purpose, so the sweep is kept out of the way.
    engine._last_orphan_sweep = float("inf")  # pyright: ignore[reportPrivateUsage]
    return engine, tracker


async def drain(engine: BatchEngine) -> None:
    """Run dispatch rounds, as the engine's loop does, until a round sends nothing."""
    while await engine._fill_slots():  # pyright: ignore[reportPrivateUsage]
        pass


async def test_concurrent_enqueues_never_exceed_the_budget(lanes_yaml: Any, db: Database) -> None:
    # One lane with the whole day, so the budget (not a share) is what binds, at any hour.
    lanes_yaml(lanes={"reserve": {"about": "all", "share": 1.0, "alpha_types": []}}, task_lanes={})
    engine, tracker = make_engine(db)

    async def producer() -> None:
        await engine.enqueue([a_request() for _ in range(10)], task="manual")

    async def dispatcher() -> None:
        for _ in range(30):
            await drain(engine)
            await asyncio.sleep(0)

    await asyncio.gather(*(producer() for _ in range(15)), dispatcher())
    await drain(engine)

    spent = sum(r.cost for r in await ledger_rows(db))
    assert spent == tracker.sent == 90  # 150 asked; the budget is 100 - 10
    async with db.session() as session:
        held = await session.scalar(
            select(func.count()).where(SimulationRecord.status == SimStatus.QUEUED)
        )
    assert held == 60  # held for tomorrow, not rejected


async def test_cache_hits_cost_nothing(db: Database) -> None:
    engine, tracker = make_engine(db)
    request = a_request()
    async with db.session() as session:
        session.add(DedupEntry(request_hash=request_hash(request), alpha_id="ALPHA1"))

    result = await engine.enqueue([request], task="manual")
    assert result["queued"] == []
    await drain(engine)

    [row] = await ledger_rows(db)
    assert (row.cached, row.cost, row.alpha_id, row.lane) == (True, 0, "ALPHA1", "reserve")
    state = await engine.governor.state()  # pyright: ignore[reportOptionalMemberAccess]
    assert (state.spent, state.cached, tracker.sent) == (0, 1, 0)


async def test_manual_and_lab_sims_land_in_reserve(db: Database) -> None:
    engine, _ = make_engine(db)
    await engine.enqueue([a_request()], task="manual")
    await engine.enqueue([a_request()], task="template_lab:7")
    await engine.enqueue([a_request()], task="forge.validate")
    await drain(engine)

    assert [(r.task, r.lane, r.cost) for r in await ledger_rows(db)] == [
        ("manual", "reserve", 1),
        ("template_lab:7", "reserve", 1),
        ("forge.validate", "validate", 1),
    ]


async def test_a_relaunch_is_not_charged_twice(db: Database) -> None:
    [record_id] = await queue(db, "manual", 1)
    async with db.session() as session:
        await ledger.record_spend(session, [record_id])
        await ledger.record_spend(session, [record_id])
    assert len(await ledger_rows(db)) == 1


# -- the route ------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(db: Database) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app()
    governor = QuotaGovernor(db, clock=Clock(DAY_START + timedelta(hours=2)))
    app.state.harness = SimpleNamespace(engine=SimpleNamespace(governor=governor))
    await spend(db, "manual", 3, DAY_START + timedelta(hours=1))
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


async def test_quota_route(client: httpx2.AsyncClient, db: Database) -> None:
    res = await client.get("/api/forge/quota", headers={"x-harness-client": "1"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["day"], body["limit"], body["budget"], body["spent"]) == ("2026-10-20", 100, 90, 3)
    assert {"safetyMargin", "marginWarning", "resetsInSeconds", "brainRemaining"} <= set(body)
    reserve = next(lane for lane in body["lanes"] if lane["lane"] == "reserve")
    assert (reserve["planned"], reserve["spent"], reserve["about"]) == (18, 3, "by hand")
    assert len(body["hourly"]) == 24
    # BRAIN's day starts at 09:30 in India; the chart starts at the hour that contains it.
    assert body["hourly"][0]["hour"] == "09:00"
    assert sum(h["spent"] for h in body["hourly"]) == 3
    async with db.session() as session:
        assert await session.scalar(select(func.count()).select_from(LaneState)) == 3


async def test_estimate_route(client: httpx2.AsyncClient) -> None:
    headers = {"x-harness-client": "1"}
    res = await client.post(
        "/api/forge/quota/estimate", json={"lane": "reserve", "simulations": 40}, headers=headers
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # Two hours in: reserve's 18 plus 1/12 of the other lanes' idle 72 released = 24; 3 spent.
    assert (body["admitted"], body["trimmed"], body["laneRemaining"]) == (21, 19, 21)

    res = await client.post(
        "/api/forge/quota/estimate", json={"lane": "nowhere", "simulations": 1}, headers=headers
    )
    assert res.status_code == 422
