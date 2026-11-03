"""``forge/pipelines/store.py``: a pipeline run's durable row, from start to finish — and that
it survives a process restart, which is the entire point of this table existing (CLAUDE.md rule
10: offline tests, no BRAIN spend; this module never touches a simulator).
"""

from typing import TYPE_CHECKING

import pytest_asyncio

from alpha_harness.db.sqlite import Database
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


async def test_a_run_starts_running_and_is_readable_immediately(db: Database) -> None:
    await pipelines_store.start_run(
        db,
        "run-1",
        pipeline="regular",
        field_set="pv",
        lane="forge.regular",
        template_ids=["t1", "t2"],
        budget=10,
    )
    row = await pipelines_store.get_run(db, "run-1")
    assert row is not None
    assert row.status == "running"
    assert row.pipeline == "regular"
    assert row.template_ids == ["t1", "t2"]
    assert row.budget == 10
    assert row.finished_at is None


async def test_progress_updates_the_same_row(db: Database) -> None:
    await pipelines_store.start_run(
        db,
        "run-1",
        pipeline="regular",
        field_set="pv",
        lane="forge.regular",
        template_ids=["t1"],
        budget=5,
    )
    await pipelines_store.update_run(db, "run-1", detail="lint: 3/5 eligible")
    row = await pipelines_store.get_run(db, "run-1")
    assert row is not None
    assert row.detail == "lint: 3/5 eligible"
    assert row.status == "running"


async def test_finishing_a_run_fills_counts_and_the_report(db: Database) -> None:
    await pipelines_store.start_run(
        db,
        "run-1",
        pipeline="regular",
        field_set="pv",
        lane="forge.regular",
        template_ids=["t1"],
        budget=5,
    )
    report = {
        "status": "done",
        "pipeline": "run-1",
        "spent": 5,
        "candidates": [{"expression": "e1"}, {"expression": "e2"}],
        "finalists": [{"expression": "e1"}],
        "rejected": [{"expression": "e3", "reason": "nope"}],
        "checks": {},
        "correlations": {},
    }
    await pipelines_store.finish_run(db, "run-1", status="done", report=report)
    row = await pipelines_store.get_run(db, "run-1")
    assert row is not None
    assert row.status == "done"
    assert row.finished_at is not None
    assert row.candidates_count == 2
    assert row.finalists_count == 1
    assert row.rejected_count == 1
    assert row.spent == 5
    assert row.report == report


async def test_a_failed_run_records_its_error(db: Database) -> None:
    await pipelines_store.start_run(
        db,
        "run-1",
        pipeline="regular",
        field_set="pv",
        lane="forge.regular",
        template_ids=["t1"],
        budget=5,
    )
    await pipelines_store.finish_run(db, "run-1", status="failed", error="BRAIN timed out")
    row = await pipelines_store.get_run(db, "run-1")
    assert row is not None
    assert row.status == "failed"
    assert row.error == "BRAIN timed out"
    assert row.finished_at is not None


async def test_list_runs_is_most_recent_first(db: Database) -> None:
    await pipelines_store.start_run(
        db,
        "run-1",
        pipeline="regular",
        field_set="pv",
        lane="forge.regular",
        template_ids=["t1"],
        budget=5,
    )
    await pipelines_store.start_run(
        db,
        "run-2",
        pipeline="atom",
        field_set="pv",
        lane="forge.atom",
        template_ids=["t1"],
        budget=5,
    )
    rows = await pipelines_store.list_runs(db)
    assert [r.id for r in rows] == ["run-2", "run-1"]


async def test_a_runs_history_survives_a_simulated_restart(tmp_path: Path) -> None:
    path = tmp_path / "harness.db"
    first = Database(path)
    await first.create_all()
    await pipelines_store.start_run(
        first,
        "run-1",
        pipeline="regular",
        field_set="pv",
        lane="forge.regular",
        template_ids=["t1"],
        budget=5,
    )
    await pipelines_store.finish_run(
        first, "run-1", status="done", report={"candidates": [], "finalists": [], "rejected": []}
    )
    await first.dispose()

    # A fresh process would open a new Database against the same file.
    restarted = Database(path)
    await restarted.create_all()
    try:
        row = await pipelines_store.get_run(restarted, "run-1")
        assert row is not None
        assert row.status == "done"
        assert row.pipeline == "regular"
        rows = await pipelines_store.list_runs(restarted)
        assert [r.id for r in rows] == ["run-1"]
    finally:
        await restarted.dispose()
