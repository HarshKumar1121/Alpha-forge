"""``/api/forge/submit-helper``: a last-mile check before a competition deadline.

A competition usually asks for two separate things before you may submit an alpha: its
**self-correlation** (vs your own pool) and its **production correlation** (vs BRAIN's
production universe), each under some cutoff. ``forge/nuggets/correlation.py`` already keeps
these two numbers apart on principle — a screen that averaged them, or showed only one,
would be reporting something BRAIN never said. This router fetches both, per alpha, through
the same cached/rate-limited path the Submittable Board uses, and nothing more.

It then asks a second question the per-alpha numbers cannot answer on their own: among the
alphas that *do* pass, are any of them secretly the same bet? Two alphas can each pass their
individual production-correlation check and still be so correlated *with each other* that
submitting both barely diversifies anything. BRAIN's self-correlation body already carries
pairwise information (which other alpha, and by how much) against the candidate's own pool,
so when every alpha being checked is also in that pool, the rows that reference another id
being checked here give the pairwise picture among just the submitted set — no extra BRAIN
calls. From that small graph (edge = "mutually correlated above the threshold") a maximum
independent set gives the largest subset with no two alphas that collide — i.e. "submit only
these".

No code path here submits anything (CLAUDE.md rule 1): this is read-only decision support
that ends at a ranked list you act on by hand on the BRAIN website.
"""

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import select

from ..brain.errors import BrainError
from ..db.models import BrainCache, utcnow
from ..forge.board import store
from ..forge.board.models import PipelineAlpha
from ..forge.nuggets.correlation import (
    _max_abs_correlation as _max_value,
)
from ..schemas import Out
from ..tasks import spawn
from .deps import State, refuse

router = APIRouter(prefix="/api/forge/submit-helper", tags=["forge-submit-helper"])
log = structlog.get_logger(__name__)
Limit = Query(default=50, ge=1, le=200)

FETCH_KIND = "submit-helper-check"
RESULT_KEY = "submit-helper:last"
#: Re-fetching a correlation inside this window would just re-ask BRAIN the same question it
#: already answered this hour, so it is skipped and the cached answer is reused (rule 3).
CACHE_FRESH_SECONDS = 3600.0
#: Two alphas whose self-correlation exceeds this are "mutually correlated" for the
#: independent-set recommendation — matches the summary line's own wording.
MUTUAL_THRESHOLD = 0.5
#: Common field names BRAIN's correlation records use for "the other alpha in this row".
_ID_KEYS = ("id", "alphaId", "alpha_id", "otherId", "other")


class RecentAlpha(Out):
    id: int
    alpha_id: str
    pipeline: str
    alpha_type: str
    created_at: datetime


class CheckRequest(Out):
    alpha_ids: list[str] = Field(min_length=1, max_length=20)
    #: This competition's pass/fail cutoff on production correlation. Never hardcoded: every
    #: competition sets its own, so the caller always supplies it.
    threshold: float = Field(gt=0.0, le=1.0)


class TaskStarted(Out):
    task_id: str


class SubmitHelperAlphaRow(Out):
    alpha_id: str
    self_correlation: float | None
    prod_correlation: float | None
    last_checked: datetime | None
    verdict: str  # "PASS" | "FAIL" | "UNKNOWN"


class SubmitHelperResult(Out):
    threshold: float
    alphas: list[SubmitHelperAlphaRow]
    #: Pairwise self-correlation among just the submitted alphas, ``alpha_id -> alpha_id -> value``.
    #: Only pairs BRAIN's own records could identify are present.
    matrix: dict[str, dict[str, float]]
    #: Alpha ids recommended to submit: a maximum independent set of the "PASS" alphas over
    #: the mutual-correlation graph.
    recommended: list[str]
    summary: str


def _id_of(row: dict[str, Any]) -> str | None:
    for key in _ID_KEYS:
        v = row.get(key)
        if isinstance(v, str):
            return v
    return None


