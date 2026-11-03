"""``/api/forge/agent``: proposal -> approve -> real run, forced into the ``reserve`` lane
regardless of which pipeline was proposed (CLAUDE.md rule 5). Reuses the pipelines route's
harness (real SQLite, a real ``BatchEngine`` and Quota Governor, a scripted tracker in place of
BRAIN) exactly as ``test_forge_board_api.py`` does, so nothing here spends a real simulation.
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
    make_template,
    wait_for_rows,
)

from alpha_harness.forge.agent.tools import TOOL_SCHEMAS

if TYPE_CHECKING:
    import httpx2


async def test_tools_route_lists_the_same_schemas_the_toolbox_dispatches(
    client: httpx2.AsyncClient,  # noqa: F811
) -> None:
    res = await client.get("/api/forge/agent/tools")
    assert res.status_code == 200, res.text
    names = {t["name"] for t in res.json()}
    assert names == {s["name"] for s in TOOL_SCHEMAS}
    assert "submit" not in " ".join(names).lower()


async def test_hypothesis_and_lesson_round_trip(client: httpx2.AsyncClient) -> None:  # noqa: F811
    created = await client.post(
        "/api/forge/agent/hypotheses",
        json={"title": "reversal", "mechanism": "overreaction mean-reverts"},
        headers=CLIENT,
    )
    assert created.status_code == 200, created.text
    listed = await client.get("/api/forge/agent/hypotheses")
    assert listed.json()[0]["title"] == "reversal"

    lesson = await client.post(
        "/api/forge/agent/lessons",
        json={"text": "watch turnover", "tags": ["turnover"]},
        headers=CLIENT,
    )
    assert lesson.status_code == 200, lesson.text
    lessons = await client.get("/api/forge/agent/lessons")
    assert lessons.json()[0]["text"] == "watch turnover"


async def test_approving_a_proposal_runs_it_in_the_reserve_lane_not_the_pipelines_own_lane(
    client: httpx2.AsyncClient,  # noqa: F811
    harness: Harness,  # noqa: F811
) -> None:
    await make_template(harness)
    field_set_id = await make_field_set(harness)

    proposed = await client.post(
        "/api/forge/agent/proposals",
        json={
            "pipeline": "regular",
            "fieldSetId": field_set_id,
            "templateIds": ["pipeline_test_simple"],
            "budget": 2,
            "rationale": "cheap probe of the idea",
        },
        headers=CLIENT,
    )
    assert proposed.status_code == 200, proposed.text
    proposal_id = proposed.json()["id"]
    assert proposed.json()["status"] == "pending"

    approved = await client.post(
        f"/api/forge/agent/proposals/{proposal_id}/approve", headers=CLIENT
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    # The run happens in the background, same as POST /api/forge/pipelines/run — drive the
    # fake BRAIN edge until it finishes.
    for _ in range(50):
        await wait_for_rows(harness.db, 1)
        await drain(harness.engine)
        await asyncio.sleep(0)
        got = (await client.get("/api/forge/agent/proposals")).json()
        row = next(p for p in got if p["id"] == proposal_id)
        if row["status"] in ("executed", "failed"):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("proposal never finished executing")

    assert row["status"] == "executed", row
    assert row["runId"]

    runs = await client.get("/api/forge/agent/runs")
    run = next(r for r in runs.json() if r["runId"] == row["runId"])
    # "regular"'s own pipeline config pays from "forge_regular" (config/forge/pipelines/
    # regular.yaml); an agent-approved run must pay from "reserve" regardless.
    assert run["lane"] == "reserve"
    assert run["status"] == "done"


async def test_approving_twice_is_refused(
    client: httpx2.AsyncClient,  # noqa: F811
    harness: Harness,  # noqa: F811
) -> None:
    await make_template(harness)
    field_set_id = await make_field_set(harness)
    proposed = await client.post(
        "/api/forge/agent/proposals",
        json={
            "pipeline": "regular",
            "fieldSetId": field_set_id,
            "templateIds": ["pipeline_test_simple"],
            "budget": 1,
            "rationale": "probe",
        },
        headers=CLIENT,
    )
    proposal_id = proposed.json()["id"]
    first = await client.post(f"/api/forge/agent/proposals/{proposal_id}/approve", headers=CLIENT)
    assert first.status_code == 200, first.text
    second = await client.post(f"/api/forge/agent/proposals/{proposal_id}/approve", headers=CLIENT)
    assert second.status_code == 409


async def test_rejecting_a_proposal_never_runs_it(
    client: httpx2.AsyncClient,  # noqa: F811
    harness: Harness,  # noqa: F811
) -> None:
    await make_template(harness)
    field_set_id = await make_field_set(harness)
    proposed = await client.post(
        "/api/forge/agent/proposals",
        json={
            "pipeline": "regular",
            "fieldSetId": field_set_id,
            "templateIds": ["pipeline_test_simple"],
            "budget": 1,
            "rationale": "probe",
        },
        headers=CLIENT,
    )
    proposal_id = proposed.json()["id"]
    rejected = await client.post(f"/api/forge/agent/proposals/{proposal_id}/reject", headers=CLIENT)
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    runs = await client.get("/api/forge/agent/runs")
    assert runs.json() == []
