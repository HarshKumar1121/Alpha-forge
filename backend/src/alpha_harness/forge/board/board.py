"""The Submittable Board, assembled from what is already known. Pure: no BRAIN, no database.

Grouping is day -> alpha type -> family:

- **Comparison sets.** Families are clustered only inside the set BRAIN itself compares an
  alpha with (``brain/platform.py``): Power Pool alphas with Power Pool alphas, everything else
  with the non-Power-Pool alphas of the same region. Two alphas BRAIN never compares cannot
  block each other, so they never share a family here either.
- **Families.** Leader clustering on daily PnL (:mod:`..research.families`), threshold from
  ``learning.yaml``. The leader is the best TRAIN Sharpe (CLAUDE.md rule 6). An alpha whose PnL
  has not been fetched yet falls back to a structural family: same template on the same field.
- **Ranking.** Ready leaders are ranked by what they ADD to your pool, BRAIN's own
  ``before-and-after-performance`` score change, then by train Sharpe.
"""

import dataclasses
import math
from dataclasses import dataclass
from datetime import date  # noqa: TC003 - pydantic reads it at runtime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ...brain.platform import (
    PLATFORM_ALPHA_URL,
    POWER_POOL_CORRELATION_CEILING,
    SELF_CORRELATION_CEILING,
)
from ...schemas import Out
from .. import analytics as an
from .. import config
from ..research import families as fam
from . import checks as ck

if TYPE_CHECKING:
    import pandas as pd

DEFAULT_THRESHOLD = 0.6


# --- inputs ---------------------------------------------------------------------------


@dataclass(slots=True)
class Entry:
    """One pipeline alpha with everything already fetched for it (``None`` where nothing is)."""

    alpha_id: str
    day: date
    alpha_type: str
    pipeline: str = ""
    region: str = ""
    expression: str = ""
    field: str = ""
    template_id: str = ""
    stage: str = ""
    train_sharpe: float | None = None
    test_sharpe: float | None = None
    train_fitness: float | None = None
    turnover: float | None = None
    # Not `field(...)`: this class has an attribute named `field`, which shadows it here.
    checks: list[dict[str, Any]] = dataclasses.field(default_factory=list[dict[str, Any]])
    #: ``check`` once BRAIN's Check Submission answered; ``simulation`` for the sim's own checks.
    checks_source: str | None = None
    correlation: dict[str, Any] | None = None
    performance: dict[str, Any] | None = None
    pnl: pd.Series | None = None
    description: DescriptionOut | None = None
    repairs_queued: int = 0


# --- wire shapes ----------------------------------------------------------------------


class CheckOut(Out):
    name: str
    result: str
    #: ``gating`` decides submission; ``quota`` is today's allowance; ``label`` never decides.
    role: str
    value: Any = None
    limit: Any = None


class RepairOut(Out):
    check: str
    #: The ``court.yaml`` repair; ``null`` when only advice exists.
    key: str | None
    why: str
    needs: list[str]


class CorrelationOut(Out):
    kind: str
    max: float | None
    ceiling: float
    over: bool


class AddsOut(Out):
    """What submitting this alpha does to your pool, from BRAIN's before/after."""

    score_before: float | None
    score_after: float | None
    score_delta: float | None
    sharpe_before: float | None
    sharpe_after: float | None


class DescriptionOut(Out):
    text: str
    edited: bool
    checks: list[CheckOut] = Field(default_factory=list[CheckOut])


class AlphaOut(Out):
    alpha_id: str
    alpha_type: str
    pipeline: str
    region: str
    expression: str
    stage: str
    train_sharpe: float | None
    #: Held-out years. Shown once, labelled; never used to rank (CLAUDE.md rule 6).
    test_sharpe: float | None
    train_fitness: float | None
    turnover: float | None
    status: ck.Status
    checks_source: str | None
    checks: list[CheckOut]
    power_pool: bool
    #: Today's submission allowance is spent: ready, but submit after BRAIN's reset.
    quota_blocked: list[str]
    why_not: list[str]
    repairs: list[RepairOut]
    correlation: CorrelationOut | None
    adds: AddsOut | None
    description: DescriptionOut | None
    #: A member's PnL correlation with its family's leader.
    leader_correlation: float | None
    repairs_queued: int
    brain_url: str


