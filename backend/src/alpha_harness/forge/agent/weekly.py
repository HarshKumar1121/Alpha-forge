"""The weekly summary job: reads the last 7 days of runs and failures, writes one lesson.

Playbook Phase 10: "a weekly summary is written to lessons automatically." It is a plain
function, not a scheduler, so a test calls it directly (CLAUDE.md rule 10: offline tests) and
whatever wall-clock scheduling exists (``main.py``'s lifespan, a future ``forge.schedule``
module) is a thin, untested-by-necessity wrapper around it.
"""

from datetime import timedelta
from typing import TYPE_CHECKING

from ...db.models import utcnow
from . import store as agent_store

if TYPE_CHECKING:
    from ...db.sqlite import Database
    from ..pipelines.models import PipelineRunRecord
    from .models import AgentLesson

WINDOW_DAYS = 7


def _summary_text(days: int, runs: list[PipelineRunRecord], failures: list[tuple[str, int]]) -> str:
    spent = sum(r.spent for r in runs)
    done = sum(1 for r in runs if r.status == "done")
    lines = [
        f"Week in review ({days}d): {len(runs)} pipeline run(s), {done} finished, "
        f"{spent} simulation(s) spent."
    ]
    if failures:
        top = ", ".join(f"{name} x{count}" for name, count in failures[:3])
        lines.append(f"Most common failing checks: {top}.")
    else:
        lines.append("No failing checks recorded this week.")
    return " ".join(lines)


def _within(run: PipelineRunRecord, days: int) -> bool:
    return run.started_at >= utcnow() - timedelta(days=days)


async def run_weekly_summary(db: Database, *, days: int = WINDOW_DAYS) -> AgentLesson:
    """Aggregate the last ``days`` days and write them to ``lessons`` as one entry.

    Idempotent in spirit, not in effect: calling it twice in one day writes two lessons, same
    as a person journaling twice would. The caller (a scheduler, or a test) decides how often
    that is appropriate; this function only knows how to summarize a window.
    """
    runs = await agent_store.list_pipeline_runs(db, limit=500)
    recent_runs = [r for r in runs if _within(r, days)]
    failures = await agent_store.failures_by_reason(db, days=days)
    text = _summary_text(days, recent_runs, failures)
    tags = ["weekly-summary"] + [name.lower() for name, _ in failures[:3]]
    return await agent_store.record_lesson(db, text, source="weekly_job", tags=tags)
