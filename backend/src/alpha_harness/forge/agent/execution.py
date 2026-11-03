"""The only place an agent proposal can turn into a real simulation.

Deliberately its own module, never imported by :mod:`.tools`: :func:`execute_proposal` is
reachable only from ``POST /api/forge/agent/proposals/{id}/approve`` (``api/forge_agent.py``),
a plain REST route the model's tool-use loop never calls — the model can only reach
``propose_pipeline_run``, which writes a row and spends nothing. CLAUDE.md rule 5 ("spends only
from the reserve lane after the user clicks Approve") is enforced twice here, not once:

1. :func:`execute_proposal` refuses anything whose ``status`` is not already ``"approved"``,
   which only a human clicking Approve (or a test standing in for one) can have set.
2. The lane is forced to ``"reserve"`` unconditionally, by replacing whatever lane the proposed
   pipeline's own config names — an agent proposing the ``forge_regular`` pipeline still spends
   out of ``reserve``, never out of that pipeline's own share of the day.
"""

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..pipelines import store as pipelines_store
from . import store as agent_store

if TYPE_CHECKING:
    from ...state import AppState
    from ..pipelines.orchestrator import PipelineReport
    from .models import AgentProposal

#: The only lane an approved proposal may ever spend from, whatever the pipeline config says.
RESERVE_LANE = "reserve"


class ApprovalRequiredError(PermissionError):
    """Raised when a proposal that is not ``approved`` is handed to :func:`execute_proposal`."""


async def execute_proposal(state: AppState, proposal: AgentProposal) -> PipelineReport:
    if proposal.status != "approved":
        raise ApprovalRequiredError(
            f"Proposal {proposal.id} is {proposal.status!r}, not approved; nothing was run."
        )
    if proposal.kind != "pipeline_run":
        raise ValueError(f"Don't know how to execute proposal kind {proposal.kind!r}")

    # Imported here, not at module level: these names come from the API layer, and keeping
    # that import local to the one function that needs it keeps `tools.py`'s
    # `assert_no_spending_tool` check meaningful (nothing in this module graph is pulled in by
    # importing `tools`, since nothing in `tools.py` imports `execution.py` either).
    from ...api import forge_pipelines as pipelines_api
    from ..pipelines.orchestrator import PipelineRun

    # Looked up as an attribute of the module, not imported by name, so a test that patches
    # ``forge_pipelines.BrainSimulator`` (a faster poll interval, no real wall-clock wait)
    # patches this call too — the same simulator class the plain pipelines route uses.
    brain_simulator_cls = pipelines_api.BrainSimulator

    payload: dict[str, Any] = proposal.payload
    plan = await pipelines_api._build_plan(
        pipelines_api.RunRequest(
            pipeline=payload["pipeline"],
            field_set_id=payload["field_set_id"],
            template_ids=list(payload["template_ids"]),
            budget=payload["budget"],
        ),
        state,
    )
    reserved = replace(plan.pipeline, lane=RESERVE_LANE)

    run_id = f"agent-proposal-{proposal.id}"
    await pipelines_store.start_run(
        state.db,
        run_id,
        pipeline=reserved.name,
        field_set=plan.field_set.name,
        lane=RESERVE_LANE,
        template_ids=[t.id for t in plan.templates],
        budget=plan.budget,
    )

    run = PipelineRun(
        plan.run.store,
        brain_simulator_cls(state.engine, state.db, state.endpoints, RESERVE_LANE),
        plan.run.governor,
        plan.run.checker,
        reserved,
        plan.run.type,
        plan.run.field_set,
        plan.templates,
        budget=plan.budget,
        target_dataset=plan.run.target_dataset,
    )
    try:
        report = await run.run()
    except Exception as exc:
        await pipelines_store.finish_run(state.db, run_id, status="failed", error=str(exc))
        await agent_store.set_proposal_status(state.db, proposal.id, "failed", error=str(exc))
        raise

    from ..board.store import record_run

    await record_run(
        state.db,
        report,
        pipeline=reserved.name,
        alpha_type=reserved.alpha_type,
        correlation=reserved.correlation_kind,
        run_id=run_id,
    )
    await pipelines_store.finish_run(
        state.db,
        run_id,
        status="done",
        report={
            "status": "done",
            "pipeline": run_id,
            "spent": report.research.spent,
            "candidates": [{"expression": c.expression} for c in report.research.candidates],
            "finalists": [{"expression": c.expression} for c in report.research.finalists],
            "rejected": [{"expression": e, "reason": r} for e, r in report.rejected],
            "checks": report.checks,
            "correlations": report.correlations,
        },
    )
    await agent_store.set_proposal_status(state.db, proposal.id, "executed", run_id=run_id)
    return report
