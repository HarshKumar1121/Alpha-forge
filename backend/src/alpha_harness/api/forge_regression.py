"""``/api/forge/regression``: the Multi-Field Regression Lab — a field-screening step that runs
*before* template binding, not after.

Two endpoints, two very different costs:

- ``/prescreen`` is free: ``field_profile.duplicate_reason()`` run pairwise over catalog
  metadata already synced, the same cheap check the Nugget layer uses, before anyone pays for
  a simulation.
- ``/run`` is not free: each candidate field's behaviour only comes back as a simulated alpha's
  PnL (``forge/simulator.py``'s module docstring, point 3), so fetching those series spends the
  ``explore`` lane through the Quota Governor, exactly like ``forge_probe`` does (CLAUDE.md rule
  3). The regression math itself (``forge/research/regression.py``) is a pure function over
  whatever series got admitted and spends nothing more.
"""

from collections import Counter
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select

from ..catalog.queries import Tuple4
from ..forge import alpha_types
from ..forge.nuggets.models import Nugget
from ..forge.pipelines.brain_adapter import BrainSimulator
from ..forge.research import regression as reg
from ..forge.research import regression_data as rdata
from ..forge.research.field_profile import FieldProfile, duplicate_reason, profile_field
from ..schemas import Out
from .catalog import Scope
from .deps import State, refuse

router = APIRouter(prefix="/api/forge/regression", tags=["forge-regression"])

#: Same lane ``forge_probe`` uses: cheap, pre-screening exploration, never the finalist lanes.
LANE = "explore"

#: Up to 8 candidate X fields: enough for a real screening pass, short enough to read a
#: correlation heatmap or a coefficient bar chart of at a glance.
MIN_FIELDS = 2
MAX_FIELDS = 8


def _dedup(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(f.strip() for f in ids if f.strip()))


# --- wire shapes --------------------------------------------------------------------------------


class RegressionScope(Out):
    instrument_type: str = "EQUITY"
    region: str = Field(min_length=1, max_length=16)
    delay: int = Field(ge=0, le=1)
    universe: str = Field(min_length=1, max_length=32)

    def to_tuple4(self) -> Tuple4:
        return Tuple4(
            instrument_type=self.instrument_type,
            region=self.region,
            delay=self.delay,
            universe=self.universe,
        )

    def to_settings(self) -> dict[str, Any]:
        return {
            "instrumentType": self.instrument_type,
            "region": self.region,
            "delay": self.delay,
            "universe": self.universe,
        }


class DateRange(Out):
    start: str
    end: str


class TargetSpec(Out):
    kind: Literal["vault", "gym", "field"]
    ref: str = Field(min_length=1, max_length=4000)


class PrescreenRequest(Out):
    field_ids: list[str] = Field(min_length=MIN_FIELDS, max_length=MAX_FIELDS)


class DuplicatePairOut(Out):
    field_a: str
    field_b: str
    reason: str


class PrescreenOut(Out):
    duplicate_pairs: list[DuplicatePairOut]


class RegressionRunRequest(Out):
    scope: RegressionScope
    field_ids: list[str] = Field(min_length=MIN_FIELDS, max_length=MAX_FIELDS)
    target: TargetSpec
    date_range: DateRange | None = None


class FieldProfileOut(Out):
    coverage: float | None
    date_coverage: float | None
    value_score: float | None
    user_count: int | None
    alpha_count: int | None
    crowding: float | None
    category: str | None
    field_type: str | None


class StabilityOut(Out):
    sign_half1: int
    sign_half2: int
    significant_half1: bool
    significant_half2: bool
    stable: bool


class FieldStatOut(Out):
    field_id: str
    coef: float
    se: float
    t_stat: float
    p_value: float
    ci_low: float
    ci_high: float
    standalone_corr: float
    vif: float
    vif_flag: bool
    stability: StabilityOut | None
    profile: FieldProfileOut | None


class RecommendationOut(Out):
    keep: list[str]
    drop: list[str]
    reason: str


class WinnerOut(Out):
    field_id: str | None
    t_stat: float | None
    message: str


class SuggestedSetOut(Out):
    label: str
    category: str
    field_ids: list[str]
    source: str


