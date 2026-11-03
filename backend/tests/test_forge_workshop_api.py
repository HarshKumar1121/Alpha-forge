"""``/api/forge/workshop``: evidence (free), a Court trial (spends the ``validate`` lane
through a fake engine, never real BRAIN), and promotion -> approve -> real run forced into
``reserve`` (CLAUDE.md rules 3-5). Reuses ``test_forge_pipelines_api.py``'s harness, same as
``test_forge_agent_api.py`` does.
"""

import asyncio
from typing import TYPE_CHECKING

import pytest
from test_forge_pipelines_api import (
    CLIENT,
    Harness,
    client,  # noqa: F401  # pyright: ignore[reportUnusedImport] - fixture
    drain,
    harness,  # noqa: F401  # pyright: ignore[reportUnusedImport] - fixture
    make_field_set,
)

from alpha_harness.forge.board.models import PipelineAlpha
from alpha_harness.forge.workshop import trial as workshop_trial

if TYPE_CHECKING:
    import httpx2


@pytest.fixture(autouse=True)
def _fast_simulator(monkeypatch: pytest.MonkeyPatch) -> None:
    """``forge/workshop/trial.py`` imports ``BrainSimulator`` itself (never through
    ``api/forge_pipelines.py``), so the pipelines test harness's own monkeypatch of that
    module's attribute does not reach it — patch this module's copy of the name instead, to
    the same fast-polling adapter the pipelines tests use."""

    def fast(engine: object, db: object, endpoints: object, lane: str) -> object:
        from alpha_harness.forge.pipelines.brain_adapter import BrainSimulator

        return BrainSimulator(engine, db, endpoints, lane, poll_interval=0.01)

    monkeypatch.setattr(workshop_trial, "BrainSimulator", fast)


async def test_evidence_reads_prior_runs_without_simulating_anything(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    async with harness.db.session() as session:
        session.add_all(
            [
                PipelineAlpha(
                    alpha_id="A1",
                    pipeline="regular",
                    alpha_type="regular",
                    expression="rank(news_sentiment)",
                    field="news_sentiment",
                    sim_checks=[{"name": "HIGH_TURNOVER", "result": "FAIL"}],
                ),
                PipelineAlpha(
                    alpha_id="A2",
                    pipeline="regular",
                    alpha_type="regular",
                    expression="rank(news_sentiment)",
                    field="news_sentiment",
                    sim_checks=[{"name": "HIGH_TURNOVER", "result": "FAIL"}],
                ),
            ]
        )
    res = await client.post(
        "/api/forge/workshop/evidence",
        json={"pipeline": "regular", "fields": ["news_sentiment"]},
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["sampleSize"] == 2
    assert body["findings"][0]["check"] == "HIGH_TURNOVER"
    assert body["findings"][0]["count"] == 2


async def _run_trial_to_completion(
    client: httpx2.AsyncClient,  # noqa: F811
    harness: Harness,  # noqa: F811
    field_set_id: int,
) -> dict:
    started = await client.post(
        "/api/forge/workshop/trials",
        json={
            "title": "reversal",
            "mechanism": "overreaction mean-reverts",
            "pipeline": "regular",
            "fieldSetId": field_set_id,
            "fields": ["close"],
            "testBudget": 2,
        },
        headers=CLIENT,
    )
    assert started.status_code == 200, started.text
    trial_id = started.json()["id"]
    assert started.json()["status"] == "running"
    assert started.json()["lane"] == "validate"

    for _ in range(200):
        await drain(harness.engine)
        await asyncio.sleep(0)
        got = await client.get(f"/api/forge/workshop/trials/{trial_id}")
        if got.json()["status"] in ("done", "failed"):
            return got.json()
        await asyncio.sleep(0.01)
    pytest.fail("trial never finished")


async def test_a_trial_runs_in_the_validate_lane_and_records_a_verdict(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    field_set_id = await make_field_set(harness)
    finished = await _run_trial_to_completion(client, harness, field_set_id)
    assert finished["status"] == "done", finished
    assert finished["verdict"] in ("SURVIVES", "WOUNDED", "REJECTED")
    assert finished["case"] is not None

    # The simulations it spent were tagged for the validate lane, not forge_regular.
    async with harness.db.session() as session:
        from sqlalchemy import select

        from alpha_harness.db.models import SimulationRecord

        tasks = {r for (r,) in (await session.execute(select(SimulationRecord.task))).all()}
    assert any(t.startswith("forge.validate") for t in tasks)


async def test_promoting_with_no_survivors_is_refused(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    field_set_id = await make_field_set(harness)
    finished = await _run_trial_to_completion(client, harness, field_set_id)
    if finished["verdict"] == "SURVIVES":
        pytest.skip("this seed happened to survive; the refusal path is covered elsewhere")
    res = await client.post(
        f"/api/forge/workshop/trials/{finished['id']}/promote",
        json={"budget": 5, "rationale": "try it anyway"},
        headers=CLIENT,
    )
    assert res.status_code == 422


async def test_promoting_survivors_and_approving_runs_in_the_reserve_lane(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    field_set_id = await make_field_set(harness)
    finished = await _run_trial_to_completion(client, harness, field_set_id)

    promote = await client.post(
        f"/api/forge/workshop/trials/{finished['id']}/promote",
        json={"budget": 2, "rationale": "survived the Court", "force": True},
        headers=CLIENT,
    )
    assert promote.status_code == 200, promote.text
    proposal_id = promote.json()["proposal_id"]
    assert promote.json()["status"] == "pending"

    approved = await client.post(
        f"/api/forge/agent/proposals/{proposal_id}/approve", headers=CLIENT
    )
    assert approved.status_code == 200, approved.text

    for _ in range(200):
        await drain(harness.engine)
        await asyncio.sleep(0)
        got = (await client.get("/api/forge/agent/proposals")).json()
        row = next(p for p in got if p["id"] == proposal_id)
        if row["status"] in ("executed", "failed"):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("promotion never finished executing")

    assert row["status"] == "executed", row
    runs = await client.get("/api/forge/agent/runs")
    run = next(r for r in runs.json() if r["runId"] == row["runId"])
    assert run["lane"] == "reserve"


async def test_promoting_twice_is_refused(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    field_set_id = await make_field_set(harness)
    finished = await _run_trial_to_completion(client, harness, field_set_id)
    first = await client.post(
        f"/api/forge/workshop/trials/{finished['id']}/promote",
        json={"budget": 2, "rationale": "a", "force": True},
        headers=CLIENT,
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"/api/forge/workshop/trials/{finished['id']}/promote",
        json={"budget": 2, "rationale": "b", "force": True},
        headers=CLIENT,
    )
    assert second.status_code == 409
