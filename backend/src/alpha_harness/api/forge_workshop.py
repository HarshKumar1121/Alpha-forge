"""``/api/forge/workshop``: challenge a hypothesis with your own history, put it on trial in
the Hypothesis Court (``validate`` lane), and turn survivors into a proposal you approve.

Evidence is a free read (same table the Submittable Board already shows). Starting a trial
spends real quota the moment it is called, so — same shape as ``POST /api/forge/pipelines/run``
— it runs in the background and the caller polls ``GET /trials/{id}``. Promoting survivors
writes a ``court_promotion`` proposal that spends nothing until a human clicks Approve on
``POST /api/forge/agent/proposals/{id}/approve`` (CLAUDE.md rule 5) — this router has no
approve route of its own, it reuses the agent's.
"""

from typing import Any

from fastapi import APIRouter
from pydantic import Field

from ..forge.agent import store as agent_store
from ..forge.workshop import store as workshop_store
from ..forge.workshop.trial import promotion_payload, run_trial
from ..schemas import Out
from ..tasks import spawn
from .deps import State, refuse

router = APIRouter(prefix="/api/forge/workshop", tags=["forge-workshop"])


class EvidenceRequest(Out):
    pipeline: str = ""
    fields: list[str] = Field(default_factory=list)
    days: int = Field(default=90, ge=1, le=365)


class Finding(Out):
    check: str
    count: int
    sample_size: int
    no_decay: int
    note: str


class Evidence(Out):
    pipeline: str
    fields: list[str]
    days: int
    sample_size: int
    findings: list[Finding]


class TrialCreate(Out):
    #: An existing hypothesis to pull the title/mechanism/sign/horizon/template from, or
    #: omit and fill them in directly below for an idea never saved to the journal.
    hypothesis_id: int | None = None
    title: str = ""
    mechanism: str = ""
    expected_sign: str = "+"
    horizon: str = ""
    template: str = ""
    template_id: str = ""
    pipeline: str = Field(min_length=1)
    field_set_id: int
    fields: list[str] = Field(min_length=1, max_length=3)
    test_budget: int = Field(default=15, ge=1, le=100)


class TrialOut(Out):
    id: int
    hypothesis_id: int | None
    hypothesis: dict[str, Any]
    pipeline: str
    field_set_id: int
    fields: list[str]
    lane: str
    status: str
    case: dict[str, Any] | None
    verdict: str
    spent: int
    error: str
    proposal_id: int | None
    created_at: str
    finished_at: str | None


class Promote(Out):
    budget: int = Field(ge=1)
    rationale: str = Field(min_length=1)
    #: Promote WOUNDED fields too, not just SURVIVES — the researcher's own call, not the
    #: Court's (:func:`~..forge.workshop.trial.promotion_payload`'s own ``force``).
    force: bool = False


def _trial_out(row: Any) -> TrialOut:
    return TrialOut(
        id=row.id,
        hypothesis_id=row.hypothesis_id,
        hypothesis=row.hypothesis,
        pipeline=row.pipeline,
        field_set_id=row.field_set_id,
        fields=list(row.fields or []),
        lane=row.lane,
        status=row.status,
        case=row.case,
        verdict=row.verdict,
        spent=row.spent,
        error=row.error,
        proposal_id=row.proposal_id,
        created_at=row.created_at.isoformat(),
        finished_at=row.finished_at.isoformat() if row.finished_at else None,
    )


@router.post("/evidence")
async def evidence(body: EvidenceRequest, state: State) -> Evidence:
    """ "Your last N alphas on these fields failed this check" — read, never simulated."""
    out = await workshop_store.challenge(
        state.db, pipeline=body.pipeline, fields=body.fields, days=body.days
    )
    return Evidence.model_validate(out)


async def _hypothesis_dict(body: TrialCreate, state: State) -> dict[str, Any]:
    if body.hypothesis_id is not None:
        rows = await agent_store.list_hypotheses(state.db, limit=1000)
        found = next((h for h in rows if h.id == body.hypothesis_id), None)
        if found is None:
            raise refuse(404, "not_found", f"No hypothesis {body.hypothesis_id}.")
        return {
            "title": found.title,
            "mechanism": found.mechanism,
            "expected_sign": found.expected_sign,
            "horizon": found.horizon,
            "template": found.template,
            "template_id": found.template_id,
        }
    if not body.title or not body.mechanism:
        raise refuse(
            422,
            "hypothesis_required",
            "Give an existing hypothesisId, or a title and mechanism for a new one.",
        )
    return {
        "title": body.title,
        "mechanism": body.mechanism,
        "expected_sign": body.expected_sign,
        "horizon": body.horizon,
        "template": body.template,
        "template_id": body.template_id,
    }


@router.post("/trials")
async def start_trial(body: TrialCreate, state: State) -> TrialOut:
    """Start a Court trial in the background — spends real quota in the ``validate`` lane the
    moment this is called. Poll ``GET /trials/{id}`` for the verdict."""
    hypothesis = await _hypothesis_dict(body, state)
    row = await workshop_store.create_trial(
        state.db,
        hypothesis_id=body.hypothesis_id,
        hypothesis=hypothesis,
        pipeline=body.pipeline,
        field_set_id=body.field_set_id,
        fields=body.fields,
    )

    async def execute() -> None:
        try:
            case = await run_trial(
                state,
                pipeline_name=body.pipeline,
                field_set_id=body.field_set_id,
                hypothesis=hypothesis,
                fields=body.fields,
                test_budget=body.test_budget,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced through the trial record
            await workshop_store.fail_trial(state.db, row.id, error=str(exc))
            return
        await workshop_store.finish_trial(
            state.db, row.id, case=case.to_dict(), verdict=case.verdict, spent=case.spent
        )

    spawn(execute(), name="workshop-trial")
    return _trial_out(row)


@router.get("/trials")
async def list_trials(state: State) -> list[TrialOut]:
    rows = await workshop_store.list_trials(state.db)
    return [_trial_out(r) for r in rows]


@router.get("/trials/{trial_id}")
async def get_trial(trial_id: int, state: State) -> TrialOut:
    row = await workshop_store.get_trial(state.db, trial_id)
    if row is None:
        raise refuse(404, "not_found", f"No trial {trial_id}.")
    return _trial_out(row)


@router.post("/trials/{trial_id}/promote")
async def promote_trial(trial_id: int, body: Promote, state: State) -> dict[str, Any]:
    """Write a ``court_promotion`` approval card for this trial's survivors. Spends nothing
    (CLAUDE.md rule 5) — approve it from ``POST /api/forge/agent/proposals/{id}/approve``."""
    row = await workshop_store.get_trial(state.db, trial_id)
    if row is None:
        raise refuse(404, "not_found", f"No trial {trial_id}.")
    if row.status != "done":
        raise refuse(409, "not_ready", f"Trial {trial_id} is still {row.status}.")
    if row.proposal_id is not None:
        raise refuse(409, "already_promoted", f"Trial {trial_id} already has a proposal.")
    payload = promotion_payload(row, budget=body.budget, force=body.force)
    if not payload["fields"]:
        raise refuse(
            422, "no_survivors", "Nothing survived (or was WOUNDED, with force) to promote."
        )
    proposal = await agent_store.create_proposal(
        state.db,
        kind="court_promotion",
        payload=payload,
        rationale=body.rationale,
        source="workshop",
    )
    await workshop_store.set_trial_proposal(state.db, trial_id, proposal.id)
    return {
        "proposal_id": proposal.id,
        "status": proposal.status,
        "payload": proposal.payload,
        "rationale": proposal.rationale,
    }