class RegressionRunOut(Out):
    lane: str
    admitted: int
    asked: int
    dropped: list[str]
    n_obs: int
    r_squared: float
    adj_r_squared: float
    residual_std_error: float
    fields: list[FieldStatOut]
    corr_matrix: dict[str, dict[str, float]]
    duplicate_pairs: list[DuplicatePairOut]
    plain_summary: str
    recommendation: RecommendationOut
    winner: WinnerOut
    #: Fields whose pairwise correlation clears ``reg.REDUNDANCY_CORR_THRESHOLD`` — each inner
    #: list is one cluster of at least two fields "moving together"; singletons are left out.
    redundancy_groups: list[list[str]]


# --- prescreen: free, no simulation -------------------------------------------------------------


def _duplicate_pairs(profiles: dict[str, FieldProfile]) -> list[DuplicatePairOut]:
    names = list(profiles)
    pairs: list[DuplicatePairOut] = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            reason = duplicate_reason(profiles[a], profiles[b])
            if reason:
                pairs.append(DuplicatePairOut(field_a=a, field_b=b, reason=reason))
    return pairs


@router.post("/prescreen")
async def prescreen(scope: Scope, body: PrescreenRequest, state: State) -> PrescreenOut:
    """Which candidate pairs already look like the same measure twice, before anyone pays for
    a simulation. Zero lane spend."""
    ids = _dedup(body.field_ids)
    rows = await state.queries.fields_by_id(scope, ids)
    profiles = {str(r["field_id"]): profile_field(r) for r in rows}
    return PrescreenOut(duplicate_pairs=_duplicate_pairs(profiles))


# --- run: spends the explore lane for field series ----------------------------------------------


def _clip_to_range(series: pd.Series, date_range: DateRange | None) -> pd.Series:
    if date_range is None or series.empty:
        return series
    try:
        return series.loc[date_range.start : date_range.end]
    except KeyError, TypeError:
        return series


