"""Results Breakdown on synthetic data: pure functions first (CLAUDE.md rule 4), then the real
route end to end with a fake DuckDB/SQLite pair, proving the stage-then-repair bridge actually
reaches the existing ``/api/forge/board/repair`` path (CLAUDE.md rule 3: the only path that
spends anything)."""

import json
from typing import TYPE_CHECKING, Any

import httpx2
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select
from test_forge_board_api import BoardEndpoints
from test_forge_pipelines_api import (
    CLIENT,
    Harness,
    drain,
    harness,  # noqa: F401  # pyright: ignore[reportUnusedImport] - fixture
)

from alpha_harness.api import forge_board
from alpha_harness.db.models import BrainCache, Study, StudyStatus, Trial, TrialState, utcnow
from alpha_harness.forge.board import breakdown as bk
from alpha_harness.forge.board.models import PipelineAlpha
from alpha_harness.forge.pipelines.brain_adapter import BrainSimulator
from alpha_harness.main import create_app
from alpha_harness.vault.store import AlphaVault

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

DAYS = pd.bdate_range("2022-01-03", periods=600)


def pnl(seed: int, base: np.ndarray | None = None, noise: float = 0.3) -> pd.Series:
    rng = np.random.default_rng(seed)
    own = rng.normal(0, 1, len(DAYS))
    return pd.Series(own if base is None else base + noise * own, index=DAYS)


BASE = np.random.default_rng(0).normal(0, 1, len(DAYS))

PASS = [{"name": "LOW_SHARPE", "result": "PASS"}, {"name": "SELF_CORRELATION", "result": "PASS"}]
FAIL_FITNESS_NEAR = [
    {"name": "LOW_SHARPE", "result": "PASS"},
    {"name": "LOW_FITNESS", "result": "FAIL", "value": 0.95, "limit": 1.0},
]
FAIL_FITNESS_FAR = [
    {"name": "LOW_SHARPE", "result": "PASS"},
    {"name": "LOW_FITNESS", "result": "FAIL", "value": 0.2, "limit": 1.0},
]
FAIL_TWO = [
    {"name": "LOW_SHARPE", "result": "FAIL", "value": 0.5, "limit": 1.0},
    {"name": "LOW_FITNESS", "result": "FAIL", "value": 0.95, "limit": 1.0},
]
FAIL_NO_REPAIR = [{"name": "LOW_SHARPE", "result": "FAIL", "value": 0.5, "limit": 1.0}]


def row(
    alpha_id: str, field: str, train: float | None, checks: list[dict[str, Any]]
) -> bk.AlphaRow:
    return bk.AlphaRow(
        alpha_id=alpha_id,
        expression=f"rank({field})",
        settings={"neutralization": "SUBINDUSTRY", "decay": 5},
        field=field,
        template_id="t1",
        train_sharpe=train,
        test_sharpe=None,
        checks=checks,
    )


# --- (a) slice breakdown -----------------------------------------------------------------


def test_slice_breakdown_groups_by_field_and_hides_small_slices() -> None:
    rows = [
        row("A1", "close", 1.5, PASS),
        row("A2", "close", 1.2, FAIL_NO_REPAIR),
        row("A3", "volume", 1.0, PASS),  # only one alpha: hidden by min_n
    ]
    out = bk.slice_breakdown(rows, "field", None, min_n=2, datasets_of={})
    assert [s.key for s in out] == ["close"]
    close = out[0]
    assert close.n == 2
    assert close.pass_rate == pytest.approx(0.5)
    assert close.median_train_sharpe == pytest.approx(1.35)


def test_slice_breakdown_by_dataset_counts_a_shared_field_toward_both() -> None:
    rows = [row("A1", "f1", 1.0, PASS), row("A2", "f1", 1.2, PASS)]
    datasets_of = {"f1": {"analyst", "pv"}}
    out = bk.slice_breakdown(rows, "dataset", None, min_n=2, datasets_of=datasets_of)
    assert {s.key for s in out} == {"analyst", "pv"}
    assert all(s.n == 2 for s in out)


def test_slice_breakdown_by_setting() -> None:
    rows = [row("A1", "f1", 1.0, PASS), row("A2", "f2", 1.2, PASS)]
    out = bk.slice_breakdown(rows, "setting", "neutralization", min_n=1, datasets_of={})
    assert [s.key for s in out] == ["SUBINDUSTRY"]
    assert out[0].n == 2


# --- (b) why alphas failed, and near-miss ------------------------------------------------


def test_failure_counts_tallies_gating_checks() -> None:
    rows = [row("A1", "f1", 1.0, FAIL_NO_REPAIR), row("A2", "f1", 1.0, FAIL_TWO)]
    counts = {c.check: c.count for c in bk.failure_counts(rows)}
    assert counts == {"LOW_SHARPE": 2, "LOW_FITNESS": 1}


