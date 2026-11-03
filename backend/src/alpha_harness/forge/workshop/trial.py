"""Running a Hypothesis Court trial for real, in the ``validate`` lane, and promoting survivors.

Two entry points, both spending real quota (never from a test — CLAUDE.md rule 4 means
``tests/test_forge_workshop_*.py`` drive these through a fake engine, the same harness
``test_forge_pipelines_api.py`` built):

- :func:`run_trial` is reached from ``POST /api/forge/workshop/trials``, same shape as a
  pipeline run: it costs quota the moment it is called, so the route runs it as a background
  task, never inline on the request.
- :func:`execute_promotion` is reached only from
  :func:`~..agent.execution.execute_proposal`, once a human has clicked Approve on the
  ``court_promotion`` proposal :func:`promotion_payload` built — the same two-gate shape
  ``pipeline_run`` proposals already use (CLAUDE.md rule 5).
"""

from typing import TYPE_CHECKING, Any

from ..alpha_types import load_all as load_alpha_types
from ..data.fieldsets import resolve
from ..pipelines.brain_adapter import BrainSimulator
from ..research.binding import field_infos
from ..research.court import Court
from ..research.planner import Researcher
from ..store import InMemoryResearchStore

if TYPE_CHECKING:
    from ...state import AppState
    from ..research.court import Case
    from ..research.planner import Report
    from .models import WorkshopTrial

#: The only lane a Court trial spends from (config/forge/lanes.yaml: "the Court, repairs").
VALIDATE_LANE = "validate"
#: The only lane an approved promotion spends from, same rule ``execution.py`` enforces for
#: ``pipeline_run`` proposals — a human's Approve click never buys more than the reserve share.
RESERVE_LANE = "reserve"


async def run_trial(
    state: AppState,
    *,
    pipeline_name: str,
    field_set_id: int,
    hypothesis: dict[str, Any],
    fields: list[str],
    test_budget: int = 15,
) -> Case:
    # Local import: the same cross-module reach ``agent/execution.py`` already uses, so this
    # module's own callers (never the agent's tool-use loop) stay the only path that can spend.
    from ...api import forge_pipelines as pipelines_api

    pipeline = pipelines_api._pipeline(pipeline_name)
    types = load_alpha_types()
    row = await pipelines_api._field_set(state, field_set_id)
    alpha_type = pipelines_api._scoped_to(types[pipeline.alpha_type], row)
    resolved = await resolve(row, state.queries)
    court = Court(
        InMemoryResearchStore(),
        BrainSimulator(state.engine, state.db, state.endpoints, VALIDATE_LANE),
        alpha_type,
        hypothesis,
        fields,
        test_budget=test_budget,
        scope=row.name,
        # So a hypothesis naming a real template can bind these fields to its slots (each
        # field's dataset must be known for the same-dataset/semantic-pairing rules to apply).
        field_infos=field_infos(resolved["fields"]),
    )
    return await court.run()


def promotion_payload(trial: WorkshopTrial, *, budget: int, force: bool = False) -> dict[str, Any]:
    """What a ``court_promotion`` proposal needs to re-run the surviving fields later."""
    verdicts: dict[str, str] = (trial.case or {}).get("verdicts", {})
    keep = [f for f, v in verdicts.items() if v == "SURVIVES" or (force and v == "WOUNDED")]
    return {"trial_id": trial.id, "fields": keep, "budget": budget}


async def execute_promotion(
    state: AppState, trial: WorkshopTrial, payload: dict[str, Any]
) -> Report:
    """Turn a trial's survivors into alphas with the budgeted planner, in the reserve lane.

    Re-reads the pipeline and field set from the trial row rather than trusting anything in
    ``payload`` beyond the budget and the field list: the same field set, scoped the same way
    :func:`run_trial` scoped it for the Court, so a promoted alpha simulates under the settings
    it was actually tested against.
    """
    from ...api import forge_pipelines as pipelines_api

    fields = payload["fields"]
    if not fields:
        raise ValueError(f"Trial {trial.id} has no surviving fields to promote.")
    pipeline = pipelines_api._pipeline(trial.pipeline)
    types = load_alpha_types()
    row = await pipelines_api._field_set(state, trial.field_set_id)
    alpha_type = pipelines_api._scoped_to(types[pipeline.alpha_type], row)
    planner = Researcher(
        InMemoryResearchStore(),
        BrainSimulator(state.engine, state.db, state.endpoints, RESERVE_LANE),
        alpha_type,
        trial.hypothesis,
        fields,
        payload["budget"],
        scope=row.name,
    )
    return await planner.run()
