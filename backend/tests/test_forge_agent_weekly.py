"""``forge/agent/weekly.py``: the weekly-summary job writes one lesson from the last N days of
pipeline runs and failing checks (CLAUDE.md rule 10: offline, calls no simulator)."""

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest_asyncio

from alpha_harness.db.models import utcnow
from alpha_harness.db.sqlite import Database
from alpha_harness.forge.agent import store as agent_store
from alpha_harness.forge.agent import weekly
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


async def test_weekly_summary_counts_recent_runs_and_top_failures(db: Database) -> None:
    await pipelines_store.start_run(
        db,
        "run-1",
        pipeline="regular",
        field_set="pv",
        lane="forge_regular",
        template_ids=["t1"],
        budget=10,
    )
    await pipelines_store.finish_run(
        db,
        "run-1",
        status="done",
        report={
            "status": "done",
            "pipeline": "run-1",
            "spent": 8,
            "candidates": [],
            "finalists": [],
            "rejected": [],
            "checks": {},
            "correlations": {},
        },
    )
    async with db.session() as session:
        session.add(
            PipelineAlpha(
                alpha_id="A1",
                pipeline="regular",
                alpha_type="regular",
                expression="rank(x)",
                sim_checks=[{"name": "HIGH_TURNOVER", "result": "FAIL"}],
            )
        )

    lesson = await weekly.run_weekly_summary(db, days=7)

    assert lesson.source == "weekly_job"
    assert "weekly-summary" in lesson.tags
    assert "high_turnover" in lesson.tags
    assert "1 pipeline run" in lesson.text
    assert "HIGH_TURNOVER" in lesson.text

    stored = await agent_store.list_lessons(db)
    assert stored[0].id == lesson.id


async def test_weekly_summary_ignores_runs_and_alphas_outside_the_window(db: Database) -> None:
    async with db.session() as session:
        session.add(
            PipelineAlpha(
                alpha_id="OLD",
                pipeline="regular",
                alpha_type="regular",
                expression="rank(x)",
                sim_checks=[{"name": "HIGH_TURNOVER", "result": "FAIL"}],
                created_at=utcnow() - timedelta(days=90),
            )
        )
    await pipelines_store.start_run(
        db,
        "old-run",
        pipeline="regular",
        field_set="pv",
        lane="forge_regular",
        template_ids=["t1"],
        budget=5,
    )
    async with db.session() as session:
        from alpha_harness.forge.pipelines.models import PipelineRunRecord

        row = await session.get(PipelineRunRecord, "old-run")
        assert row is not None
        row.started_at = utcnow() - timedelta(days=90)

    lesson = await weekly.run_weekly_summary(db, days=7)
    assert "0 pipeline run" in lesson.text
    assert "No failing checks" in lesson.text
