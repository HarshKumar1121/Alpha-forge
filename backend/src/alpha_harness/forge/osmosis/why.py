"""The "Why this allocation" panel: what was chosen, and what evidence it rests on.

The panel states the evidence behind each choice in plain words, so a reader can judge how much
to trust the points. It is built from one scope's analysis only. Nothing here reads BRAIN.

The evidence is walk-forward validation on in-sample history: the stored PnL runs to the end of
each Alpha's simulation window, not past it. BRAIN's out-of-sample window (from 2024-01-01) is
not stored, so no out-of-sample check is possible yet. The panel says so.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scope import ScopeAnalysis
    from .settings import Settings

#: The start of BRAIN's out-of-sample window, as its ``os`` block reports it.
OS_WINDOW_START = "2024-01-01"


@dataclass(frozen=True)
class WhyPanel:
    """Each line is a sentence the panel shows, in order. Numbers are shown beside them."""

    #: The method the points use, or ``None`` when the scope is blocked.
    method: str | None
    #: True only when the default method beat equal weight on the holdout folds.
    validated: bool
    #: How the method was chosen and checked, in one line.
    method_line: str
    #: How the size was chosen, in one line.
    size_line: str
    #: The data the evidence rests on: days, folds, and that no out-of-sample data exists.
    evidence_line: str
    #: The decay check's status. It needs out-of-sample days, so it cannot run yet.
    decay_line: str
    #: Alphas left out of the ranking for correlation, shown as a count.
    correlation_exclusions: int


def build_why(analysis: ScopeAnalysis, settings: Settings) -> WhyPanel:
    """The panel's sentences for one analysed scope."""
    correlated = sum(1 for e in analysis.excluded if e.reason == "correlation")
    if analysis.blocked is not None:
        return WhyPanel(
            method=None,
            validated=False,
            method_line="No method is applied: this scope is blocked.",
            size_line=analysis.blocked,
            evidence_line="No evidence is shown for a blocked scope.",
            decay_line="Decay check not run: the scope is blocked.",
            correlation_exclusions=correlated,
        )

    if analysis.validated:
        method_line = (
            f"{analysis.method} was chosen on the earlier walk-forward folds and beat equal "
            "weight on the holdout folds."
        )
    elif analysis.fallback:
        method_line = (
            f"{analysis.requested_method} found no solution on this window, so equal weight is "
            "used. Not validated."
        )
    else:
        verdict = analysis.banner or "no walk-forward verdict"
        method_line = f"{analysis.method} is the default, not validated: {verdict}"

    # The floor cannot exceed the ranking: with fewer ranked alphas than the minimum, the floor
    # is every ranked alpha, and the scope carries a below-minimum warning.
    floor = min(settings.min_eligible, len(analysis.bets))
    size_line = (
        f"{analysis.n} alphas. The recommended {analysis.recommended_n} is the smallest size, "
        f"never below {floor}, after which one more alpha raises the effective number of "
        f"independent bets by less than {settings.bets_min_gain:.0%}."
    )

    evidence_line = (
        f"Walk-forward on in-sample history only: {analysis.folds} scored folds over "
        f"{analysis.window_days} days of training data"
        + (f", {analysis.skipped_folds} skipped" if analysis.skipped_folds else "")
        + f". No out-of-sample data is available: BRAIN's out-of-sample window (from "
        f"{OS_WINDOW_START}) is not stored yet."
    )

    decay_line = (
        "Decay check not run: it compares out-of-sample Sharpe with in-sample Sharpe, and no "
        "out-of-sample days are stored."
    )

    return WhyPanel(
        method=analysis.method,
        validated=analysis.validated,
        method_line=method_line,
        size_line=size_line,
        evidence_line=evidence_line,
        decay_line=decay_line,
        correlation_exclusions=correlated,
    )
