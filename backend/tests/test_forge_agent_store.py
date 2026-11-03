"""``forge/agent/store.py``: hypotheses, lessons, proposals, and the read-only views over
pipeline run history and failures by reason (CLAUDE.md rule 10: offline, no BRAIN spend —
this module never touches a simulator)."""

from typing import TYPE_CHECKING

import pytest_asyncio

from alpha_harness.db.sqlite import Database
from alpha_harness.forge.agent import store as agent_store
from alpha_harness.forge.board.models import PipelineAlpha
from alpha_harness.forge.pipelines import store as pipelines_store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "harness.db")
    await database.create_all()
    yield database
    await database.dispose()


# --- hypotheses --------------------------------------------------------------------------


async def test_a_proposed_hypothesis_is_readable_and_listed_newest_first(db: Database) -> None:
    await agent_store.propose_hypothesis(db, title="h1", mechanism="m1", source="researcher")
    await agent_store.propose_hypothesis(db, title="h2", mechanism="m2", source="researcher")
    rows = await agent_store.list_hypotheses(db)
    assert [r.title for r in rows] == ["h2", "h1"]
    assert rows[0].status == "proposed"
    assert rows[0].source == "researcher"


async def test_closing_a_hypothesis_records_the_verdict(db: Database) -> None:
    row = await agent_store.propose_hypothesis(db, title="h1", mechanism="m1")
    await agent_store.close_hypothesis(db, row.id, "killed by the critic: known factor")
    rows = await agent_store.list_hypotheses(db, status="closed")
    assert len(rows) == 1
    assert rows[0].verdict == "killed by the critic: known factor"


async def test_listing_filters_by_status(db: Database) -> None:
    open_row = await agent_store.propose_hypothesis(db, title="open one", mechanism="m")
    await agent_store.close_hypothesis(db, open_row.id, "no")
    await agent_store.propose_hypothesis(db, title="still proposed", mechanism="m")
    proposed = await agent_store.list_hypotheses(db, status="proposed")
    assert [r.title for r in proposed] == ["still proposed"]


# --- lessons -----------------------------------------------------------------------------


async def test_a_recorded_lesson_is_listed_newest_first(db: Database) -> None:
    await agent_store.record_lesson(db, "lesson one", source="user", tags=["turnover"])
    await agent_store.record_lesson(db, "lesson two", source="weekly_job")
    rows = await agent_store.list_lessons(db)
    assert [r.text for r in rows] == ["lesson two", "lesson one"]
    assert rows[1].tags == ["turnover"]


# --- proposals ---------------------------------------------------------------------------


async def test_a_proposal_starts_pending_and_status_changes_stick(db: Database) -> None:
    row = await agent_store.create_proposal(
        db, kind="pipeline_run", payload={"pipeline": "regular"}, rationale="why not"
    )
    assert row.status == "pending"
    changed = await agent_store.set_proposal_status(db, row.id, "approved")
    assert changed is not None
    assert changed.status == "approved"
    assert changed.decided_at is not None
    fetched = await agent_store.get_proposal(db, row.id)
    assert fetched is not None
    assert fetched.status == "approved"


async def test_listing_proposals_filters_by_status(db: Database) -> None:
    a = await agent_store.create_proposal(db, kind="pipeline_run", payload={}, rationale="a")
    await agent_store.create_proposal(db, kind="pipeline_run", payload={}, rationale="b")
    await agent_store.set_proposal_status(db, a.id, "approved")
    pending = await agent_store.list_proposals(db, status="pending")
    assert len(pending) == 1
    assert pending[0].rationale == "b"


# --- pipeline run history and failures by reason ------------------------------------------


async def test_list_pipeline_runs_reads_the_same_table_the_pipelines_screen_does(
    db: Database,
) -> None:
    await pipelines_store.start_run(
        db,
        "run-1",
        pipeline="regular",
        field_set="pv",
        lane="forge_regular",
        template_ids=["t1"],
        budget=10,
    )
    rows = await agent_store.list_pipeline_runs(db)
    assert [r.id for r in rows] == ["run-1"]


async def test_failures_by_reason_counts_failing_checks_across_alphas(db: Database) -> None:
    async with db.session() as session:
        session.add_all(
            [
                PipelineAlpha(
                    alpha_id="A1",
                    pipeline="regular",
                    alpha_type="regular",
                    expression="rank(x)",
                    sim_checks=[
                        {"name": "HIGH_TURNOVER", "result": "FAIL"},
                        {"name": "LOW_SHARPE", "result": "PASS"},
                    ],
                ),
                PipelineAlpha(
                    alpha_id="A2",
                    pipeline="regular",
                    alpha_type="regular",
                    expression="rank(y)",
                    sim_checks=[{"name": "HIGH_TURNOVER", "result": "FAIL"}],
                ),
                PipelineAlpha(
                    alpha_id="A3",
                    pipeline="regular",
                    alpha_type="regular",
                    expression="rank(z)",
                    sim_checks=[{"name": "SELF_CORRELATION", "result": "ERROR"}],
                ),
            ]
        )
    counts = await agent_store.failures_by_reason(db)
    assert counts[0] == ("HIGH_TURNOVER", 2)
    assert ("SELF_CORRELATION", 1) in counts
    assert all(name != "LOW_SHARPE" for name, _ in counts)


async def test_failures_by_reason_ignores_alphas_outside_the_window(db: Database) -> None:
    from datetime import timedelta

    from alpha_harness.db.models import utcnow

    async with db.session() as session:
        session.add(
            PipelineAlpha(
                alpha_id="OLD",
                pipeline="regular",
                alpha_type="regular",
                expression="rank(x)",
                sim_checks=[{"name": "HIGH_TURNOVER", "result": "FAIL"}],
                created_at=utcnow() - timedelta(days=60),
            )
        )
    assert await agent_store.failures_by_reason(db, days=30) == []
