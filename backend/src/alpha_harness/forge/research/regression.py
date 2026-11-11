"""Multi-field regression: how much of a target's movement a handful of candidate fields
explain, together and individually — the statistical half of the Regression Lab.

Pure functions over already-fetched PnL series (see ``regression_data.py`` for how those
series are obtained — that is the only part of this feature that spends anything). Mirrors
``factors.regress``'s alignment and ``MIN_OVERLAP``-drop philosophy, extended with per-field
standard errors / t-stats / p-values, a correlation matrix, Variance Inflation Factor (VIF),
and a two-half stability check. Hand-rolled numpy throughout, no scikit-learn and no scipy:
p-values come from a hand-rolled Student-t survival function (the regularized incomplete beta
function, Numerical-Recipes style), not a library call.
"""

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .factors import MIN_OVERLAP

#: The conventional cutoff: a field whose coefficient is unstable to collinearity or whose
#: regression loses most of its own information to the other fields in the mix.
VIF_THRESHOLD = 5.0

#: The default significance level for p-values, confidence intervals and the stability check,
#: when a caller doesn't choose its own (matches ``factors.py``'s "5% is the house rule").
ALPHA_DEFAULT = 0.05

#: Pairwise correlation above which two X fields are grouped as "moving together" for the
#: redundancy-grouping callout — the same convention as VIF's 5.0, just read off the raw
#: correlation matrix instead of the multi-field fit.
REDUNDANCY_CORR_THRESHOLD = 0.8

#: Below this R², the fit is weak enough that "nothing to add" isn't useful advice on its own —
#: ``plain_summary`` names a concrete next step instead.
LOW_FIT_R2 = 0.05


@dataclass(slots=True)
class FieldStat:
    """One X field's place in the regression: its own coefficient plus every diagnostic a
    researcher needs to judge whether that coefficient is real, independent signal."""

    field_id: str
    coef: float
    se: float
    t_stat: float
    p_value: float
    ci_low: float
    ci_high: float
    #: This field's own (uncontrolled) correlation with the target — compare against ``coef``
    #: to see whether controlling for the other fields changes the story.
    standalone_corr: float
    vif: float
    vif_flag: bool


@dataclass(slots=True)
class StabilityRow:
    """Does this field's effect hold up across both halves of the date range, or is it noise
    that happened to line up with the target in only one of them?"""

    field_id: str
    sign_half1: int
    sign_half2: int
    significant_half1: bool
    significant_half2: bool
    stable: bool


@dataclass(slots=True)
class WinnerField:
    """The single strongest, most-stable candidate — the one-line answer to "which field should
    I actually use", picked only from fields that are VIF-clean *and* stable in both halves."""

    field_id: str
    t_stat: float


@dataclass(slots=True)
class RegressionResult:
    fields: list[FieldStat]
    r_squared: float
    adj_r_squared: float
    residual_std_error: float
    n_obs: int
    corr_matrix: dict[str, dict[str, float]]
    stability: list[StabilityRow] = field(default_factory=list)
    #: Fields that failed ``MIN_OVERLAP`` against the target and were left out, same
    #: drop-not-fail philosophy as ``factors.regress``.
    dropped: list[str] = field(default_factory=list)


# --- hand-rolled Student-t two-sided p-value, no scipy ---------------------------------------
#
# P(|T| >= |t|) for a Student-t with `dof` degrees of freedom equals the regularized incomplete
# beta function I_x(dof/2, 1/2) at x = dof / (dof + t^2) — a standard identity that turns a
# t-test p-value into one call of `betainc`, itself built from a Lanczos `gammln` and the
# Numerical-Recipes continued-fraction expansion of the incomplete beta function. All of this is
# plain float arithmetic: no scipy.stats, no scikit-learn.

_MAXIT = 200
_EPS = 3e-16
_FPMIN = 1e-300