class FamilyOut(Out):
    id: str
    comparison_set: str
    #: ``pnl`` when clustered on daily PnL; ``structure`` when PnL was missing.
    basis: Literal["pnl", "structure"]
    leader: AlphaOut
    members: list[AlphaOut]


class TypeGroup(Out):
    alpha_type: str
    families: list[FamilyOut]
    ready: int
    alphas: int


class DayGroup(Out):
    day: date
    types: list[TypeGroup]


class ChecklistItem(Out):
    rank: int
    alpha_id: str
    alpha_type: str
    region: str
    expression: str
    train_sharpe: float | None
    score_delta: float | None
    power_pool: bool
    brain_url: str
    steps: list[str]


class Missing(Out):
    """What the board would show with one more fetch (free: no simulations)."""

    pnl: int
    checks: int
    correlations: int
    performance: int


class Board(Out):
    days: list[DayGroup]
    checklist: list[ChecklistItem]
    missing: Missing
    family_threshold: float


# --- reading what BRAIN sent ----------------------------------------------------------


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, int | float) or math.isnan(v):
        return None
    return float(v)


def adds(performance: dict[str, Any] | None) -> AddsOut | None:
    """The score change from BRAIN's before/after body; ``None`` when it has none."""
    if not performance or performance.get("cached") is False:
        return None
    score = performance.get("score") or {}
    stats = performance.get("stats") or {}
    before, after = _num(score.get("before")), _num(score.get("after"))
    return AddsOut(
        score_before=before,
        score_after=after,
        score_delta=None if after is None else after - (before or 0.0),
        sharpe_before=_num((stats.get("before") or {}).get("sharpe")),
        sharpe_after=_num((stats.get("after") or {}).get("sharpe")),
    )


def correlation_kind(power_pool: bool) -> str:
    """The correlation BRAIN gates this alpha on: Power Pool among Power Pool, else self."""
    return "power-pool" if power_pool else "self"


def correlation_of(body: dict[str, Any] | None, power_pool: bool) -> CorrelationOut | None:
    if not body or body.get("cached") is False:
        return None
    ceiling = POWER_POOL_CORRELATION_CEILING if power_pool else SELF_CORRELATION_CEILING
    top = _num(body.get("max"))
    return CorrelationOut(
        kind=correlation_kind(power_pool),
        max=top,
        ceiling=ceiling,
        over=top is not None and top >= ceiling,
    )


def is_power_pool(e: Entry) -> bool:
    """BRAIN's classification when its checks are in, else what the pipeline aimed at."""
    return ck.read(e.checks).power_pool if e.checks else e.alpha_type == "power_pool"


def comparison_set(e: Entry) -> str:
    return f"{'Power Pool' if is_power_pool(e) else 'Regular'} · {e.region or '?'}"


# --- families -------------------------------------------------------------------------


@dataclass(slots=True)
class Cluster:
    leader: str
    members: list[str]
    basis: Literal["pnl", "structure"]


def threshold() -> float:
    return float((config.learning().get("families") or {}).get("pnl_corr_threshold", 0.6))


def _score(e: Entry) -> float:
    return math.nan if e.train_sharpe is None else e.train_sharpe