def _pairwise(body: dict[str, Any], alpha_id: str, wanted: set[str]) -> dict[str, float]:
    """Self-correlation records for ``alpha_id`` restricted to other alphas in ``wanted``.

    BRAIN's self-correlation body compares the candidate against the user's own pool, so when
    every alpha being checked is also in that pool, the rows referencing another id in
    ``wanted`` are exactly the pairwise edges among the submitted set.
    """
    rows = body.get("records") or body.get("rows") or []
    out: dict[str, float] = {}
    for r in rows:
        if not isinstance(r, dict) or r.get("correlation") is None:
            continue
        other = _id_of(r)
        if other is not None and other != alpha_id and other in wanted:
            out[other] = float(r["correlation"])
    return out


def max_independent_set(nodes: list[str], edges: set[frozenset[str]]) -> list[str]:
    """Largest subset of ``nodes`` with no two connected by an edge.

    "Edge" here means "mutually correlated above the threshold" — two alphas that would not
    meaningfully diversify a submission. Exact maximum independent set is NP-hard in general,
    but a competition deadline deals with a handful to low tens of alphas, where the textbook
    greedy approximation — repeatedly drop the node touching the most remaining edges, until
    none are left — is simple, fast, and close enough: it never leaves in a pair known to
    collide, even though it is not guaranteed to find the single largest such set.
    """
    remaining = set(nodes)
    live_edges = {e for e in edges if e <= remaining}
    while live_edges:
        degree: dict[str, int] = dict.fromkeys(remaining, 0)
        for e in live_edges:
            for n in e:
                degree[n] += 1
        worst = max(degree, key=lambda n: degree[n])
        remaining.discard(worst)
        live_edges = {e for e in live_edges if worst not in e}
    return [n for n in nodes if n in remaining]


def _verdict(prod: float | None, threshold: float) -> str:
    if prod is None:
        return "UNKNOWN"
    return "PASS" if prod < threshold else "FAIL"


def _summary(alphas: list[SubmitHelperAlphaRow], recommended: list[str]) -> str:
    total = len(alphas)
    passing = [a for a in alphas if a.verdict == "PASS"]
    npass = len(passing)
    if npass == 0:
        return f"0 of {total} alphas pass PC < threshold."
    collided = npass - len(recommended)
    line = f"{npass} of {total} alphas pass PC < threshold"
    if collided > 0:
        line += (
            f"; of those, {collided} are mutually correlated above {MUTUAL_THRESHOLD:g} — "
            f"consider submitting only {len(recommended)} of them"
        )
    return line + "."


async def _cached(state: State, key: str) -> tuple[dict[str, Any] | None, datetime | None]:
    async with state.db.session() as session:
        row = await session.get(BrainCache, key)
    if row is None:
        return None, None
    return row.body, row.fetched_at


def _is_fresh(fetched_at: datetime | None, now: datetime) -> bool:
    if fetched_at is None:
        return False
    return (now - fetched_at).total_seconds() < CACHE_FRESH_SECONDS


async def _fetch_alpha(
    state: State, alpha_id: str, now: datetime
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, datetime | None]:
    """Both correlation stages for one alpha, from cache when fresh, else from BRAIN.

    Self and prod are fetched and cached independently under their own ``correlation:<kind>:``
    key — the same keys the Submittable Board writes — so a fetch paid for on either screen is
    never paid for again on the other (rule 3), and neither is ever re-asked within the hour.
    """
    last_checked: datetime | None = None
    bodies: dict[str, dict[str, Any] | None] = {}
    for kind in ("self", "prod"):
        key = f"correlation:{kind}:{alpha_id}"
        body, fetched_at = await _cached(state, key)
        if body is None or not _is_fresh(fetched_at, now):
            try:
                body = await state.endpoints.correlations(alpha_id, kind)
            except BrainError as exc:
                log.warning("submit_helper.fetch_failed", key=key, error=str(exc))
            else:
                await store.keep(state.db, key, body)
                fetched_at = now
        bodies[kind] = body
        if fetched_at is not None and (last_checked is None or fetched_at > last_checked):
            last_checked = fetched_at
    return bodies.get("self"), bodies.get("prod"), last_checked


