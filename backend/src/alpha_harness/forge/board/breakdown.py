"""Results Breakdown: what a finished Template Lab task's alphas actually did, read from what
already ran. No simulation is spent building any of this (CLAUDE.md rule 1, rule 4 n/a).

Three questions, each over the same stored rows:

- **Pass rate & Sharpe by slice** (:func:`slice_breakdown`) -- where this task's idea works.
- **Why alphas failed, and what's fixable** (:func:`failure_counts`, :func:`near_misses`) --
  reusing ``court.yaml``'s own repairs (:mod:`.checks`), not a new list of fixes.
- **Submit order by correlation** (:func:`submit_order`) -- families reused exactly from
  :mod:`.board` / :mod:`..research.families`, ordered by what each pick adds to overlap with
  the real submitted pool, cumulatively.

Queueing a repair is not this module's job: it stays the existing ``/api/forge/board/repair``
route, the one path CLAUDE.md rule 3 allows to spend anything here.
"""

import dataclasses
import datetime as dt
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from ...schemas import Out
from .. import analytics as an
from . import board as bd
from . import checks as ck

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pandas as pd

GroupBy = Literal["field", "dataset", "setting"]
SettingName = Literal["neutralization", "decay", "universe"]
_UNUSED_DAY = dt.date(1970, 1, 1)


@dataclass(slots=True)
class AlphaRow:
    """One finished trial, with BRAIN's current checks preferred over the trial's frozen copy."""

    alpha_id: str
    expression: str
    settings: dict[str, Any]
    field: str
    template_id: str
    train_sharpe: float | None
    test_sharpe: float | None
    # Not ``field(...)``: this class has an attribute named ``field``, which shadows it here.
    checks: list[dict[str, Any]] = dataclasses.field(default_factory=list[dict[str, Any]])
    pnl: pd.Series | None = None


def status_of(r: AlphaRow) -> ck.Status:
    return ck.read(r.checks).status


# --- (a) pass rate & Sharpe by slice ----------------------------------------------------


class SliceRow(Out):
    key: str
    n: int
    pass_rate: float | None
    median_train_sharpe: float | None
    median_test_sharpe: float | None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def _keys_for(
    r: AlphaRow,
    group_by: GroupBy,
    setting_name: SettingName | None,
    datasets_of: Mapping[str, set[str]],
) -> list[str]:
    if group_by == "setting":
        value = r.settings.get(setting_name) if setting_name else None
        return [str(value) if value is not None else "?"]
    fields = [r.field] if r.field else []
    if group_by == "field":
        return fields or ["?"]
    # dataset: every dataset any of this alpha's fields files under; a field some market lists
    # under two datasets counts toward both, the same rule ``forge/data/usage.py`` uses.
    found = {d for f in fields for d in datasets_of.get(f, ())}
    return sorted(found) or ["?"]


def slice_breakdown(
    rows: list[AlphaRow],
    group_by: GroupBy,
    setting_name: SettingName | None,
    min_n: int,
    datasets_of: Mapping[str, set[str]],
) -> list[SliceRow]:
    """Pass rate and median Sharpe by slice, a single lucky run held out by ``min_n``."""
    buckets: dict[str, list[AlphaRow]] = {}
    for r in rows:
        for key in _keys_for(r, group_by, setting_name, datasets_of):
            buckets.setdefault(key, []).append(r)
    out: list[SliceRow] = []
    for key, members in buckets.items():
        if len(members) < min_n:
            continue
        statuses = [status_of(m) for m in members]
        checked = [s for s in statuses if s in ("ready", "not_ready")]
        passed = [s for s in statuses if s == "ready"]
        trains = [m.train_sharpe for m in members if m.train_sharpe is not None]
        tests = [m.test_sharpe for m in members if m.test_sharpe is not None]
        out.append(
            SliceRow(
                key=key,
                n=len(members),
                pass_rate=len(passed) / len(checked) if checked else None,
                median_train_sharpe=_median(trains),
                median_test_sharpe=_median(tests),
            )
        )
    return sorted(out, key=lambda s: (-(s.pass_rate if s.pass_rate is not None else -1.0), -s.n))