def cluster(entries: list[Entry], corr_threshold: float) -> list[Cluster]:
    """Families inside one comparison set: PnL leader clustering, structure for the rest."""
    with_pnl = {e.alpha_id: e.pnl for e in entries if e.pnl is not None and len(e.pnl)}
    scores = {e.alpha_id: _score(e) for e in entries}
    out = [
        Cluster(f.leader, f.members, "pnl")
        for f in fam.cluster(with_pnl, scores, corr_threshold)  # pyright: ignore[reportArgumentType]
    ]
    by_signature: dict[str, list[Entry]] = {}
    for e in entries:
        if e.alpha_id not in with_pnl:
            by_signature.setdefault(fam.signature([e.field], e.template_id), []).append(e)
    for group in by_signature.values():
        ranked = sorted(group, key=lambda e: -_score(e) if e.train_sharpe is not None else math.inf)
        out.append(Cluster(ranked[0].alpha_id, [e.alpha_id for e in ranked], "structure"))
    return out


# --- one alpha ------------------------------------------------------------------------


def _fmt(v: Any) -> str:
    return f"{v:.3g}" if isinstance(v, int | float) and not isinstance(v, bool) else str(v)


def _alpha(
    e: Entry,
    *,
    leader: Entry | None = None,
    leader_corr: float | None = None,
    basis: str = "pnl",
) -> AlphaOut:
    reading = ck.read(e.checks)
    pp = reading.power_pool if e.checks else e.alpha_type == "power_pool"
    corr = correlation_of(e.correlation, pp)
    why: list[str] = []
    repairs: list[RepairOut] = []
    if leader is not None:
        why.append(
            f"Same bet as {leader.alpha_id}"
            + (
                f" (PnL correlation {leader_corr:.2f})"
                if leader_corr is not None
                else " (same template on the same field; no PnL fetched yet)"
                if basis == "structure"
                else ""
            )
            + ": one submission per family, and the leader has the better train Sharpe."
        )
    if reading.status == "unchecked":
        why.append("BRAIN has not checked this alpha yet. Fetch BRAIN data to run its checks.")
    elif reading.status == "pending":
        why.append(f"BRAIN is still judging {', '.join(reading.pending)}. Fetch again later.")
    elif reading.status == "not_ready":
        rows = {r.name: r for r in reading.rows}
        for name in reading.failing:
            r = rows[name]
            limit = "" if r.limit is None else f" (value {_fmt(r.value)}, limit {_fmt(r.limit)})"
            why.append(f"{name} failed{limit}.")
            fix = ck.repair_for(name)
            repairs.append(
                RepairOut(check=fix.check, key=fix.key, why=fix.why, needs=list(fix.needs))
            )
    if corr is not None and corr.over and reading.status != "not_ready":
        why.append(
            f"{corr.kind} correlation {_fmt(corr.max)} is at or above BRAIN's {corr.ceiling} "
            "ceiling; expect its correlation check to fail."
        )
    desc = e.description
    if desc is not None:
        desc = desc.model_copy(update={"checks": [_check(r) for r in ck.description_rows(reading)]})
    return AlphaOut(
        alpha_id=e.alpha_id,
        alpha_type=e.alpha_type,
        pipeline=e.pipeline,
        region=e.region,
        expression=e.expression,
        stage=e.stage,
        train_sharpe=e.train_sharpe,
        test_sharpe=e.test_sharpe,
        train_fitness=e.train_fitness,
        turnover=e.turnover,
        status=reading.status,
        checks_source=e.checks_source,
        checks=[_check(r) for r in reading.rows],
        power_pool=pp,
        quota_blocked=reading.quota_blocked,
        why_not=why,
        repairs=repairs,
        correlation=corr,
        adds=adds(e.performance),
        description=desc if pp else None,
        leader_correlation=leader_corr,
        repairs_queued=e.repairs_queued,
        brain_url=f"{PLATFORM_ALPHA_URL}{e.alpha_id}",
    )


def _check(r: ck.CheckRow) -> CheckOut:
    return CheckOut(name=r.name, result=r.result, role=r.role, value=r.value, limit=r.limit)


