"""``/api/forge/advisor``: preflight (catalog + ``forge_template_stats`` only) and postrun
(the Board's checks plus whatever the tear sheet could compute). Nothing here reaches BRAIN for
real and nothing simulates (CLAUDE.md rules 1 and 4).
"""

from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from forge_data_harness import CLIENT, make_client
from test_forge_pipelines_api import (
    Harness,  # noqa: TC002 - used bare in annotations alongside `harness`, a runtime fixture
    harness,  # noqa: F401  # pyright: ignore[reportUnusedImport] - fixture
)

from alpha_harness.brain.errors import BrainNotFound
from alpha_harness.db.models import utcnow
from alpha_harness.forge.board.models import PipelineAlpha
from alpha_harness.forge.studio.models import TemplateStats
from alpha_harness.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

USA = {"region": "USA", "delay": 1, "universe": "TOP3000"}
ANALYST = ["anl4_eps_mean", "anl4_rev_mean", "anl4_eps_low"]
SETTINGS = {
    "instrumentType": "EQUITY",
    "region": "USA",
    "universe": "TOP3000",
    "delay": 1,
    "decay": 0,
    "neutralization": "SUBINDUSTRY",
    "truncation": 0.08,
}


class NoPnlEndpoints:
    """BRAIN hasn't been fetched for this alpha yet: every recordset call 404s."""

    async def get_recordset(self, _alpha_id: str, _name: str) -> Any:
        raise BrainNotFound("no recordset yet")


@pytest.fixture
async def preflight(tmp_path: Path) -> AsyncIterator[tuple[httpx2.AsyncClient, Any]]:
    async for pair in make_client(tmp_path):
        yield pair


@pytest.fixture
async def postrun_client(harness: Harness) -> AsyncIterator[httpx2.AsyncClient]:  # noqa: F811
    harness.endpoints = NoPnlEndpoints()  # type: ignore[assignment]
    app = create_app()
    app.state.harness = harness
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://localhost", headers=CLIENT
    ) as c:
        yield c


async def seed_alpha(h: Harness, alpha_id: str, checks: list[dict[str, Any]]) -> None:
    async with h.db.session() as session:
        session.add(
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
                train_sharpe=0.8,
                sim_checks=checks,
                created_at=utcnow(),
            )
        )


# --- preflight --------------------------------------------------------------------------


async def test_preflight_describes_the_picked_fields(
    preflight: tuple[httpx2.AsyncClient, Any],
) -> None:
    client, _ = preflight
    res = await client.post(
        "/api/forge/advisor/preflight",
        json={"templateId": "my_revision", "fieldIds": ANALYST, **USA},
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "anl4_eps_mean" in body["paragraph"]
    assert "no logged attempts yet" in body["paragraph"]


async def test_preflight_reports_the_templates_track_record(
    preflight: tuple[httpx2.AsyncClient, Any],
) -> None:
    client, state = preflight
    async with state.db.session() as session:
        session.add(
            TemplateStats(
                template_id="my_revision",
                scope="EQUITY/USA/D1/TOP3000",
                dataset_category="analyst",
                alpha_type="regular",
                sims=12,
                checked=10,
                passed=4,
                median_train_sharpe=1.1,
            )
        )
    res = await client.post(
        "/api/forge/advisor/preflight",
        json={"templateId": "my_revision", "fieldIds": ANALYST[:1], **USA},
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    paragraph = res.json()["paragraph"]
    assert "run 12 times before" in paragraph
    assert "4/10 passed" in paragraph
    assert "median train Sharpe of 1.10" in paragraph


# --- postrun ----------------------------------------------------------------------------


async def test_postrun_unknown_alpha_is_404(postrun_client: httpx2.AsyncClient) -> None:
    res = await postrun_client.get("/api/forge/advisor/postrun/UNKNOWN")
    assert res.status_code == 404


async def test_postrun_suggests_the_repair_for_a_failing_check(
    postrun_client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    await seed_alpha(
        harness,
        "A1",
        [{"name": "LOW_FITNESS", "result": "FAIL", "value": 0.8, "limit": 1.0}],
    )
    res = await postrun_client.get("/api/forge/advisor/postrun/A1")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["actionKind"] == "repair"
    assert "LOW_FITNESS_FAST" in body["action"]
    assert "refuse" in body["verdict"]


async def test_postrun_asks_to_fetch_when_checks_pass_but_pnl_is_missing(
    postrun_client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    await seed_alpha(harness, "A2", [{"name": "LOW_SHARPE", "result": "PASS"}])
    res = await postrun_client.get("/api/forge/advisor/postrun/A2")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["actionKind"] == "fetch"
    assert "submittable" in body["verdict"]