# --- (b) why alphas failed, and what's fixable ------------------------------------------


class FailureCount(Out):
    check: str
    count: int


def failure_counts(rows: list[AlphaRow]) -> list[FailureCount]:
    tally: dict[str, int] = {}
    for r in rows:
        for name in ck.read(r.checks).failing:
            tally[name] = tally.get(name, 0) + 1
    return [
        FailureCount(check=name, count=n)
        for name, n in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _direction(name: str) -> Literal["higher_is_better", "lower_is_better"]:
    """Read off the check's own name: BRAIN's ``LOW_*`` checks fail below their limit, a
    ``HIGH_*``/``CONCENTRATED_*`` check fails above it. The same rule :func:`sub_universe_margin`
    (``vault/yields.py``) already uses for its one check, generalised to every repairable one."""
    upper = name.upper()
    return "lower_is_better" if upper.startswith(("HIGH_", "CONCENTRATED")) else "higher_is_better"


def check_margin(name: str, value: Any, limit: Any) -> float | None:
    """How close a failing check came to its own limit: 1.0 at the limit, lower further off.

    Both directions are expressed as ``smaller / larger`` so the number means the same thing
    either way: the share of the limit this check's value actually reached.
    """
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    if not isinstance(limit, int | float) or isinstance(limit, bool) or limit == 0:
        return None
    value, limit = float(value), float(limit)
    ratio = value / limit if _direction(name) == "higher_is_better" else limit / value
    return None if ratio <= 0 else min(ratio, 1.0)


class NearMiss(Out):
    alpha_id: str
    field: str
    failed_check: str
    repair_key: str
    why: str
    value: float
    limit: float
    margin: float


def near_misses(rows: list[AlphaRow], margin_cutoff: float) -> list[NearMiss]:
    """Fails exactly one gating check, that check has a ``court.yaml`` repair, and the failing
    value is within ``margin_cutoff`` of the check's own limit (:func:`check_margin`)."""
    out: list[NearMiss] = []
    for r in rows:
        reading = ck.read(r.checks)
        if len(reading.failing) != 1:
            continue
        name = reading.failing[0]
        repair = ck.repair_for(name)
        if repair.key is None:
            continue
        row = next((c for c in reading.rows if c.name == name), None)
        if row is None:
            continue
        margin = check_margin(name, row.value, row.limit)
        if margin is None or margin < margin_cutoff:
            continue
        out.append(
            NearMiss(
                alpha_id=r.alpha_id,
                field=r.field,
                failed_check=name,
                repair_key=repair.key,
                why=repair.why,
                value=float(row.value),
                limit=float(row.limit),
                margin=margin,
            )
        )
    return sorted(out, key=lambda n: -n.margin)


# --- (c) submit order by correlation -----------------------------------------------------


class SubmitCandidate(Out):
    alpha_id: str
    expression: str
    family_size: int
    train_sharpe: float | None
    #: ``None`` when no PnL is stored yet for this alpha or for anything it could be measured
    #: against -- it is still listed, just not scored.
    best_correlation: float | None
    best_correlation_with: str | None
    high_overlap: bool
    #: Whether BRAIN's own self- and production-correlation checks have ever been fetched for
    #: this alpha (Submittable Board or Submit Helper both write the same cache keys). This is
    #: independent of ``best_correlation``, which is a *local* PnL correlation and never reads
    #: BRAIN's own checks -- see the module docstring.
    has_check_data: bool


def _entry(r: AlphaRow) -> bd.Entry:
    # ``bd.cluster`` reads only alpha_id/pnl/train_sharpe/field/template_id off an Entry; the
    # rest of the dataclass is board-building machinery this view has no use for.
    return bd.Entry(
        alpha_id=r.alpha_id,
        # Unused by ``bd.cluster``: it reads only alpha_id/pnl/train_sharpe/field/template_id.
        day=_UNUSED_DAY,
        alpha_type="",
        expression=r.expression,
        field=r.field,
        template_id=r.template_id,
        train_sharpe=r.train_sharpe,
        pnl=r.pnl,
    )


def submit_order(
    passing: list[AlphaRow],
    pool: dict[str, pd.Series],
    family_threshold: float,
    overlap_ceiling: float,
    checked: set[str] | None = None,
) -> list[SubmitCandidate]:
    """One leader per family (:mod:`.board`'s own clustering, reused exactly), ordered
    fewest-correlated-first against ``pool`` -- cumulatively: once a leader is placed, its PnL
    joins ``pool`` for every pick after it, so two near-duplicate families never both rank early."""
    if not passing:
        return []
    checked = checked or set()
    by_id = {r.alpha_id: r for r in passing}
    clusters = bd.cluster([_entry(r) for r in passing], family_threshold)
    leaders = [by_id[c.leader] for c in clusters]
    family_size = {c.leader: len(c.members) for c in clusters}

    cumulative = dict(pool)
    #: Paired with its PnL so the type checker knows it is never ``None`` inside the loop below.
    measurable: list[tuple[AlphaRow, pd.Series]] = [
        (r, r.pnl) for r in leaders if r.pnl is not None and len(r.pnl)
    ]
    unmeasurable = [r for r in leaders if r.pnl is None or not len(r.pnl)]
    order: list[SubmitCandidate] = []
    while measurable:
        best_idx = 0
        best_score = math.inf
        best_against: str | None = None
        for i, (_, pnl) in enumerate(measurable):
            against, corr = an.max_correlation(pnl, cumulative) if cumulative else (None, math.nan)
            score = 0.0 if math.isnan(corr) else corr
            if score < best_score:
                best_idx, best_score, best_against = i, score, against
        best_r, best_pnl = measurable.pop(best_idx)
        order.append(
            SubmitCandidate(
                alpha_id=best_r.alpha_id,
                expression=best_r.expression,
                family_size=family_size.get(best_r.alpha_id, 1),
                train_sharpe=best_r.train_sharpe,
                best_correlation=best_score,
                best_correlation_with=best_against,
                high_overlap=best_score > overlap_ceiling,
                has_check_data=best_r.alpha_id in checked,
            )
        )
        cumulative[best_r.alpha_id] = best_pnl
    order.extend(
        SubmitCandidate(
            alpha_id=r.alpha_id,
            expression=r.expression,
            family_size=family_size.get(r.alpha_id, 1),
            train_sharpe=r.train_sharpe,
            best_correlation=None,
            best_correlation_with=None,
            high_overlap=False,
            has_check_data=r.alpha_id in checked,
        )
        for r in unmeasurable
    )
    return order


# --- putting it together ------------------------------------------------------------------


class Breakdown(Out):
    slices: list[SliceRow]
    slice_by: GroupBy
    slice_setting: SettingName | None
    min_n: int
    total: int
    failures: list[FailureCount]
    near_miss_margin: float
    near_misses: list[NearMiss]
    pool_collision_ceiling: float
    submit_order: list[SubmitCandidate]


def build(
    rows: list[AlphaRow],
    passing: list[AlphaRow],
    pool: dict[str, pd.Series],
    *,
    group_by: GroupBy,
    setting_name: SettingName | None,
    min_n: int,
    datasets_of: Mapping[str, set[str]],
    family_threshold: float,
    near_miss_margin: float,
    pool_collision_ceiling: float,
    checked: set[str] | None = None,
) -> Breakdown:
    return Breakdown(
        slices=slice_breakdown(rows, group_by, setting_name, min_n, datasets_of),
        slice_by=group_by,
        slice_setting=setting_name,
        min_n=min_n,
        total=len(rows),
        failures=failure_counts(rows),
        near_miss_margin=near_miss_margin,
        near_misses=near_misses(rows, near_miss_margin),
        pool_collision_ceiling=pool_collision_ceiling,
        submit_order=submit_order(passing, pool, family_threshold, pool_collision_ceiling, checked),
    )