def _gammln(xx: float) -> float:
    """log(Gamma(xx)) via the Lanczos approximation (Numerical Recipes' 6-term series)."""
    cof = (
        76.18009172947146,
        -86.50532032941677,
        24.01409824083091,
        -1.231739572450155,
        0.1208650973866179e-2,
        -0.5395239384953e-5,
    )
    x = xx
    y = xx
    tmp = x + 5.5
    tmp -= (x + 0.5) * math.log(tmp)
    ser = 1.000000000190015
    for c in cof:
        y += 1.0
        ser += c / y
    return -tmp + math.log(2.5066282746310005 * ser / x)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued-fraction evaluation of the incomplete beta function (Lentz's method)."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function I_x(a, b), for 0 <= x <= 1."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(
        _gammln(a + b) - _gammln(a) - _gammln(b) + a * math.log(x) + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _t_sf(t: float, dof: float) -> float:
    """Two-sided Student-t p-value: P(|T| >= |t|) for `dof` degrees of freedom."""
    if dof <= 0 or math.isnan(t):
        return math.nan
    return _betainc(dof / 2.0, 0.5, dof / (dof + t * t))


def _t_ppf(tail: float, dof: float) -> float:
    """The critical |t| such that ``_t_sf(t, dof) == tail`` (e.g. ``tail=0.05`` -> the usual
    95%-CI multiplier), found by bisection since there is no closed-form inverse here."""
    if dof <= 0 or not 0.0 < tail < 1.0:
        return math.nan
    lo, hi = 0.0, 1.0
    while _t_sf(hi, dof) > tail:
        hi *= 2.0
        if hi > 1e8:
            break
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if _t_sf(mid, dof) > tail:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# --- OLS core, shared by ols_fit and vif -------------------------------------------------------


def _ols_core(y: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Fit ``y = x @ coef + residual`` (``x`` already carries its intercept column) and return
    ``(coef, se_per_coef, r_squared, sigma2)``."""
    n, k = x.shape
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    residual = y - x @ coef
    ss_res = float(np.sum(residual**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else math.nan
    dof = n - k
    sigma2 = ss_res / dof if dof > 0 else math.nan
    if math.isnan(sigma2):
        se = np.full(k, math.nan)
    else:
        xtx_inv = np.linalg.pinv(x.T @ x)
        se = np.sqrt(np.clip(sigma2 * np.diag(xtx_inv), 0.0, None))
    return coef, se, r_squared, sigma2


def adjusted_r_squared(r2: float, n: int, k: int) -> float:
    """R-squared penalized for the number of fields ``k`` used to get it from ``n`` rows.

    Its own function (rather than inlined in ``ols_fit``) so "adding a useless field lowers
    this" has one small, directly-testable target: raw R² can only rise as fields are added,
    but this falls whenever a field's gain is smaller than the penalty for spending a degree
    of freedom on it.
    """
    if math.isnan(r2) or n - k - 1 <= 0:
        return math.nan
    return 1.0 - (1.0 - r2) * (n - 1) / (n - k - 1)


def correlation_matrix(fields: dict[str, pd.Series]) -> dict[str, dict[str, float]]:
    """Pairwise Pearson correlation among the candidate X fields themselves (not the target) —
    the cheap, visual half of the multicollinearity check."""
    if not fields:
        return {}
    aligned = pd.concat(fields, axis=1, join="inner").dropna()
    if aligned.empty:
        names = list(fields)
        return {a: {b: (1.0 if a == b else math.nan) for b in names} for a in names}
    corr = aligned.corr()
    return {a: {b: float(corr.loc[a, b]) for b in corr.columns} for a in corr.index}


def vif(fields_df: pd.DataFrame) -> dict[str, float]:
    """Variance Inflation Factor per column: regress each field on every other field and read
    off ``1 / (1 - r_squared)`` of that fit. A field with VIF > 5 is substantially explained by
    the others already in the mix — it is not adding independent information."""
    names = list(fields_df.columns)
    if len(names) < 2:
        return dict.fromkeys(names, 1.0)
    aligned = fields_df.dropna()
    n = len(aligned)
    if n < 3:
        return dict.fromkeys(names, math.nan)
    out: dict[str, float] = {}
    for name in names:
        others = [c for c in names if c != name]
        y = aligned[name].to_numpy(dtype=float)
        x = np.column_stack([np.ones(n), *(aligned[c].to_numpy(dtype=float) for c in others)])
        coef, *_ = np.linalg.lstsq(x, y, rcond=None)
        residual = y - x @ coef
        ss_res = float(np.sum(residual**2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        out[name] = math.inf if r2 >= 1.0 else 1.0 / (1.0 - r2)
    return out


def _fit(target: pd.Series, fields: dict[str, pd.Series], alpha: float) -> RegressionResult:
    """``ols_fit`` without the stability pass — split out so ``split_half_stability`` (which
    fits each half) never recurses into computing stability on sub-halves of a half."""

    def _overlap(s: pd.Series) -> int:
        return len(pd.concat([target, s], axis=1, join="inner").dropna())

    usable = {name: s for name, s in fields.items() if _overlap(s) >= MIN_OVERLAP}
    dropped = [name for name in fields if name not in usable]
    empty = RegressionResult(
        fields=[],
        r_squared=math.nan,
        adj_r_squared=math.nan,
        residual_std_error=math.nan,
        n_obs=0,
        corr_matrix={},
        dropped=dropped,
    )
    if not usable:
        return empty
    aligned = pd.concat({"target": target, **usable}, axis=1, join="inner").dropna()
    if len(aligned) < MIN_OVERLAP:
        return RegressionResult(
            fields=[],
            r_squared=math.nan,
            adj_r_squared=math.nan,
            residual_std_error=math.nan,
            n_obs=0,
            corr_matrix={},
            dropped=list(fields),
        )

    names = list(usable)
    n, k = len(aligned), len(names)
    y = aligned["target"].to_numpy(dtype=float)
    x = np.column_stack([np.ones(n), *(aligned[name].to_numpy(dtype=float) for name in names)])
    coef, se, r_squared, sigma2 = _ols_core(y, x)
    dof = n - k - 1
    rse = math.sqrt(sigma2) if not math.isnan(sigma2) and sigma2 >= 0 else math.nan
    adj_r2 = adjusted_r_squared(r_squared, n, k)
    vifs = vif(aligned[names]) if k > 1 else dict.fromkeys(names, 1.0)
    tcrit = _t_ppf(alpha, dof) if dof > 0 else math.nan

    stats: list[FieldStat] = []
    for i, name in enumerate(names):
        c = float(coef[1 + i])
        s = float(se[1 + i])
        t_stat = c / s if s and not math.isnan(s) else math.nan
        p = _t_sf(t_stat, dof) if dof > 0 else math.nan
        ci_low = c - tcrit * s if not (math.isnan(s) or math.isnan(tcrit)) else math.nan
        ci_high = c + tcrit * s if not (math.isnan(s) or math.isnan(tcrit)) else math.nan
        standalone = float(aligned["target"].corr(aligned[name]))
        v = vifs.get(name, math.nan)
        stats.append(
            FieldStat(
                field_id=name,
                coef=c,
                se=s,
                t_stat=t_stat,
                p_value=p,
                ci_low=ci_low,
                ci_high=ci_high,
                standalone_corr=standalone,
                vif=v,
                vif_flag=(not math.isnan(v)) and v > VIF_THRESHOLD,
            )
        )
    return RegressionResult(
        fields=stats,
        r_squared=r_squared,
        adj_r_squared=adj_r2,
        residual_std_error=rse,
        n_obs=n,
        corr_matrix=correlation_matrix(usable),
        dropped=dropped,
    )


def split_half_stability(
    target: pd.Series, fields: dict[str, pd.Series], *, alpha: float = ALPHA_DEFAULT
) -> list[StabilityRow]:
    """Re-fit on each non-overlapping half of the aligned date range (split at the midpoint,
    date order preserved) and compare each field's sign and significance across the two.

    A field insignificant in both halves is reported ``stable=False`` too (not silently
    "stable" by virtue of never mattering) — see ``stable``'s definition: significant *and*
    sign-agreeing in both halves, nothing less.
    """
    if not fields:
        return []
    aligned = pd.concat({"target": target, **fields}, axis=1, join="inner").dropna().sort_index()
    names = list(fields)
    if len(aligned) < 4:
        return []
    mid = len(aligned) // 2
    half1, half2 = aligned.iloc[:mid], aligned.iloc[mid:]
    r1 = _fit(half1["target"], {n: half1[n] for n in names}, alpha)
    r2 = _fit(half2["target"], {n: half2[n] for n in names}, alpha)
    by1 = {f.field_id: f for f in r1.fields}
    by2 = {f.field_id: f for f in r2.fields}
    rows: list[StabilityRow] = []
    for name in names:
        s1, s2 = by1.get(name), by2.get(name)
        if s1 is None or s2 is None:
            continue
        sign1 = 1 if s1.coef >= 0 else -1
        sign2 = 1 if s2.coef >= 0 else -1
        sig1 = (not math.isnan(s1.p_value)) and s1.p_value < alpha
        sig2 = (not math.isnan(s2.p_value)) and s2.p_value < alpha
        rows.append(
            StabilityRow(
                field_id=name,
                sign_half1=sign1,
                sign_half2=sign2,
                significant_half1=sig1,
                significant_half2=sig2,
                stable=sig1 and sig2 and sign1 == sign2,
            )
        )
    return rows


def ols_fit(
    target: pd.Series, fields: dict[str, pd.Series], *, alpha: float = ALPHA_DEFAULT
) -> RegressionResult:
    """Multi-field OLS: ``target = intercept + sum(coef_i * field_i) + residual``, plus every
    diagnostic the Regression Lab needs: standard errors, t-stats, p-values and confidence
    intervals per field (hand-rolled Student-t, see above), standalone correlation, VIF, the
    X-field correlation matrix, and the two-half stability check.

    Aligns via ``pd.concat(..., join="inner").dropna()`` exactly like ``factors.regress``, and
    drops (rather than fails on) any field short of ``MIN_OVERLAP`` common rows with the
    target — a field the simulator couldn't evaluate shouldn't block the rest.
    """
    result = _fit(target, fields, alpha)
    if not result.fields:
        return result
    usable = {name: s for name, s in fields.items() if name not in result.dropped}
    result.stability = split_half_stability(target, usable, alpha=alpha)
    return result


def strongest_field(result: RegressionResult) -> WinnerField | None:
    """The field with the largest |t-stat| among those that are both VIF-clean and stable in
    both halves of the date range — the plain "winner" callout above the coefficient chart.

    ``None`` is itself an answer: when nothing clears this bar, the honest message is that no
    candidate here is a safe standalone signal yet, not a forced pick of the least-bad one.
    """
    stability_by_field = {s.field_id: s for s in result.stability}
    candidates = [
        f
        for f in result.fields
        if not f.vif_flag
        and not math.isnan(f.t_stat)
        and (srow := stability_by_field.get(f.field_id)) is not None
        and srow.stable
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda f: abs(f.t_stat))
    return WinnerField(field_id=best.field_id, t_stat=best.t_stat)


def winner_message(winner: WinnerField | None) -> str:
    """The one-line callout ``plain_summary`` and the API both render verbatim."""
    if winner is None:
        return (
            "No field here is both statistically significant and stable across both halves of "
            "the date range — none of these candidates is a safe standalone signal yet."
        )
    return (
        f"{winner.field_id} is your strongest independent signal here "
        f"(t={winner.t_stat:+.1f}, stable across both halves)."
    )


def redundancy_groups(
    corr_matrix: dict[str, dict[str, float]], *, threshold: float = REDUNDANCY_CORR_THRESHOLD
) -> list[list[str]]:
    """Cluster X fields whose pairwise correlation exceeds ``threshold`` (union-find over the
    correlation matrix) so "these move together" reads as one named group instead of a heatmap
    the user has to scan cell by cell. A field correlated with no one is left out entirely — a
    group means at least two fields moving together, by construction.
    """
    names = list(corr_matrix)
    parent = {n: n for n in names}

    def find(n: str) -> str:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            corr = corr_matrix.get(a, {}).get(b, math.nan)
            if not math.isnan(corr) and abs(corr) > threshold:
                union(a, b)

    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)
    return [sorted(members) for members in groups.values() if len(members) > 1]


# --- plain-language interpretation -------------------------------------------------------------


def _fmt_pct(x: float) -> str:
    return "n/a" if math.isnan(x) else f"{x * 100:.0f}%"


#: A field's update cadence (``field_profile.Cadence``, free/local — never the paid probe-based
#: archetype) suggests which transformation to try next, in the same voice as
#: ``profiler.py``'s own per-archetype notes. Deliberately conservative: this is a hint read off
#: metadata already in hand, not a measurement of the field's actual behaviour.
_CADENCE_TRANSFORM_HINT: dict[str, str] = {
    "daily": (
        "these update daily and may just be noisy day to day — try smoothing "
        "(ts_mean, decay) or rank(ts_delta(...)) rather than the raw level"
    ),
    "quarterly": (
        "these update only a few times a year — try ts_delta or an update-event flag rather "
        "than the raw level"
    ),
    "sparse": (
        "these have thin coverage — try a different dataset category, or backfill and rank "
        "before regressing"
    ),
}


def _low_fit_suggestion(fields: list[FieldStat], cadence_by_field: dict[str, str] | None) -> str:
    cadence_by_field = cadence_by_field or {}
    hints = {
        _CADENCE_TRANSFORM_HINT[cadence]
        for f in fields
        if (cadence := cadence_by_field.get(f.field_id)) in _CADENCE_TRANSFORM_HINT
    }
    if hints:
        return "; ".join(sorted(hints)) + "."
    return (
        "try a different dataset category, or a transformation (ts_delta, ts_rank) rather than "
        "the raw level."
    )


def plain_summary(
    result: RegressionResult,
    vif_flags: dict[str, bool],
    *,
    cadence_by_field: dict[str, str] | None = None,
) -> str:
    """One paragraph, built from fixed phrase templates (not free text generation): overall fit
    in plain terms, then which candidates add nothing once the others are in the model and why.
    When the fit itself is weak (``LOW_FIT_R2``), names a concrete next step — a transformation
    suggested by each field's free cadence profile when one applies, a different category
    otherwise — rather than just "add nothing".
    """
    if not result.fields:
        return (
            "Not enough overlapping history to run this regression — every candidate field "
            "fell short of the minimum overlap with the target."
        )
    n = len(result.fields)
    stability_by_field = {s.field_id: s for s in result.stability}
    redundant: list[str] = []
    unstable: list[str] = []
    for f in result.fields:
        flagged = vif_flags.get(f.field_id, f.vif_flag)
        srow = stability_by_field.get(f.field_id)
        if flagged:
            redundant.append(f.field_id)
        if srow is not None and not srow.stable:
            unstable.append(f.field_id)

    lead = (
        f"These {n} field{'s' if n != 1 else ''} together explain "
        f"{_fmt_pct(result.r_squared)} of the target's movement "
        f"({_fmt_pct(result.adj_r_squared)} once adjusted for the number of fields)."
    )
    flagged_ids = set(redundant) | set(unstable)
    low_fit = math.isnan(result.r_squared) or result.r_squared < LOW_FIT_R2
    next_step = _low_fit_suggestion(result.fields, cadence_by_field) if low_fit else None

    if not flagged_ids:
        tail = " All candidates carry independent, stable signal."
        if next_step:
            tail += f" Even so, the overall fit is weak — {next_step}"
        return lead + tail

    clauses: list[str] = []
    for fid in redundant:
        partners = sorted(
            (
                (other, abs(result.corr_matrix.get(fid, {}).get(other, 0.0)))
                for other in result.corr_matrix.get(fid, {})
                if other != fid
            ),
            key=lambda kv: -kv[1],
        )
        against = f" with {partners[0][0]}" if partners and partners[0][1] >= 0.5 else ""
        stat = next((s for s in result.fields if s.field_id == fid), None)
        v = f"{stat.vif:.1f}" if stat and not math.isnan(stat.vif) else "n/a"
        clauses.append(f"{fid} is redundant{against} (VIF {v})")
    for fid in unstable:
        if fid in redundant:
            continue
        clauses.append(f"{fid} never clears significance in both halves of the date range")

    body = (
        f"{lead} {len(flagged_ids)} of your {n} candidate{'s' if n != 1 else ''} add nothing "
        f"once the other fields are accounted for: " + "; ".join(clauses) + "."
    )
    if next_step:
        body += f" The overall fit is weak too — {next_step}"
    return body


def recommendation(
    result: RegressionResult, vif_flags: dict[str, bool], *, alpha: float = ALPHA_DEFAULT
) -> tuple[list[str], list[str], str]:
    """``(keep_field_ids, drop_field_ids, one_line_reason)``: drop a field for a redundant VIF,
    a failed stability check, or a p-value that never clears significance; keep the rest."""
    stability_by_field = {s.field_id: s for s in result.stability}
    keep: list[str] = []
    drop: list[str] = []
    why: dict[str, str] = {}
    for f in result.fields:
        reasons: list[str] = []
        if vif_flags.get(f.field_id, f.vif_flag):
            reasons.append(f"VIF {f.vif:.1f}, redundant" if not math.isnan(f.vif) else "redundant")
        srow = stability_by_field.get(f.field_id)
        if srow is not None and not srow.stable:
            reasons.append("fails the stability check")
        if math.isnan(f.p_value) or f.p_value >= alpha:
            p_text = "p=n/a" if math.isnan(f.p_value) else f"p={f.p_value:.2f}"
            reasons.append(f"{p_text}, never significant")
        if reasons:
            drop.append(f.field_id)
            why[f.field_id] = ", ".join(reasons)
        else:
            keep.append(f.field_id)

    if not drop:
        reason = (
            f"Keep all {len(keep)} fields — every one carries independent, stable signal."
            if keep
            else "No field survived: nothing here is a usable candidate."
        )
        return keep, drop, reason

    keep_part = f"Keep {', '.join(keep)}" if keep else "Keep none of these fields"
    drop_part = "; ".join(f"{fid} ({why[fid]})" for fid in drop)
    reason = f"{keep_part} — carries independent, stable signal. Drop {drop_part}."
    return keep, drop, reason