def test_near_miss_requires_exactly_one_failure_a_repair_and_a_close_margin() -> None:
    rows = [
        row("close_call", "close", 1.0, FAIL_FITNESS_NEAR),  # 0.95/1.0 margin: near
        row("far_off", "close", 1.0, FAIL_FITNESS_FAR),  # 0.2/1.0 margin: not near
        row("two_fails", "close", 1.0, FAIL_TWO),  # fails two checks: excluded
        row("no_repair", "close", 1.0, FAIL_NO_REPAIR),  # LOW_SHARPE has no repair: excluded
    ]
    near = bk.near_misses(rows, margin_cutoff=0.75)
    assert [n.alpha_id for n in near] == ["close_call"]
    assert near[0].repair_key == "LOW_FITNESS_FAST"
    assert near[0].margin == pytest.approx(0.95)


def test_check_margin_is_direction_aware() -> None:
    # LOW_*: fails below its limit, so value/limit.
    assert bk.check_margin("LOW_SUB_UNIVERSE_SHARPE", 0.4, 0.5) == pytest.approx(0.8)
    # HIGH_*/CONCENTRATED_*: fails above its limit, so limit/value.
    assert bk.check_margin("HIGH_TURNOVER", 0.8, 0.4) == pytest.approx(0.5)
    assert bk.check_margin("CONCENTRATED_WEIGHT", 0.08, 0.04) == pytest.approx(0.5)
    assert bk.check_margin("LOW_SHARPE", 1.0, 0.0) is None  # zero limit: no ratio to report


# --- (c) submit order by correlation -------------------------------------------------------


def test_submit_order_picks_one_leader_per_family_fewest_correlated_first_cumulatively() -> None:
    leader = row("L", "f1", 1.8, PASS)
    leader.pnl = pnl(2, BASE)
    member = row("M", "f1", 1.2, PASS)  # same bet as the leader: one family, member dropped
    member.pnl = pnl(1, BASE)
    independent = row("I", "f2", 1.0, PASS)
    independent.pnl = pnl(99)  # uncorrelated with everything
    passing = [leader, member, independent]

    pool = {"SUBMITTED1": pnl(2, BASE)}  # highly correlated with the family leader

    order = bk.submit_order(passing, pool, family_threshold=0.6, overlap_ceiling=0.7)
    assert {c.alpha_id for c in order} == {"L", "I"}  # M is not a leader: never ranked alone
    by_id = {c.alpha_id: c for c in order}
    assert by_id["L"].family_size == 2
    assert by_id["L"].high_overlap is True
    assert by_id["L"].best_correlation_with == "SUBMITTED1"
    assert by_id["I"].high_overlap is False
    # Fewest-correlated-first: the independent idea (low overlap) ranks before the colliding one.
    assert [c.alpha_id for c in order] == ["I", "L"]


def test_submit_order_handles_no_pnl_for_anything() -> None:
    rows = [row("A", "f1", 1.0, PASS), row("B", "f2", 1.2, PASS)]
    order = bk.submit_order(rows, {}, family_threshold=0.6, overlap_ceiling=0.7)
    assert {c.alpha_id for c in order} == {"A", "B"}
    assert all(c.best_correlation is None and not c.high_overlap for c in order)
    assert all(not c.has_check_data for c in order)


def test_submit_order_flags_candidates_with_brain_check_data() -> None:
    """``has_check_data`` is independent of the PnL-only ``best_correlation`` score: it just
    says whether BRAIN's own self/prod correlation checks have ever been fetched for this id."""
    rows = [row("A", "f1", 1.0, PASS), row("B", "f2", 1.2, PASS)]
    order = bk.submit_order(rows, {}, family_threshold=0.6, overlap_ceiling=0.7, checked={"A"})
    by_id = {c.alpha_id: c for c in order}
    assert by_id["A"].has_check_data is True
    assert by_id["B"].has_check_data is False


# --- the route, end to end -----------------------------------------------------------------


@pytest.fixture
async def client(
    harness: Harness,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx2.AsyncClient]:
    def fast_simulator(engine: Any, db: Any, endpoints: Any, lane: str) -> BrainSimulator:
        return BrainSimulator(engine, db, endpoints, lane, poll_interval=0.01)

    monkeypatch.setattr(forge_board, "BrainSimulator", fast_simulator)
    harness.endpoints = BoardEndpoints()
    harness.alphas = AlphaVault(harness.catalog)  # forge: breakdown reads BRAIN history here
    app = create_app()
    app.state.harness = harness
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://localhost", headers=CLIENT
    ) as c:
        yield c


