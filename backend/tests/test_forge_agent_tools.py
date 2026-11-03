"""``forge/agent/tools.py``: the model-facing toolbox, and the permission line CLAUDE.md rules
1 and 5 draw around it — read/propose freely, never spend, never submit.
"""

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from alpha_harness.db.sqlite import Database
from alpha_harness.forge.agent import tools as agent_tools
from alpha_harness.forge.agent.tools import TOOL_SCHEMAS, Toolbox, UnknownToolError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "harness.db")
    await database.create_all()
    yield database
    await database.dispose()


# --- permissions (CLAUDE.md rules 1 and 5) -------------------------------------------------


def test_no_tool_name_can_spend_or_submit() -> None:
    agent_tools.assert_no_spending_tool()


def test_no_tool_is_named_or_described_as_a_submit() -> None:
    for schema in TOOL_SCHEMAS:
        assert "submit" not in schema["name"].lower()
        assert "submit" not in schema["description"].lower()


def test_execution_module_is_never_imported_by_tools(repo_src: Path) -> None:
    text = (repo_src / "forge" / "agent" / "tools.py").read_text()
    assert "import execution" not in text
    assert "from .execution" not in text
    assert "from . import execution" not in text


async def test_toolbox_has_no_way_to_reach_a_simulator_or_governor(db: Database) -> None:
    """``Toolbox`` can only do what ``store.py``'s read/propose functions do: nothing in its
    attribute surface is a Simulator or a QuotaGovernor, so even a buggy dispatch could not
    accidentally spend."""
    toolbox = Toolbox(db)
    for attr_name in dir(toolbox):
        value = getattr(toolbox, attr_name, None)
        assert type(value).__name__ not in {"QuotaGovernor", "BrainSimulator", "FakeSimulator"}


async def test_unknown_tool_name_is_refused(db: Database) -> None:
    with pytest.raises(UnknownToolError):
        await Toolbox(db).call("submit_alpha", {})
    with pytest.raises(UnknownToolError):
        await Toolbox(db).call("run_in_reserve_lane", {})


# --- the tools actually work ----------------------------------------------------------------


async def test_propose_hypothesis_then_list_hypotheses_round_trips(db: Database) -> None:
    toolbox = Toolbox(db, source="researcher")
    created = await toolbox.call(
        "propose_hypothesis",
        {"title": "sentiment reversal", "mechanism": "overreaction to news, mean-reverts"},
    )
    assert created["status"] == "proposed"
    listed = await toolbox.call("list_hypotheses", {})
    assert listed[0]["id"] == created["id"]
    assert listed[0]["title"] == "sentiment reversal"


async def test_record_lesson_then_list_lessons_round_trips(db: Database) -> None:
    toolbox = Toolbox(db, source="critic")
    await toolbox.call(
        "record_lesson", {"text": "always test the contrast objection", "tags": ["court"]}
    )
    lessons = await toolbox.call("list_lessons", {})
    assert lessons[0]["text"] == "always test the contrast objection"
    assert lessons[0]["source"] == "critic"


async def test_failures_by_reason_tool_reads_the_store_function(db: Database) -> None:
    out = await Toolbox(db).call("failures_by_reason", {"days": 7})
    assert out == []


async def test_explain_check_reads_courtyaml_repairs() -> None:
    toolbox = Toolbox(None)  # type: ignore[arg-type] - explain_check never touches the db
    out = await toolbox.call("explain_check", {"name": "HIGH_TURNOVER"})
    assert out["check"] == "HIGH_TURNOVER"
    assert out["why"]


async def test_propose_pipeline_run_writes_a_pending_approval_card_and_spends_nothing(
    db: Database,
) -> None:
    toolbox = Toolbox(db, source="researcher")
    out = await toolbox.call(
        "propose_pipeline_run",
        {
            "pipeline": "regular",
            "field_set_id": 1,
            "template_ids": ["t1"],
            "budget": 20,
            "rationale": "test the reversal idea on the PV field set",
        },
    )
    assert out["status"] == "pending"
    assert out["payload"]["pipeline"] == "regular"

    from alpha_harness.forge.agent import store as agent_store

    rows = await agent_store.list_proposals(db)
    assert len(rows) == 1
    assert rows[0].status == "pending"