@router.post("/run")
async def run_regression(body: RegressionRunRequest, state: State) -> RegressionRunOut:
    """Fetch each candidate field's series (and the target's) and run the OLS/VIF/stability
    screen over however many fields the ``explore`` lane admitted.

    A partially-admitted request still returns a regression over however many fields were
    admitted; the rest are listed in ``dropped``, the same "admitted vs asked" shape
    ``forge_probe`` already uses.
    """
    governor = state.engine.governor
    if governor is None:
        raise refuse(503, "quota_governor_off", "The Quota Governor is not running.")

    ids = _dedup(body.field_ids)
    if len(ids) < MIN_FIELDS:
        raise refuse(422, "too_few_fields", f"Need at least {MIN_FIELDS} distinct field ids.")

    rows = await state.queries.fields_by_id(body.scope.to_tuple4(), ids)
    profiles = {str(r["field_id"]): profile_field(r) for r in rows}
    duplicate_pairs = _duplicate_pairs(profiles)

    # One simulation admitted per candidate field; the target, when it's itself a raw field or
    # a vault/Gym lookup, is handled separately below (a vault hit and a Gym expression cost
    # nothing extra from this lane; a field target is simulated through the same admission).
    admission = await governor.admit(LANE, [1] * len(ids))
    admitted_ids = ids[: admission.admitted]
    not_admitted = ids[admission.admitted :]
    if not admitted_ids:
        raise refuse(429, "no_quota", f"The {LANE} lane has no room today: {admission.reason}")

    settings = body.scope.to_settings()
    alpha_type = alpha_types.load("regular")
    simulator = BrainSimulator(state.engine, state.db, state.endpoints, LANE)

    field_series = await rdata.field_series(simulator, alpha_type, settings, admitted_ids)
    target = await rdata.target_series(
        simulator, alpha_type, settings, body.target.kind, body.target.ref
    )
    target = _clip_to_range(target, body.date_range)
    field_series = {fid: _clip_to_range(s, body.date_range) for fid, s in field_series.items()}

    result = reg.ols_fit(target, field_series)
    vif_flags = {f.field_id: f.vif_flag for f in result.fields}
    # Free, already-fetched metadata (never the paid probe-based archetype) — just enough of a
    # hint to name a transformation when the fit itself is weak (CLAUDE.md rule 3: don't spend
    # more simulations just to word a summary better).
    cadence_by_field = {fid: str(p.cadence) for fid, p in profiles.items()}
    summary = reg.plain_summary(result, vif_flags, cadence_by_field=cadence_by_field)
    keep, drop, reason = reg.recommendation(result, vif_flags)
    winner = reg.strongest_field(result)
    winner_out = WinnerOut(
        field_id=winner.field_id if winner else None,
        t_stat=winner.t_stat if winner else None,
        message=reg.winner_message(winner),
    )
    groups = reg.redundancy_groups(result.corr_matrix)

    stability_by_field = {s.field_id: s for s in result.stability}
    dropped = sorted(set(result.dropped) | set(not_admitted))

    def _profile_out(field_id: str) -> FieldProfileOut | None:
        p = profiles.get(field_id)
        if p is None:
            return None
        row = next((r for r in rows if str(r["field_id"]) == field_id), {})
        user_count = row.get("user_count")
        alpha_count = row.get("alpha_count")
        crowding = None
        if isinstance(user_count, int | float) or isinstance(alpha_count, int | float):
            # A lightweight convenience score, not a platform rule: how usable this field looks
            # (good coverage, not already heavily mined). Crowding here is relative to just the
            # candidates in this run, not the whole market — see forge/data/crowding.py for the
            # full market-wide score used by the Datasets tab and the Nugget field search.
            crowding = min(1.0, (alpha_count or 0) / 1000.0)
        coverage = p.coverage or 0.0
        date_coverage = p.date_coverage or 0.0
        value_score = round(coverage * date_coverage - (crowding or 0.0) * 0.2, 4)
        return FieldProfileOut(
            coverage=p.coverage,
            date_coverage=p.date_coverage,
            value_score=value_score,
            user_count=user_count,
            alpha_count=alpha_count,
            crowding=crowding,
            category=p.category,
            field_type=p.field_type,
        )

    field_stats_out = [
        FieldStatOut(
            field_id=f.field_id,
            coef=f.coef,
            se=f.se,
            t_stat=f.t_stat,
            p_value=f.p_value,
            ci_low=f.ci_low,
            ci_high=f.ci_high,
            standalone_corr=f.standalone_corr,
            vif=f.vif,
            vif_flag=f.vif_flag,
            stability=(
                StabilityOut(
                    sign_half1=stability_by_field[f.field_id].sign_half1,
                    sign_half2=stability_by_field[f.field_id].sign_half2,
                    significant_half1=stability_by_field[f.field_id].significant_half1,
                    significant_half2=stability_by_field[f.field_id].significant_half2,
                    stable=stability_by_field[f.field_id].stable,
                )
                if f.field_id in stability_by_field
                else None
            ),
            profile=_profile_out(f.field_id),
        )
        for f in result.fields
    ]

    return RegressionRunOut(
        lane=LANE,
        admitted=admission.admitted,
        asked=admission.asked,
        dropped=dropped,
        n_obs=result.n_obs,
        r_squared=result.r_squared,
        adj_r_squared=result.adj_r_squared,
        residual_std_error=result.residual_std_error,
        fields=field_stats_out,
        corr_matrix=result.corr_matrix,
        duplicate_pairs=duplicate_pairs,
        plain_summary=summary,
        recommendation=RecommendationOut(keep=keep, drop=drop, reason=reason),
        winner=winner_out,
        redundancy_groups=groups,
    )


# --- suggestions: free, no simulation -----------------------------------------------------------


@router.get("/suggestions")
async def suggestions(scope: Scope, state: State) -> list[SuggestedSetOut]:
    """Known combinations to try first: your own saved Data Nuggets in this scope with 2-8
    fields, one click away from loading straight into the field tray, grouped by whichever
    dataset category most of their fields belong to. Zero lane spend — catalog metadata and the
    Nugget table only, nothing simulated."""
    async with state.db.session() as session:
        rows = (
            await session.scalars(
                select(Nugget).where(
                    Nugget.instrument_type == scope.instrument_type,
                    Nugget.region == scope.region,
                    Nugget.delay == scope.delay,
                    Nugget.universe == scope.universe,
                )
            )
        ).all()

    out: list[SuggestedSetOut] = []
    for row in rows:
        ids = _dedup([f for f in row.field_ids if isinstance(f, str)])
        if not (MIN_FIELDS <= len(ids) <= MAX_FIELDS):
            continue
        field_rows = await state.queries.fields_by_id(scope, ids)
        categories = [profile_field(r).category for r in field_rows]
        category = Counter(categories).most_common(1)[0][0] if categories else "other"
        out.append(
            SuggestedSetOut(
                label=row.name or row.nugget_id, category=category, field_ids=ids, source="nugget"
            )
        )
    return out[:6]
