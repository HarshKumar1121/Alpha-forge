"""``forge/storage``: the last-accessed tracker and the cache/PnL TTL decisions (CLAUDE.md rule
10 — offline, no BRAIN calls; everything here is local SQLite + DuckDB)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest_asyncio
from sqlalchemy import select

from alpha_harness.db.duck import Catalog
from alpha_harness.db.models import BrainCache, utcnow
from alpha_harness.db.sqlite import Database
from alpha_harness.forge.board.models import PipelineAlpha
from alpha_harness.forge.storage import cleanup
from alpha_harness.forge.storage.access import touch_pnl
from alpha_harness.forge.storage.models import StorageAccess

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import pytest

NOW = utcnow()


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "harness.db")
    await database.create_all()
    yield database
    await database.dispose()


@pytest_asyncio.fixture
async def catalog(tmp_path: Path) -> AsyncIterator[Catalog]:
    cat = Catalog(tmp_path / "catalog.duckdb")
    await cat.open()
    yield cat
    await cat.close()


def _check_body(result: str = "FAIL") -> dict:
    check = {"name": "LOW_SHARPE", "result": result, "value": 0.5, "limit": 1.0}
    return {"is": {"checks": [check]}}


async def _cache(db: Database, key: str, fetched_at: datetime, body: dict | None = None) -> None:
    async with db.session() as session:
        await session.merge(BrainCache(key=key, body=body or {"ok": True}, fetched_at=fetched_at))


async def _alpha(catalog: Catalog, alpha_id: str, *, submitted: bool, fetched_at: datetime) -> None:
    await catalog.upsert(
        "alpha",
        ("alpha_id", "date_submitted", "fetched_at"),
        [(alpha_id, fetched_at if submitted else None, fetched_at)],
    )


async def _pnl(catalog: Catalog, alpha_id: str) -> None:
    await catalog.upsert(
        "alpha_pnl", ("alpha_id", "date", "pnl"), [(alpha_id, date(2024, 1, 1), 1.0)]
    )


async def test_touch_pnl_upserts_and_ignores_empty(db: Database) -> None:
    await touch_pnl(db, ["A1", "A1", "A2"])
    await touch_pnl(db, [])
    async with db.session() as session:
        rows = (await session.scalars(select(StorageAccess))).all()
    assert {r.alpha_id for r in rows} == {"A1", "A2"}


async def test_old_plain_cache_entry_expires(db: Database) -> None:
    await _cache(db, "alpha:A1", NOW - timedelta(days=cleanup.CACHE_TTL_DAYS + 1))
    expired = await cleanup.expired_cache_rows(db, now=NOW)
    assert [e.key for e in expired] == ["alpha:A1"]


async def test_fresh_plain_cache_entry_survives(db: Database) -> None:
    await _cache(db, "alpha:A1", NOW - timedelta(days=1))
    expired = await cleanup.expired_cache_rows(db, now=NOW)
    assert expired == []


async def test_check_entry_protected_by_submittable_board(db: Database) -> None:
    old = NOW - timedelta(days=cleanup.CACHE_TTL_DAYS + 1)
    await _cache(db, "check:A1", old, _check_body())
    async with db.session() as session:
        session.add(
            PipelineAlpha(
                alpha_id="A1",
                pipeline="regular",
                alpha_type="regular",
                expression="rank(x)",
                region="USA",
                created_at=NOW - timedelta(days=1),
            )
        )
    expired = await cleanup.expired_cache_rows(db, now=NOW)
    assert expired == []


async def test_check_entry_protected_when_fetched_recently(
    monkeypatch: pytest.MonkeyPatch, db: Database
) -> None:
    # A shorter configured TTL is where the "checked in the last 7 days" exception bites
    # independently of Board membership: the plain TTL alone would already expire this.
    monkeypatch.setattr(cleanup, "CACHE_TTL_DAYS", 3)
    fetched_at = NOW - timedelta(days=5)
    await _cache(db, "check:A1", fetched_at, _check_body())
    expired = await cleanup.expired_cache_rows(db, now=NOW)
    assert expired == []


async def test_check_entry_expires_and_preserves_verdict(db: Database) -> None:
    old = NOW - timedelta(days=cleanup.CACHE_TTL_DAYS + cleanup.PROTECT_RECENT_DAYS + 1)
    await _cache(db, "check:A1", old, _check_body("FAIL"))
    expired = await cleanup.expired_cache_rows(db, now=NOW)
    assert len(expired) == 1
    preserved = expired[0].preserve
    assert len(preserved) == 1
    assert preserved[0].alpha_id == "A1"
    assert preserved[0].check_name == "LOW_SHARPE"
    assert preserved[0].result == "FAIL"


async def test_correlation_key_parses_alpha_id(db: Database) -> None:
    old = NOW - timedelta(days=cleanup.CACHE_TTL_DAYS + cleanup.PROTECT_RECENT_DAYS + 1)
    await _cache(db, "correlation:self:A1", old)
    protected = await cleanup.protected_alpha_ids(db)
    assert "A1" not in protected
    expired = await cleanup.expired_cache_rows(db, now=NOW)
    assert [e.key for e in expired] == ["correlation:self:A1"]


async def test_submitted_alpha_pnl_never_expires(db: Database, catalog: Catalog) -> None:
    long_ago = NOW - timedelta(days=cleanup.PNL_IDLE_DAYS + 100)
    await _alpha(catalog, "SUB1", submitted=True, fetched_at=long_ago)
    await _pnl(catalog, "SUB1")
    expired = await cleanup.expired_pnl_alpha_ids(db, catalog, now=NOW)
    assert expired == []


async def test_unsubmitted_idle_alpha_pnl_expires(db: Database, catalog: Catalog) -> None:
    long_ago = NOW - timedelta(days=cleanup.PNL_IDLE_DAYS + 1)
    await _alpha(catalog, "CAND1", submitted=False, fetched_at=long_ago)
    await _pnl(catalog, "CAND1")
    expired = await cleanup.expired_pnl_alpha_ids(db, catalog, now=NOW)
    assert expired == ["CAND1"]


async def test_unsubmitted_recently_accessed_alpha_pnl_survives(
    db: Database, catalog: Catalog
) -> None:
    long_ago = NOW - timedelta(days=cleanup.PNL_IDLE_DAYS + 1)
    await _alpha(catalog, "CAND2", submitted=False, fetched_at=long_ago)
    await _pnl(catalog, "CAND2")
    await touch_pnl(db, ["CAND2"])
    expired = await cleanup.expired_pnl_alpha_ids(db, catalog, now=NOW)
    assert expired == []


async def test_dry_run_report_counts_without_deleting(db: Database, catalog: Catalog) -> None:
    old = NOW - timedelta(days=cleanup.CACHE_TTL_DAYS + 1)
    await _cache(db, "alpha:A1", old)
    long_ago = NOW - timedelta(days=cleanup.PNL_IDLE_DAYS + 1)
    await _alpha(catalog, "CAND1", submitted=False, fetched_at=long_ago)
    await _pnl(catalog, "CAND1")

    report = await cleanup.dry_run_report(db, catalog, now=NOW)

    assert report.cache_total == 1
    assert report.cache_expired == 1
    assert report.pnl_alpha_total == 1
    assert report.pnl_alpha_expired == 1

    # Nothing was actually deleted.
    async with db.session() as session:
        remaining = (await session.scalars(select(BrainCache))).all()
    assert len(remaining) == 1
    pnl_rows = await catalog.query("SELECT count(*) AS n FROM alpha_pnl")
    assert pnl_rows[0]["n"] == 1
