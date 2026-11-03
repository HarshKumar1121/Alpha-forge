"""``forge/workshop/store.py``: the evidence challenge (a read over ``forge_pipeline_alpha``,
never a simulation — CLAUDE.md rule 10) and trial CRUD."""

from typing import TYPE_CHECKING

import pytest_asyncio

from alpha_harness.db.sqlite import Database
from alpha_harness.forge.board.models import PipelineAlpha
from alpha_harness.forge.workshop import store as workshop_store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "harness.db")
    await database.create_all()
    yield database
    await database.dispose()


async def _seed_alphas(db: Database) -> None:
    async with db.session() as session:
        session.add_all(
            [
                PipelineAlpha(
                    alpha_id="A1",
                    pipeline="regular",
                    alpha_type="regular",
                    expression="rank(news_sentiment)",
                    field="news_sentiment",
                    settings={"decay": 0},
                    sim_checks=[{"name": "HIGH_TURNOVER", "result": "FAIL"}],
                ),
                PipelineAlpha(
                    alpha_id="A2",
                    pipeline="regular",
                    alpha_type="regular",
                    expression="rank(news_sentiment)",
                    field="news_sentiment",
                    settings={"decay": 8},
                    sim_checks=[{"name": "HIGH_TURNOVER", "result": "FAIL"}],
                ),
                PipelineAlpha(
                    alpha_id="A3",
                    pipeline="regular",
                    alpha_type="regular",
                    expression="rank(cap)",
                    field="cap",
                    sim_checks=[{"name": "HIGH_TURNOVER", "result": "FAIL"}],
                ),
            ]
        )


async def test_challenge_counts_only_the_matching_field(db: Database) -> None:
    await _seed_alphas(db)
    out = await workshop_store.challenge(db, fields=["news_sentiment"])
    assert out["sample_size"] == 2
    assert out["findings"][0] == {
        "check": "HIGH_TURNOVER",
        "count": 2,
        "sample_size": 2,
        "no_decay": 1,
        "note": (
            "Your last 2 alphas on news_sentiment failed HIGH_TURNOVER 2 time(s). "
            "1 of those had no decay set."
        ),
    }


async def test_challenge_with_no_history_reports_an_empty_sample(db: Database) -> None:
    out = await workshop_store.challenge(db, fields=["never_used_field"])
    assert out == {
        "pipeline": "",
        "fields": ["never_used_field"],
        "days": 90,
        "sample_size": 0,
        "findings": [],
    }


async def test_challenge_filters_by_pipeline_too(db: Database) -> None:
    await _seed_alphas(db)
    async with db.session() as session:
        session.add(
            PipelineAlpha(
                alpha_id="A4",
                pipeline="power_pool",
                alpha_type="power_pool",
                expression="rank(news_sentiment)",
                field="news_sentiment",
                sim_checks=[{"name": "HIGH_TURNOVER", "result": "FAIL"}],
            )
        )
    out = await workshop_store.challenge(db, pipeline="power_pool", fields=["news_sentiment"])
    assert out["sample_size"] == 1


async def test_trial_round_trip(db: Database) -> None:
    row = await workshop_store.create_trial(
        db,
        hypothesis_id=None,
        hypothesis={"title": "t", "mechanism": "m", "expected_sign": "+"},
        pipeline="regular",
        field_set_id=1,
        fields=["news_sentiment"],
    )
    assert row.status == "running"
    assert row.lane == "validate"

    fetched = await workshop_store.get_trial(db, row.id)
    assert fetched is not None
    assert fetched.fields == ["news_sentiment"]

    done = await workshop_store.finish_trial(
        db,
        row.id,
        case={"verdict": "SURVIVES", "verdicts": {"news_sentiment": "SURVIVES"}},
        verdict="SURVIVES",
        spent=12,
    )
    assert done is not None
    assert done.status == "done"
    assert done.verdict == "SURVIVES"
    assert done.finished_at is not None

    await workshop_store.set_trial_proposal(db, row.id, 7)
    final = await workshop_store.get_trial(db, row.id)
    assert final is not None
    assert final.proposal_id == 7


async def test_a_failed_trial_is_marked_failed_with_its_error(db: Database) -> None:
    row = await workshop_store.create_trial(
        db,
        hypothesis_id=None,
        hypothesis={"title": "t", "mechanism": "m"},
        pipeline="regular",
        field_set_id=1,
        fields=["news_sentiment"],
    )
    failed = await workshop_store.fail_trial(db, row.id, error="QUOTA_GOVERNOR_OFF")
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == "QUOTA_GOVERNOR_OFF"


async def test_listing_trials_is_newest_first(db: Database) -> None:
    first = await workshop_store.create_trial(
        db, hypothesis_id=None, hypothesis={}, pipeline="regular", field_set_id=1, fields=["x"]
    )
    second = await workshop_store.create_trial(
        db, hypothesis_id=None, hypothesis={}, pipeline="regular", field_set_id=1, fields=["y"]
    )
    rows = await workshop_store.list_trials(db)
    assert [r.id for r in rows] == [second.id, first.id]
