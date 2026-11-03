"""``_template_trials``: Deflated Sharpe's real trial count, read from ``forge_template_stats``,
with the fallback CLAUDE.md's hard rules require when that table has no rows for a template yet
(``forge_template_stats`` is filled by the Phase 6 pipelines and may simply be empty so far).
"""

from typing import TYPE_CHECKING

import pytest_asyncio

from alpha_harness.api.forge_analytics import (
    _alpha_row,  # pyright: ignore[reportPrivateUsage]
    _template_trials,  # pyright: ignore[reportPrivateUsage]
)
from alpha_harness.db.sqlite import Database
from alpha_harness.forge.board.models import PipelineAlpha
from alpha_harness.forge.studio.models import TemplateStats

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "harness.db")
    await database.create_all()
    yield database
    await database.dispose()


async def test_falls_back_to_one_trial_when_the_alpha_is_unknown(db: Database) -> None:
    n_trials, deflated = await _template_trials(db, "UNKNOWN")
    assert (n_trials, deflated) == (1, False)


async def test_falls_back_to_one_trial_when_template_stats_is_empty(db: Database) -> None:
    async with db.session() as session:
        session.add(
            PipelineAlpha(
                alpha_id="ALPHA1",
                pipeline="p",
                alpha_type="regular",
                expression="rank(x)",
                template_id="tmpl1",
            )
        )
    n_trials, deflated = await _template_trials(db, "ALPHA1")
    assert (n_trials, deflated) == (1, False)


async def test_sums_sims_across_every_scope_the_template_was_used_in(db: Database) -> None:
    async with db.session() as session:
        session.add(
            PipelineAlpha(
                alpha_id="ALPHA1",
                pipeline="p",
                alpha_type="regular",
                expression="rank(x)",
                template_id="tmpl1",
            )
        )
        session.add(TemplateStats(template_id="tmpl1", scope="USA", sims=40))
        session.add(TemplateStats(template_id="tmpl1", scope="GLB", sims=15))
        session.add(TemplateStats(template_id="other", scope="USA", sims=999))
    n_trials, deflated = await _template_trials(db, "ALPHA1")
    assert (n_trials, deflated) == (55, True)


async def test_alpha_row_is_none_for_an_unknown_alpha(db: Database) -> None:
    assert await _alpha_row(db, "UNKNOWN") is None


async def test_alpha_row_finds_the_board_record_by_alpha_id(db: Database) -> None:
    async with db.session() as session:
        session.add(
            PipelineAlpha(
                alpha_id="ALPHA1",
                pipeline="p",
                alpha_type="regular",
                expression="rank(x)",
                settings={"region": "USA", "universe": "TOP3000", "delay": 1},
            )
        )
    row = await _alpha_row(db, "ALPHA1")
    assert row is not None
    assert row.alpha_type == "regular"
    assert row.settings == {"region": "USA", "universe": "TOP3000", "delay": 1}