def _rank_key(a: AlphaOut) -> tuple[int, float, float]:
    """Ready first, then what it adds to the pool, then train Sharpe."""
    delta = a.adds.score_delta if a.adds and a.adds.score_delta is not None else -math.inf
    return (0 if a.status == "ready" else 1, -delta, -(a.train_sharpe or -math.inf))


# --- the board ------------------------------------------------------------------------


def build(entries: list[Entry], corr_threshold: float | None = None) -> Board:
    t = threshold() if corr_threshold is None else corr_threshold
    by_id = {e.alpha_id: e for e in entries}
    days: list[DayGroup] = []
    leaders: list[AlphaOut] = []
    for day in sorted({e.day for e in entries}, reverse=True):
        sets: dict[str, list[Entry]] = {}
        for e in entries:
            if e.day == day:
                sets.setdefault(comparison_set(e), []).append(e)
        by_type: dict[str, list[FamilyOut]] = {}
        for set_name, group in sorted(sets.items()):
            for n, c in enumerate(cluster(group, t), 1):
                lead = by_id[c.leader]
                head = _alpha(lead)
                members = []
                for m in c.members:
                    if m == c.leader:
                        continue
                    me = by_id[m]
                    corr = (
                        an.correlation(me.pnl, lead.pnl, min_overlap=120)
                        if c.basis == "pnl" and me.pnl is not None and lead.pnl is not None
                        else math.nan
                    )
                    members.append(
                        _alpha(
                            me,
                            leader=lead,
                            leader_corr=None if math.isnan(corr) else corr,
                            basis=c.basis,
                        )
                    )
                leaders.append(head)
                by_type.setdefault(lead.alpha_type, []).append(
                    FamilyOut(
                        id=f"{day.isoformat()}:{set_name}:{n}",
                        comparison_set=set_name,
                        basis=c.basis,
                        leader=head,
                        members=members,
                    )
                )
        days.append(
            DayGroup(
                day=day,
                types=[
                    TypeGroup(
                        alpha_type=alpha_type,
                        families=sorted(fams, key=lambda f: _rank_key(f.leader)),
                        ready=sum(f.leader.status == "ready" for f in fams),
                        alphas=sum(1 + len(f.members) for f in fams),
                    )
                    for alpha_type, fams in sorted(by_type.items())
                ],
            )
        )
    return Board(
        days=days,
        checklist=checklist(leaders),
        missing=missing(entries, [a.alpha_id for a in leaders]),
        family_threshold=t,
    )


def checklist(leaders: list[AlphaOut]) -> list[ChecklistItem]:
    """Ready family leaders in the order to submit them by hand on BRAIN. Nothing here submits."""
    ready = sorted((a for a in leaders if a.status == "ready"), key=_rank_key)
    items: list[ChecklistItem] = []
    for rank, a in enumerate(ready, 1):
        steps = [f"Open {a.alpha_id} on BRAIN.", "Run Check Submission; every check must pass."]
        if a.power_pool:
            steps.append("Paste the description draft and confirm its format checks pass.")
        if a.quota_blocked:
            steps.append("Today's submission allowance is used: wait for BRAIN's daily reset.")
        steps.append("Submit, then re-check the next one: each submission moves self-correlation.")
        items.append(
            ChecklistItem(
                rank=rank,
                alpha_id=a.alpha_id,
                alpha_type=a.alpha_type,
                region=a.region,
                expression=a.expression,
                train_sharpe=a.train_sharpe,
                score_delta=a.adds.score_delta if a.adds else None,
                power_pool=a.power_pool,
                brain_url=a.brain_url,
                steps=steps,
            )
        )
    return items


def missing(entries: list[Entry], leader_ids: list[str]) -> Missing:
    leads = [e for e in entries if e.alpha_id in set(leader_ids)]
    return Missing(
        pnl=sum(e.pnl is None for e in entries),
        checks=sum(e.checks_source != "check" for e in leads),
        correlations=sum(e.correlation is None for e in leads),
        performance=sum(e.performance is None for e in leads),
    )