async def seed_task(h: Harness) -> int:
    """One finished Template Lab task: two trials on ``close``, one on ``volume``."""
    async with h.db.session() as session:
        study = Study(
            name="t1",
            label="Close sweep",
            template_source="",
            sampler="template",
            sampler_params={},
            objectives=["sharpe"],
            directions=["maximize"],
            seed=0,
            batch_size=1,
            max_trials=10,
            task="test",
            status=StudyStatus.COMPLETE,
        )
        session.add(study)
        await session.flush()
        for n, (alpha_id, field, checks) in enumerate(
            [
                ("A1", "close", PASS),
                ("A2", "close", FAIL_FITNESS_NEAR),
                ("A3", "volume", FAIL_FITNESS_FAR),
            ]
        ):
            session.add(
                Trial(
                    study_id=study.id,
                    number=n,
                    params={},
                    expression=f"rank({field})",
                    settings={
                        "region": "USA",
                        "universe": "TOP3000",
                        "delay": 1,
                        "neutralization": "SUBINDUSTRY",
                        "decay": 5,
                    },
                    state=TrialState.COMPLETE,
                    values=[1.5],
                    result={"stats": {"sharpe": 1.5}, "checks": checks},
                    alpha_id=alpha_id,
                    finished_at=utcnow(),
                )
            )
        task_id = study.id
    for alpha_id, checks in [("A1", PASS), ("A2", FAIL_FITNESS_NEAR), ("A3", FAIL_FITNESS_FAR)]:
        await h.catalog.execute(
            "INSERT INTO alpha (alpha_id, expression, instrument_type, region, delay, universe,"
            " checks, sim_type, series_version, fetched_at) VALUES"
            " (?, 'rank(close)', 'EQUITY', 'USA', 1, 'TOP3000', ?, 'REGULAR', 3, now())",
            [alpha_id, json.dumps(checks)],
        )
    return task_id


async def test_breakdown_reads_slices_failures_and_near_misses_from_the_task(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    task_id = await seed_task(harness)
    res = await client.get(f"/api/forge/breakdown/{task_id}", params={"min_n": 1})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 3
    close_slice = next(s for s in body["slices"] if s["key"] == "close")
    assert close_slice["n"] == 2
    assert close_slice["passRate"] == pytest.approx(0.5)
    assert {f["check"] for f in body["failures"]} == {"LOW_FITNESS"}
    assert [n["alphaId"] for n in body["nearMisses"]] == ["A2"]


async def test_submit_order_has_check_data_reflects_brain_cache(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    """Both ``correlation:self:`` and ``correlation:prod:`` must be cached for an id to count
    as checked -- whichever screen (Board or Submit Helper) fetched them, since they share
    cache keys; one-sided cache data is not enough to cross-check against BRAIN."""
    task_id = await seed_task(harness)
    async with harness.db.session() as session:
        session.add(BrainCache(key="correlation:self:A1", body={}))
        session.add(BrainCache(key="correlation:prod:A1", body={}))

    res = await client.get(f"/api/forge/breakdown/{task_id}", params={"min_n": 1})
    assert res.status_code == 200, res.text
    order = {c["alphaId"]: c for c in res.json()["submitOrder"]}
    assert order["A1"]["hasCheckData"] is True


async def test_stage_then_repair_queues_through_the_existing_repair_route(
    client: httpx2.AsyncClient,
    harness: Harness,  # noqa: F811
) -> None:
    """The one spend in this feature: staging writes a local row, then the existing
    ``/api/forge/board/repair`` route (unchanged) admits it through the real Governor."""
    task_id = await seed_task(harness)
    staged = await client.post(f"/api/forge/breakdown/{task_id}/alphas/A2/stage")
    assert staged.status_code == 200, staged.text
    assert staged.json()["alphaId"] == "A2"

    res = await client.post("/api/forge/board/repair", json={"alphaId": "A2"})
    assert res.status_code == 200, res.text
    queued = res.json()
    assert queued["repair"] == "LOW_FITNESS_FAST"
    assert queued["lane"] == "validate"

    for _ in range(500):
        await drain(harness.engine)
        task = next(t for t in harness.tasks.list_tasks() if t["id"] == queued["taskId"])
        if task["state"] != "running":
            break
    else:
        raise AssertionError("repair task never finished")
    assert task["state"] == "done", task

    async with harness.db.session() as session:
        staged_row = (
            await session.scalars(select(PipelineAlpha).where(PipelineAlpha.alpha_id == "A2"))
        ).one()
    assert staged_row.pipeline == "template-lab"
    assert staged_row.sim_type == "REGULAR"


async def test_breakdown_404s_for_an_unknown_task(client: httpx2.AsyncClient) -> None:
    res = await client.get("/api/forge/breakdown/999999")
    assert res.status_code == 404
