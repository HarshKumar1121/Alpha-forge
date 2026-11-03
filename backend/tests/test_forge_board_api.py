"""``/api/forge/board``: the board from kept answers, the free fetch, and queueing a repair.

Reuses the pipelines route's harness: real SQLite, a real ``BatchEngine`` and Quota Governor,
and a scripted tracker in place of BRAIN, so nothing spends a real simulation (CLAUDE.md rule 4).
"""

import asyncio
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from sqlalchemy import func, select
from test_forge_pipelines_api import (
    CLIENT,
    Harness,
    StubEndpoints,
    drain,
    harness,  # noqa: F401  # pyright: ignore[reportUnusedImport] - fixture
)

from alpha_harness.api import forge_board
from alpha_harness.db.models import BrainCache, SimulationRecord, utcnow
from alpha_harness.forge.board.models import PipelineAlpha
from alpha_harness.forge.pipelines.brain_adapter import BrainSimulator
from alpha_harness.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

SETTINGS = {
    "instrumentType": "EQUITY",
    "region": "USA",
    "universe": "TOP3000",
    "delay": 1,
    "decay": 0,
    "neutralization": "SUBINDUSTRY",
    "truncation": 0.08,
}
FAIL_FITNESS = [
    {"name": "LOW_SHARPE", "result": "PASS"},
    {"name": "LOW_FITNESS", "result": "FAIL", "value": 0.8, "limit": 1.0},
]
PASSING = [{"name": "LOW_SHARPE", "result": "PASS"}, {"name": "SELF_CORRELATION", "result": "PASS"}]


def pnl_body(seed: int) -> dict[str, Any]:
    """A cumulative ``pnl`` recordset as BRAIN sends it: schema plus positional records."""
    import numpy as np
    import pandas as pd

    days = pd.bdate_range("2022-01-03", periods=300)
    cum = np.cumsum(np.random.default_rng(seed).normal(0, 1, len(days)))
    return {
        "schema": {"properties": [{"name": "date"}, {"name": "pnl"}]},
        "records": [[d.date().isoformat(), float(v)] for d, v in zip(days, cum, strict=True)],
    }


class BoardEndpoints(StubEndpoints):
    def __init__(self) -> None:
        super().__init__()
        self.fetched: list[str] = []

    async def recordset_body(self, alpha_id: str, name: str) -> dict[str, Any]:
        self.fetched.append(f"{name}:{alpha_id}")
        return pnl_body(int(alpha_id.removeprefix("A")))

    async def check_alpha(self, alpha_id: str) -> dict[str, Any]:
        self.checked.append(alpha_id)
        return {"is": {"checks": PASSING}}

    async def before_and_after(self, alpha_id: str) -> dict[str, Any]:
        self.fetched.append(f"performance:{alpha_id}")
        return {"score": {"before": 100, "after": 110}}


@pytest.fixture
async def client(
    harness: Harness,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx2.AsyncClient]:
    def fast_simulator(engine: Any, db: Any, endpoints: Any, lane: str) -> BrainSimulator:
        return BrainSimulator(engine, db, endpoints, lane, poll_interval=0.01)

    monkeypatch.setattr(forge_board, "BrainSimulator", fast_simulator)
    harness.endpoints = BoardEndpoints()
    app = create_app()
    app.state.harness = harness
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://localhost", headers=CLIENT
    ) as c:
        yield c


async def seed(h: Harness, *rows: tuple[str, float, list[dict[str, Any]]]) -> None:
    async with h.db.session() as session:
        session.add_all(
            PipelineAlpha(
                alpha_id=alpha_id,
                pipeline="regular",
                alpha_type="regular",
                region="USA",
                expression="rank(close)",
                settings=SETTINGS,
                field="close",
                template_id="t1",
                stage="r1",
                train_sharpe=sharpe,
                turnover=0.4,
                sim_checks=checks,
                created_at=utcnow(),
            )
            for alpha_id, sharpe, checks in rows
        )


def stub(h: Harness) -> BoardEndpoints:
    assert isinstance(h.endpoints, BoardEndpoints)
    return h.endpoints


def leaders(board: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        f["leader"]["alphaId"]: f["leader"]
        for d in board["days"]
        for t in d["types"]
        for f in t["families"]
    }


