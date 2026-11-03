"""``forge/agent/chat.py``: the agent panel's tool-calling loop, context injection, and
persistence — against a stub model, never a real provider (CLAUDE.md rule 10 extends to the
LLM the same way it does to BRAIN: nothing here should cost a cent or need a key)."""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest_asyncio

from alpha_harness.db.sqlite import Database
from alpha_harness.forge.agent import store as agent_store
from alpha_harness.forge.agent.chat import AgentChat, UnknownRoleError
from alpha_harness.forge.board.models import PipelineAlpha

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "harness.db")
    await database.create_all()
    yield database
    await database.dispose()


@dataclass(slots=True)
class _Info:
    ref: str = "stub/model"


@dataclass(slots=True)
class _Answer:
    text: str
    model: str = "stub/model"
    prompt_tokens: int = 10
    output_tokens: int = 5
    thinking_tokens: int = 0
    total_tokens: int = 15

    @property
    def usage(self) -> dict[str, int]:
        return {"promptTokens": self.prompt_tokens, "outputTokens": self.output_tokens}


class StubLLM:
    """Scripts a fixed sequence of replies, one per ``generate()`` call, so a test can drive
    the loop deterministically without a real provider."""

    def __init__(self, replies: Sequence[dict[str, Any]]) -> None:
        self._replies = list(replies)
        self.calls: list[str] = []

    def model_for(self, _model: str | None) -> _Info:
        return _Info()

    async def generate(self, *, system: str, user: str, **_kwargs: Any) -> _Answer:  # noqa: ARG002
        self.calls.append(user)
        return _Answer(json.dumps(self._replies.pop(0)))


async def test_a_plain_turn_round_trips_with_no_tool_call(db: Database) -> None:
    llm = StubLLM([{"reply": "Hello, researcher.", "tool": ""}])
    chat = AgentChat(db, llm)
    out = await chat.say(None, "hi", role="researcher")
    assert out["reply"] == "Hello, researcher."
    assert out["tool_calls"] == []

    _, messages = await chat.thread(out["thread_id"])
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].meta["tool_calls"] == []


async def test_a_tool_call_is_dispatched_and_fed_back_before_the_final_reply(
    db: Database,
) -> None:
    await agent_store.record_lesson(db, "watch turnover", source="user", tags=["turnover"])
    llm = StubLLM(
        [
            {"reply": "", "tool": "list_lessons", "args": {"limit": 5}},
            {"reply": "You already learned: watch turnover.", "tool": ""},
        ]
    )
    chat = AgentChat(db, llm)
    out = await chat.say(None, "what have I learned?", role="researcher")
    assert out["reply"] == "You already learned: watch turnover."
    assert len(out["tool_calls"]) == 1
    assert out["tool_calls"][0]["tool"] == "list_lessons"
    assert out["tool_calls"][0]["result"][0]["text"] == "watch turnover"
    # The second call's prompt carries the first tool's result.
    assert "watch turnover" in llm.calls[1]


async def test_an_unknown_tool_name_is_reported_back_rather_than_raised(db: Database) -> None:
    llm = StubLLM(
        [
            {"reply": "", "tool": "delete_everything", "args": {}},
            {"reply": "I can't do that; here is what I can do instead.", "tool": ""},
        ]
    )
    chat = AgentChat(db, llm)
    out = await chat.say(None, "clean house", role="researcher")
    assert "error" in out["tool_calls"][0]
    assert out["reply"] == "I can't do that; here is what I can do instead."


async def test_running_out_of_tool_calls_still_returns_something(db: Database) -> None:
    always_a_tool = [{"reply": "", "tool": "list_lessons", "args": {}}] * 10
    llm = StubLLM(always_a_tool)
    chat = AgentChat(db, llm)
    out = await chat.say(None, "keep digging", role="researcher")
    assert out["reply"]
    assert len(out["tool_calls"]) == 4  # MAX_TOOL_CALLS + the final forced turn


async def test_context_is_folded_into_the_prompt_and_stored_on_the_message(db: Database) -> None:
    llm = StubLLM([{"reply": "ok", "tool": ""}])
    chat = AgentChat(db, llm)
    context = {"screen": "forge/board", "selected_alpha_id": "ALPHA1"}
    out = await chat.say(None, "what about this one?", role="analyst", context=context)
    assert "forge/board" in llm.calls[0]
    assert "ALPHA1" in llm.calls[0]
    _, messages = await chat.thread(out["thread_id"])
    assert messages[0].meta["context"] == context


async def test_an_unknown_role_is_refused(db: Database) -> None:
    llm = StubLLM([])
    chat = AgentChat(db, llm)
    try:
        await chat.say(None, "hi", role="nonsense")
    except UnknownRoleError:
        pass
    else:
        raise AssertionError("expected UnknownRoleError")


async def test_a_second_message_continues_the_same_thread_with_history(db: Database) -> None:
    llm = StubLLM([{"reply": "first", "tool": ""}, {"reply": "second", "tool": ""}])
    chat = AgentChat(db, llm)
    first = await chat.say(None, "one", role="researcher")
    second = await chat.say(first["thread_id"], "two", role="researcher")
    assert second["thread_id"] == first["thread_id"]
    assert "one" in llm.calls[1]
    assert "first" in llm.calls[1]


async def test_reading_the_trial_journal_through_the_loop_reflects_pipeline_alpha_rows(
    db: Database,
) -> None:
    """Sanity check that the loop's tools reach the same tables the Board already shows —
    ``failures_by_reason`` through the panel, not a second copy of the data."""
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
    llm = StubLLM(
        [
            {"reply": "", "tool": "failures_by_reason", "args": {"days": 30}},
            {"reply": "HIGH_TURNOVER is your top failure.", "tool": ""},
        ]
    )
    chat = AgentChat(db, llm)
    out = await chat.say(None, "what keeps failing?", role="researcher")
    assert out["tool_calls"][0]["result"] == [{"check": "HIGH_TURNOVER", "count": 1}]
