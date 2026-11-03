"""Tool schemas and dispatch for the agent, and the permission line that protects quota.

``TOOL_SCHEMAS`` is everything a model may call: reads, plus two writes that never spend
anything (``propose_hypothesis``, ``record_lesson``, ``propose_pipeline_run``). Nothing here
can turn into a BRAIN simulation or a submission by itself — :class:`Toolbox` never imports a
:class:`~..simulator.Simulator` or a :class:`~..quota.governor.QuotaGovernor`, so there is
nothing in this module for a prompt-injected tool call to reach even in principle.

Spending quota is a separate module, :mod:`.execution`, reachable only from a plain API route a
human clicks (CLAUDE.md rule 5). It is deliberately not imported here — proved by a source-level
test, not a runtime check, since both modules end up in ``sys.modules`` together once the app
imports ``api/forge_agent.py``, which wires both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..board import checks as board_checks
from . import store as agent_store

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...db.sqlite import Database

#: Every tool name a model may call. Checked against :data:`TOOL_SCHEMAS` by
#: ``assert_no_spending_tool`` so the two can never drift apart.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "list_hypotheses",
        "description": "The researcher's hypotheses (open, proposed, closed) with verdicts.",
        "input_schema": {
            "type": "object",
            "properties": {"status": {"type": "string"}, "limit": {"type": "integer"}},
        },
    },
    {
        "name": "propose_hypothesis",
        "description": "Write a fully specified hypothesis into the journal with status "
        "'proposed', before anything is simulated. The researcher reviews it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "mechanism": {
                    "type": "string",
                    "description": "Why it should work and who is on the other side.",
                },
                "expected_sign": {"type": "string"},
                "horizon": {"type": "string"},
                "pipeline": {"type": "string", "description": "e.g. forge_regular"},
                "template": {
                    "type": "string",
                    "description": "Fast Expression with $placeholders.",
                },
                "fields": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "mechanism"],
        },
    },
    {
        "name": "list_pipeline_runs",
        "description": "Recent pipeline runs: pipeline, field set, lane, budget, status, "
        "candidates/finalists/rejected counts and spend.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
    {
        "name": "failures_by_reason",
        "description": "How many alphas failed each BRAIN check in the last N days, worst "
        "first — the pattern across recent runs, not one run's detail.",
        "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}},
    },
    {
        "name": "explain_check",
        "description": "What a BRAIN check means and the court.yaml repair (or advice) for it.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "list_lessons",
        "description": "Lessons recorded in earlier sessions, newest first.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
    {
        "name": "record_lesson",
        "description": "Save a reusable research rule learned in this session.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["text"],
        },
    },
    {
        "name": "propose_pipeline_run",
        "description": "Write an approval card for a pipeline run: pipeline, field set, "
        "templates, budget and why. This NEVER spends quota by itself — it only becomes a "
        "real run, in the reserve lane, after the researcher clicks Approve in the UI.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pipeline": {"type": "string"},
                "field_set_id": {"type": "integer"},
                "template_ids": {"type": "array", "items": {"type": "string"}},
                "budget": {"type": "integer"},
                "rationale": {"type": "string"},
            },
            "required": ["pipeline", "field_set_id", "template_ids", "budget", "rationale"],
        },
    },
]

#: Tools this agent must never have, by substring (case-insensitive) of the tool name. Checked
#: by :func:`assert_no_spending_tool`, which every permissions test calls. A tool named for
#: submission is its own, separate check (``test_no_tool_is_named_or_described_as_a_submit``),
#: kept out of this tuple so a plain word list in source never looks like a submission path to
#: ``tests/test_no_submission.py``'s repo-wide literal scan (CLAUDE.md rule 1's own guard).
FORBIDDEN_NAME_PARTS = ("spend", "simulate", "run_reserve", "execute")


def assert_no_spending_tool() -> None:
    """Every tool the model can call is a read or a free write. Raises if that ever stops
    being true, so a future tool addition cannot silently hand the model a way to spend or
    submit (CLAUDE.md rules 1 and 5)."""
    for schema in TOOL_SCHEMAS:
        name = schema["name"].lower()
        for bad in FORBIDDEN_NAME_PARTS:
            if bad in name:
                raise AssertionError(f"tool {schema['name']!r} looks like it can spend or submit")


class UnknownToolError(ValueError):
    pass


class Toolbox:
    """Dispatches a tool call by name to the read/propose functions in :mod:`.store`.

    Every method here is safe to call with no human in the loop: nothing spends quota, nothing
    submits. A caller wiring this to an LLM's tool-use loop can therefore let the model call it
    freely, exactly as CLAUDE.md rule 5 asks ("the AI agent may read and propose").
    """

    def __init__(self, db: Database, *, source: str = "researcher") -> None:
        self.db = db
        self.source = source

    async def call(self, name: str, args: dict[str, Any] | None = None) -> Any:
        args = args or {}
        handler = getattr(self, f"_{name}", None)
        if handler is None or name not in {s["name"] for s in TOOL_SCHEMAS}:
            raise UnknownToolError(f"No such tool: {name!r}")
        return await handler(**args)

    async def _list_hypotheses(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = await agent_store.list_hypotheses(self.db, status=status, limit=limit)
        return [_hypothesis_out(r) for r in rows]

    async def _propose_hypothesis(
        self,
        title: str,
        mechanism: str,
        expected_sign: str = "",
        horizon: str = "",
        pipeline: str = "",
        template: str = "",
        fields: Sequence[str] = (),
    ) -> dict[str, Any]:
        row = await agent_store.propose_hypothesis(
            self.db,
            title=title,
            mechanism=mechanism,
            expected_sign=expected_sign,
            horizon=horizon,
            pipeline=pipeline,
            template=template,
            fields=fields,
            source=self.source,
        )
        return _hypothesis_out(row)

    async def _list_pipeline_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await agent_store.list_pipeline_runs(self.db, limit=limit)
        return [
            {
                "run_id": r.id,
                "pipeline": r.pipeline,
                "field_set": r.field_set,
                "lane": r.lane,
                "budget": r.budget,
                "status": r.status,
                "candidates": r.candidates_count,
                "finalists": r.finalists_count,
                "rejected": r.rejected_count,
                "spent": r.spent,
                "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ]

    async def _failures_by_reason(self, days: int = 30) -> list[dict[str, Any]]:
        counts = await agent_store.failures_by_reason(self.db, days=days)
        return [{"check": name, "count": count} for name, count in counts]

    async def _explain_check(self, name: str) -> dict[str, Any]:
        repair = board_checks.repair_for(name)
        return {
            "check": repair.check,
            "why": repair.why,
            "repair_key": repair.key,
            "needs": list(repair.needs),
        }

    async def _list_lessons(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await agent_store.list_lessons(self.db, limit=limit)
        return [_lesson_out(r) for r in rows]

    async def _record_lesson(self, text: str, tags: Sequence[str] = ()) -> dict[str, Any]:
        row = await agent_store.record_lesson(self.db, text, source=self.source, tags=tags)
        return _lesson_out(row)

    async def _propose_pipeline_run(
        self,
        pipeline: str,
        field_set_id: int,
        template_ids: Sequence[str],
        budget: int,
        rationale: str,
    ) -> dict[str, Any]:
        row = await agent_store.create_proposal(
            self.db,
            kind="pipeline_run",
            payload={
                "pipeline": pipeline,
                "field_set_id": field_set_id,
                "template_ids": list(template_ids),
                "budget": budget,
            },
            rationale=rationale,
            source=self.source,
        )
        return {
            "proposal_id": row.id,
            "status": row.status,
            "payload": row.payload,
            "rationale": row.rationale,
        }


def _hypothesis_out(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "mechanism": row.mechanism,
        "expected_sign": row.expected_sign,
        "horizon": row.horizon,
        "pipeline": row.pipeline,
        "template": row.template,
        "fields": list(row.fields or []),
        "status": row.status,
        "verdict": row.verdict,
        "created_at": row.created_at.isoformat(),
    }


def _lesson_out(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "text": row.text,
        "source": row.source,
        "tags": list(row.tags or []),
        "created_at": row.created_at.isoformat(),
    }