async def sims(h: Harness) -> int:
    async with h.db.session() as session:
        return int(await session.scalar(select(func.count()).select_from(SimulationRecord)) or 0)


async def wait_task(h: Harness, task_id: str) -> dict[str, Any]:
    for _ in range(500):
        await drain(h.engine)
        task = next(t for t in h.tasks.list_tasks() if t["id"] == task_id)
        if task["state"] != "running":
            return task
        await asyncio.sleep(0.01)
    raise AssertionError("task never finished")


async def test_board_reads_only_what_is_kept(client: httpx2.AsyncClient, harness: Harness) -> None:  # noqa: F811
    await seed(harness, ("A1", 1.5, FAIL_FITNESS), ("A2", 1.2, []))
    async with harness.db.session() as session:
        await session.merge(
            BrainCache(key="check:A2", body={"is": {"checks": PASSING}}, fetched_at=utcnow())
        )

    res = await client.get("/api/forge/board")
    assert res.status_code == 200, res.text
    board = res.json()
    # No PnL yet: same template on the same field is one structural family, led by A1.
    (lead,) = leaders(board).values()
    assert lead["alphaId"] == "A1"
    assert lead["status"] == "not_ready"
    assert lead["repairs"][0]["key"] == "LOW_FITNESS_FAST"
    assert board["missing"]["pnl"] == 2
    assert stub(harness).checked == []
    assert stub(harness).fetched == []


async def test_fetch_fills_pnl_then_leaders_without_spending_a_simulation(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    await seed(harness, ("A1", 1.5, FAIL_FITNESS), ("A2", 1.2, []))
    res = await client.post("/api/forge/board/fetch", json={"days": 2})
    assert res.status_code == 200, res.text
    task = await wait_task(harness, res.json()["taskId"])
    assert task["state"] == "done", task

    board = (await client.get("/api/forge/board")).json()
    # Independent PnL: two families, each leader checked, correlated and scored.
    found = leaders(board)
    assert set(found) == {"A1", "A2"}
    assert all(a["checksSource"] == "check" for a in found.values())
    assert all(a["status"] == "ready" for a in found.values())
    assert board["missing"] == {"pnl": 0, "checks": 0, "correlations": 0, "performance": 0}
    assert [i["alphaId"] for i in board["checklist"]] == ["A1", "A2"]
    assert await sims(harness) == 0


async def test_fetch_refuses_a_second_concurrent_fetch(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    await harness.tasks.start(forge_board.FETCH_KIND, "already running")
    res = await client.post("/api/forge/board/fetch", json={"days": 2})
    assert res.status_code == 409


async def test_queue_repair_spends_one_validate_sim_and_lands_on_the_board(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    await seed(harness, ("A1", 1.5, FAIL_FITNESS))
    res = await client.post("/api/forge/board/repair", json={"alphaId": "A1"})
    assert res.status_code == 200, res.text
    queued = res.json()
    assert (queued["repair"], queued["lane"]) == ("LOW_FITNESS_FAST", "validate")
    assert queued["settings"]["decay"] == 6
    task = await wait_task(harness, queued["taskId"])
    assert task["state"] == "done", task

    async with harness.db.session() as session:
        records = (await session.scalars(select(SimulationRecord))).all()
        repaired = (
            await session.scalars(select(PipelineAlpha).where(PipelineAlpha.stage == "repair"))
        ).all()
        source = (
            await session.scalars(select(PipelineAlpha).where(PipelineAlpha.alpha_id == "A1"))
        ).one()
    assert [r.task for r in records] == ["forge.validate"]
    assert len(repaired) == 1
    assert repaired[0].template_id == "t1+low_fitness_fast"
    assert source.repairs_queued == 1


async def test_repair_refuses_when_no_court_repair_answers_the_failure(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    await seed(harness, ("A1", 1.5, [{"name": "LOW_SHARPE", "result": "FAIL"}]))
    res = await client.post("/api/forge/board/repair", json={"alphaId": "A1"})
    assert res.status_code == 422
    assert await sims(harness) == 0


async def test_repair_404s_for_an_alpha_not_on_the_board(client: httpx2.AsyncClient) -> None:
    res = await client.post("/api/forge/board/repair", json={"alphaId": "NOPE"})
    assert res.status_code == 404