async def _run(
    state: State, alpha_ids: list[str], threshold: float, report: Any
) -> SubmitHelperResult:
    now = utcnow()
    wanted = set(alpha_ids)
    rows: list[SubmitHelperAlphaRow] = []
    self_bodies: dict[str, dict[str, Any] | None] = {}
    for n, alpha_id in enumerate(alpha_ids, 1):
        await report(f"Checking {n}/{len(alpha_ids)}: {alpha_id}")
        self_body, prod_body, last_checked = await _fetch_alpha(state, alpha_id, now)
        self_bodies[alpha_id] = self_body
        self_val = _max_value(self_body) if self_body else None
        prod_val = _max_value(prod_body) if prod_body else None
        rows.append(
            SubmitHelperAlphaRow(
                alpha_id=alpha_id,
                self_correlation=self_val,
                prod_correlation=prod_val,
                last_checked=last_checked,
                verdict=_verdict(prod_val, threshold),
            )
        )

    matrix: dict[str, dict[str, float]] = {}
    edges: set[frozenset[str]] = set()
    for alpha_id, body in self_bodies.items():
        if not body:
            continue
        pairs = _pairwise(body, alpha_id, wanted)
        if pairs:
            matrix[alpha_id] = pairs
            for other, value in pairs.items():
                if abs(value) > MUTUAL_THRESHOLD:
                    edges.add(frozenset((alpha_id, other)))

    passing = [r.alpha_id for r in rows if r.verdict == "PASS"]
    recommended = max_independent_set(passing, edges)
    ranked = sorted(
        rows, key=lambda r: r.self_correlation if r.self_correlation is not None else 1.0
    )
    return SubmitHelperResult(
        threshold=threshold,
        alphas=ranked,
        matrix=matrix,
        recommended=recommended,
        summary=_summary(rows, recommended),
    )


@router.get("/recent")
async def recent(state: State, limit: int = Limit) -> list[RecentAlpha]:
    """Recent pipeline alphas, newest first, to pick from instead of pasting ids by hand."""
    async with state.db.session() as session:
        found = (
            await session.scalars(
                select(PipelineAlpha).order_by(PipelineAlpha.created_at.desc()).limit(limit)
            )
        ).all()
    return [
        RecentAlpha(
            id=r.id,
            alpha_id=r.alpha_id,
            pipeline=r.pipeline,
            alpha_type=r.alpha_type,
            created_at=r.created_at,
        )
        for r in found
    ]


@router.post("/check")
async def check(body: CheckRequest, state: State) -> TaskStarted:
    """Start checking self- and production correlation for a hand-picked set of alphas."""
    if any(t["kind"] == FETCH_KIND and t["state"] == "running" for t in state.tasks.list_tasks()):
        raise refuse(409, "already_checking", "A submit-helper check is already running.")
    task = await state.tasks.start(FETCH_KIND, "Submit Helper: checking correlations")

    async def report(detail: str) -> None:
        await state.tasks.update(task, detail=detail)

    async def execute() -> None:
        try:
            result = await _run(state, body.alpha_ids, body.threshold, report)
            await store.keep(state.db, RESULT_KEY, result.model_dump(mode="json", by_alias=True))
            await state.tasks.finish(task, state="done")
        except Exception as exc:  # noqa: BLE001 - surfaced through the task record
            await state.tasks.finish(task, state="failed", error=str(exc))

    spawn(execute(), name="submit-helper-check")
    return TaskStarted(task_id=task.id)


@router.get("/result")
async def result(state: State) -> SubmitHelperResult | None:
    """The last completed check, if any. Free: reads only what was already kept."""
    body, _ = await _cached(state, RESULT_KEY)
    if body is None:
        return None
    return SubmitHelperResult.model_validate(body)
