"""Phase 10: the AI research agent — memory tables, tools, prompts and permissions.

The agent's memory is the database, not a prompt cache: hypotheses, pipeline run history,
failures by reason, and lessons live in the same SQLite file as everything else Forge writes,
so an agent session that started yesterday still remembers what it learned.

Hard rule enforced here (CLAUDE.md rule 5): the agent reads and proposes freely through
:mod:`tools`; it never gets a tool that spends quota. Spending happens only through
:func:`execution.execute_proposal`, which a human triggers from the UI's Approve button, runs
in the ``reserve`` lane unconditionally, and is never put in :data:`tools.TOOL_SCHEMAS`. It also
never gets a submit tool, and never will (CLAUDE.md rule 1).
"""
