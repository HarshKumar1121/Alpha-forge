"""The agent panel's chat loop.

Builds on the same :class:`~...llm.service.LLMService` every other assistant in this app uses
(``api/chat.py``'s data picker), in the same style: one structured ``generate()`` call per
turn, no provider-native tool-use API. The model names a tool and arguments in its JSON reply;
this loop dispatches it through :class:`~.tools.Toolbox` — reads and the two free writes only,
nothing that can spend quota or submit (CLAUDE.md rules 1 and 5, proved the same way
``tools.py`` proves it for its own callers) — and feeds the result back for the model's next
turn, up to :data:`MAX_TOOL_CALLS` times before it must answer.

The panel is context-aware: the caller passes the screen the researcher is looking at and
whatever alpha/template is selected, folded into the prompt as plain text, not a tool call —
the model reads it like it would read something the researcher typed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ...db.models import utcnow
from ...llm.text import loads_or
from .models import AgentChatMessage, AgentChatThread
from .prompts import ANALYST, CRITIC, RESEARCHER, TUTOR
from .tools import TOOL_SCHEMAS, Toolbox, UnknownToolError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...db.sqlite import Database
    from ...llm.service import LLMService

ROLE_PROMPTS: dict[str, str] = {
    "researcher": RESEARCHER,
    "critic": CRITIC,
    "analyst": ANALYST,
    "tutor": TUTOR,
}
#: How many tool calls one turn may make before it must answer with what it has.
MAX_TOOL_CALLS = 3
#: How much of a thread is replayed to the model each turn.
HISTORY_TURNS = 12

TURN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply": {
            "type": "string",
            "description": "What to show the researcher. May be empty while a tool call is "
            "pending; must be filled in once you are done calling tools.",
        },
        "tool": {
            "type": "string",
            "description": "One of the tool names listed, or empty if you are answering now.",
        },
        "args": {"type": "object", "description": "Arguments for that tool, if any."},
    },
    "required": ["reply"],
}


class UnknownRoleError(ValueError):
    pass


def _tool_menu() -> str:
    return "\n".join(f"- {s['name']}: {s['description']}" for s in TOOL_SCHEMAS)


def _context_block(context: dict[str, Any] | None) -> str:
    lines = [f"{k}: {v}" for k, v in (context or {}).items() if v]
    return "CURRENT SCREEN CONTEXT:\n" + "\n".join(lines) if lines else ""


def _transcript(history: Sequence[AgentChatMessage]) -> str:
    recent = list(history)[-HISTORY_TURNS:]
    if not recent:
        return "THE CONVERSATION SO FAR:\n  (this is the first message)"
    lines = [f"  {'They' if m.role == 'user' else 'You'}: {m.text.strip()[:600]}" for m in recent]
    return "THE CONVERSATION SO FAR:\n" + "\n".join(lines)


class AgentChat:
    """Conversations with the agent's RESEARCHER/CRITIC/ANALYST/TUTOR roles."""

    def __init__(self, db: Database, llm: LLMService) -> None:
        self.db = db
        self.llm = llm

    async def threads(self, limit: int = 30) -> list[AgentChatThread]:
        async with self.db.session() as session:
            return list(
                (
                    await session.scalars(
                        select(AgentChatThread)
                        .order_by(AgentChatThread.updated_at.desc())
                        .limit(limit)
                    )
                ).all()
            )

    async def thread(self, thread_id: int) -> tuple[AgentChatThread, list[AgentChatMessage]] | None:
        async with self.db.session() as session:
            row = await session.get(AgentChatThread, thread_id)
            if row is None:
                return None
            messages = list(
                (
                    await session.scalars(
                        select(AgentChatMessage)
                        .where(AgentChatMessage.thread_id == thread_id)
                        .order_by(AgentChatMessage.id)
                    )
                ).all()
            )
            return row, messages

    async def delete(self, thread_id: int) -> None:
        async with self.db.session() as session:
            row = await session.get(AgentChatThread, thread_id)
            if row is not None:
                await session.delete(row)

    async def say(
        self,
        thread_id: int | None,
        text: str,
        *,
        role: str = "researcher",
        model: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One exchange, with up to :data:`MAX_TOOL_CALLS` tool round-trips folded in before
        the reply the panel shows is settled."""
        if role not in ROLE_PROMPTS:
            raise UnknownRoleError(f"No such agent role: {role!r}")

        history: list[AgentChatMessage] = []
        if thread_id is not None:
            found = await self.thread(thread_id)
            if found is None:
                raise ValueError(f"No conversation {thread_id}.")
            _, history = found

        toolbox = Toolbox(self.db, source=role)
        info = self.llm.model_for(model)
        system = f"{ROLE_PROMPTS[role]}\n\nTOOLS YOU MAY CALL:\n{_tool_menu()}"
        parts = [
            p for p in (_transcript(history), _context_block(context), f"RESEARCHER: {text}") if p
        ]
        prompt = "\n\n---\n\n".join(parts)

        tool_calls: list[dict[str, Any]] = []
        reply_text, answer = "", None
        parsed: dict[str, Any] = {}
        for _ in range(MAX_TOOL_CALLS + 1):
            answer = await self.llm.generate(
                system=system,
                user=prompt,
                model_ref=info.ref,
                temperature=0.4,
                response_schema=TURN_SCHEMA,
                thinking="LOW",
            )
            parsed = loads_or(answer.text) or {"reply": answer.text}
            tool_name = str(parsed.get("tool") or "").strip()
            if not tool_name:
                reply_text = str(parsed.get("reply") or answer.text).strip()
                break
            args = parsed.get("args") or {}
            try:
                result = await toolbox.call(tool_name, args)
                outcome: dict[str, Any] = {"tool": tool_name, "args": args, "result": result}
            except (UnknownToolError, TypeError) as exc:
                outcome = {"tool": tool_name, "args": args, "error": str(exc)}
            tool_calls.append(outcome)
            prompt += f"\n\n---\n\nTOOL RESULT for {tool_name}:\n{outcome}"
        else:
            reply_text = str(parsed.get("reply") or "").strip() or (
                "(used every tool call available this turn without a final answer)"
            )

        if answer is None:  # pragma: no cover - the loop above always runs at least once
            raise RuntimeError("the model was never called")

        if thread_id is None:
            thread_id = (await self._start(text, role)).id
        await self._append(thread_id, "user", text, context=context or {})
        await self._append(
            thread_id,
            "assistant",
            reply_text,
            tool_calls=tool_calls,
            model=answer.model,
            usage=answer.usage,
        )
        return {
            "thread_id": thread_id,
            "role": role,
            "reply": reply_text,
            "tool_calls": tool_calls,
            "model": answer.model,
            "usage": answer.usage,
        }

    async def _start(self, title: str, role: str) -> AgentChatThread:
        async with self.db.session() as session:
            row = AgentChatThread(title=title[:120], role=role)
            session.add(row)
            await session.flush()
            await session.refresh(row)
        return row

    async def _append(self, thread_id: int, role: str, text: str, **meta: Any) -> None:
        async with self.db.session() as session:
            session.add(AgentChatMessage(thread_id=thread_id, role=role, text=text, meta=meta))
            t = await session.get(AgentChatThread, thread_id)
            if t is not None:
                t.updated_at = utcnow()
            await session.flush()
