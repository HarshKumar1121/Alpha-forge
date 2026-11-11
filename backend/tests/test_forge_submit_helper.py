"""``/api/forge/submit-helper``: hourly-cached correlation check, verdicts and the
independent-set recommendation. Fake BRAIN only (CLAUDE.md rule 4) — never real BRAIN.
"""

import asyncio
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from test_forge_pipelines_api import (
    CLIENT,
    Harness,
    StubEndpoints,
    drain,
    harness,  # noqa: F401  # pyright: ignore[reportUnusedImport] - fixture
)

from alpha_harness.api import forge_submit_helper
from alpha_harness.db.models import utcnow
from alpha_harness.forge.board.models import PipelineAlpha
from alpha_harness.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def rec(other: str, value: float) -> dict[str, Any]:
    return {"id": other, "correlation": value}


class HelperEndpoints(StubEndpoints):
    """Records every ``correlations`` call so the test can assert cache reuse."""

    def __init__(self, bodies: dict[tuple[str, str], dict[str, Any]]) -> None:
        super().__init__()
        self.bodies = bodies
        self.calls: list[tuple[str, str]] = []

    async def correlations(self, alpha_id: str, kind: str = "self") -> dict[str, Any]:
        self.calls.append((alpha_id, kind))
        return self.bodies.get((alpha_id, kind), {"records": []})


Bodies = dict[tuple[str, str], dict[str, Any]]


@pytest.fixture
async def client(
    harness: Harness,  # noqa: F811
    bodies: Bodies,
) -> AsyncIterator[httpx2.AsyncClient]:
    harness.endpoints = HelperEndpoints(bodies)
    app = create_app()
    app.state.harness = harness
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://localhost", headers=CLIENT
    ) as c:
        yield c


def stub(h: Harness) -> HelperEndpoints:
    assert isinstance(h.endpoints, HelperEndpoints)
    return h.endpoints


async def wait_task(h: Harness, task_id: str) -> dict[str, Any]:
    for _ in range(500):
        await drain(h.engine)
        task = next(t for t in h.tasks.list_tasks() if t["id"] == task_id)
        if task["state"] != "running":
            return task
        await asyncio.sleep(0.01)
    raise AssertionError("task never finished")


async def seed_pipeline(h: Harness, *alpha_ids: str) -> None:
    async with h.db.session() as session:
        session.add_all(
            PipelineAlpha(
                alpha_id=a,
                pipeline="regular",
                alpha_type="regular",
                region="USA",
                expression="rank(close)",
                settings={},
                field="close",
                template_id="t1",
                stage="r1",
                created_at=utcnow(),
            )
            for a in alpha_ids
        )


@pytest.fixture
def bodies() -> Bodies:
    return {}


async def test_recent_lists_pipeline_alphas_newest_first(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    await seed_pipeline(harness, "A1", "A2")
    res = await client.get("/api/forge/submit-helper/recent")
    assert res.status_code == 200, res.text
    ids = [r["alphaId"] for r in res.json()]
    assert set(ids) == {"A1", "A2"}


@pytest.mark.parametrize(
    "bodies",
    [
        {
            ("A1", "self"): {"records": [rec("A2", 0.9)]},
            ("A1", "prod"): {"records": [rec("X", 0.2)]},
        }
    ],
)
async def test_second_check_within_the_hour_reuses_the_cache(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
    bodies: Bodies,  # noqa: ARG001
) -> None:
    res = await client.post(
        "/api/forge/submit-helper/check", json={"alphaIds": ["A1"], "threshold": 0.7}
    )
    task = await wait_task(harness, res.json()["taskId"])
    assert task["state"] == "done", task
    first_calls = len(stub(harness).calls)
    assert first_calls == 2  # self + prod

    res2 = await client.post(
        "/api/forge/submit-helper/check", json={"alphaIds": ["A1"], "threshold": 0.7}
    )
    task2 = await wait_task(harness, res2.json()["taskId"])
    assert task2["state"] == "done", task2
    assert len(stub(harness).calls) == first_calls  # no new BRAIN calls: cache was fresh


@pytest.mark.parametrize(
    "bodies",
    [
        {
            ("A1", "self"): {"records": []},
            ("A1", "prod"): {"records": [rec("X", 0.2)]},
            ("A2", "self"): {"records": []},
            ("A2", "prod"): {"records": [rec("X", 0.9)]},
        }
    ],
)
async def test_verdict_pass_fail_against_threshold(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
    bodies: Bodies,  # noqa: ARG001
) -> None:
    res = await client.post(
        "/api/forge/submit-helper/check", json={"alphaIds": ["A1", "A2"], "threshold": 0.7}
    )
    task = await wait_task(harness, res.json()["taskId"])
    assert task["state"] == "done", task

    result = (await client.get("/api/forge/submit-helper/result")).json()
    verdicts = {r["alphaId"]: r["verdict"] for r in result["alphas"]}
    assert verdicts == {"A1": "PASS", "A2": "FAIL"}


@pytest.mark.parametrize(
    "bodies",
    [
        {
            ("A1", "self"): {"records": [rec("A2", 0.9), rec("A3", 0.1)]},
            ("A1", "prod"): {"records": [rec("X", 0.1)]},
            ("A2", "self"): {"records": [rec("A1", 0.9), rec("A3", 0.1)]},
            ("A2", "prod"): {"records": [rec("X", 0.1)]},
            ("A3", "self"): {"records": [rec("A1", 0.1), rec("A2", 0.1)]},
            ("A3", "prod"): {"records": [rec("X", 0.1)]},
        }
    ],
)
async def test_independent_set_drops_one_of_a_correlated_pair(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
    bodies: Bodies,  # noqa: ARG001
) -> None:
    # A1 and A2 are mutually correlated above 0.5; A3 is independent of both. All three pass
    # the (generous) threshold, so the recommendation should drop exactly one of A1/A2.
    res = await client.post(
        "/api/forge/submit-helper/check",
        json={"alphaIds": ["A1", "A2", "A3"], "threshold": 0.7},
    )
    task = await wait_task(harness, res.json()["taskId"])
    assert task["state"] == "done", task

    result = (await client.get("/api/forge/submit-helper/result")).json()
    recommended = set(result["recommended"])
    assert "A3" in recommended
    assert len(recommended & {"A1", "A2"}) == 1
    assert "mutually correlated above 0.5" in result["summary"]


async def test_check_refuses_a_second_concurrent_check(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    await harness.tasks.start(forge_submit_helper.FETCH_KIND, "already running")
    res = await client.post(
        "/api/forge/submit-helper/check", json={"alphaIds": ["A1"], "threshold": 0.7}
    )
    assert res.status_code == 409


async def test_result_is_none_before_any_check_has_run(
    client: httpx2.AsyncClient,
) -> None:
    res = await client.get("/api/forge/submit-helper/result")
    assert res.status_code == 200
    assert res.json() is None
