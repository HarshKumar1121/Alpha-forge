"""The Run Advisor's own synthesis: turn what other Forge modules already computed into one
plain-language paragraph before a run, and one verdict plus a single concrete next step after
one lands.

Nothing here simulates or calls BRAIN (CLAUDE.md rules 3-4) and nothing here re-derives numbers
another module already owns — it only reads :class:`~alpha_harness.forge.research.field_profile.
FieldProfile`, :class:`~alpha_harness.forge.nuggets.suggest.PairingSuggestion`,
``forge_template_stats`` rows, the Board's :mod:`~alpha_harness.forge.board.checks` reading, and
the tear sheet's own numbers (:mod:`~alpha_harness.forge.research.tearsheet`,
:mod:`~alpha_harness.forge.research.factors`) and writes a sentence over them.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from ..board import checks as ck

if TYPE_CHECKING:
    from ..nuggets.suggest import PairingSuggestion
    from ..research.field_profile import FieldProfile

ActionKind = Literal["repair", "field_swap", "close", "wait", "fetch", "ready"]


@dataclass(frozen=True, slots=True)
class SimilarAttempt:
    """One ``forge_template_stats`` cut: this template on this kind of data, before."""

    dataset_category: str
    alpha_type: str
    sims: int
    checked: int
    passed: int
    median_train_sharpe: float | None


@dataclass(frozen=True, slots=True)
class PreflightSummary:
    paragraph: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PostRunSummary:
    verdict: str
    action: str
    action_kind: ActionKind


# --- before a run ---------------------------------------------------------------------


def _field_clause(f: FieldProfile) -> str:
    bits: list[str] = [f.cadence]
    if f.coverage is not None:
        bits.append(f"{f.coverage * 100:.0f}% coverage")
    note = (
        " (sparse — expect long stretches of nothing to trade on)" if f.cadence == "sparse" else ""
    )
    return f"{f.field_id} is {' with '.join(bits)}{note}"


def _pairing_clause(pairings: list[PairingSuggestion]) -> str | None:
    bad = [p for p in pairings if not p.valid]
    if not pairings:
        return None
    if not bad:
        return "every pair of fields here looks structurally sound together"
    worst = bad[0]
    more = f", and {len(bad) - 1} more pair{'s' if len(bad) > 2 else ''}" if len(bad) > 2 else ""
    return f"{worst.field_a} and {worst.field_b}: {worst.reason}{more}"


def _history_clause(similar: list[SimilarAttempt]) -> str:
    if not similar:
        return (
            "this template has no logged attempts yet on this kind of data — its first result "
            "here will be the first entry"
        )
    best = max(similar, key=lambda s: s.sims)
    sharpe = (
        f"a median train Sharpe of {best.median_train_sharpe:.2f}"
        if best.median_train_sharpe is not None
        else "no Sharpe logged yet"
    )
    pass_rate = (
        f"{best.passed}/{best.checked} passed BRAIN's checks"
        if best.checked
        else "none checked yet"
    )
    return (
        f"on {best.alpha_type or 'this'} alpha type / {best.dataset_category or 'this'} data, "
        f"it has been run {best.sims} time{'s' if best.sims != 1 else ''} before, {pass_rate}, "
        f"with {sharpe}"
    )


def preflight(
    fields: list[FieldProfile],
    pairings: list[PairingSuggestion],
    similar: list[SimilarAttempt],
) -> PreflightSummary:
    """One paragraph: what these fields look like, any pairing concern, and how this template has
    scored on data like this before."""
    warnings = [p.reason for p in pairings if not p.valid]
    field_sentence = (
        "; ".join(_field_clause(f) for f in fields) + "." if fields else "No fields picked yet."
    )
    pairing_clause = _pairing_clause(pairings)
    sentences = [field_sentence]
    if pairing_clause:
        sentences.append(f"Pairing check: {pairing_clause}.")
    sentences.append(f"Track record: {_history_clause(similar)}.")
    return PreflightSummary(paragraph=" ".join(sentences), warnings=warnings)


# --- after a run -----------------------------------------------------------------------

_STATUS_PHRASE: dict[ck.Status, str] = {
    "ready": "BRAIN's checks say it is submittable",
    "pending": "BRAIN is still judging some of its checks",
    "not_ready": "BRAIN's checks refuse it as it stands",
    "unchecked": "its checks have not been fetched from BRAIN yet",
}

#: Crowding and significance thresholds for the advisor's own judgement calls — not a BRAIN
#: platform rule (CLAUDE.md rule 7 is about simulation/eligibility rules, not this UI synthesis),
#: so they live here rather than in court.yaml.
_CROWDED_CORRELATION = 0.7
_DOMINANT_FACTOR_SHARE = 0.5
_NOISE_P_VALUE = 0.2
_WEAK_TRAIN_SHARPE = 1.0


def _sharpe_clause(train_sharpe: float | None, test_sharpe: float | None) -> str | None:
    if train_sharpe is None:
        return None
    clause = f"train Sharpe is {train_sharpe:.2f}"
    if test_sharpe is not None:
        if train_sharpe - test_sharpe > 0.3:
            clause += (
                f", but it fades to {test_sharpe:.2f} on the held-out test years "
                "(a sign of overfitting)"
            )
        else:
            clause += f" and holds up at {test_sharpe:.2f} on the held-out test years"
    return clause


def verdict_line(
    *,
    status: ck.Status,
    train_sharpe: float | None,
    test_sharpe: float | None,
    tear_verdict: str | None,
) -> str:
    """One sentence tying train/test Sharpe, the Board's check status and the tear sheet's own
    factor/crowding verdict together."""
    parts = [p for p in (_sharpe_clause(train_sharpe, test_sharpe), _STATUS_PHRASE[status]) if p]
    if tear_verdict:
        parts.append(tear_verdict.rstrip("."))
    return "; ".join(parts) + "." if parts else "Nothing is known about this alpha yet."


def next_action(
    *,
    reading: ck.Reading,
    train_sharpe: float | None,
    pool_correlation: list[tuple[str, float]],
    permutation_p_value: float | None,
    has_pnl: bool,
) -> tuple[str, ActionKind]:
    """The one concrete next step, highest-priority reason first: a repair BRAIN's own checks
    point to beats a crowding guess, which beats a call to close the hypothesis."""
    if reading.failing:
        check = reading.failing[0]
        fix = ck.repair_for(check)
        if fix.key:
            return f"Queue the {fix.key} repair for {check}: {fix.why}", "repair"
        return f"{check} failed and has no automatic repair: {fix.why}", "repair"
    if reading.pending:
        return (
            f"BRAIN is still judging {', '.join(reading.pending)}; fetch the Board again later "
            "rather than changing anything yet.",
            "wait",
        )
    if not has_pnl:
        return (
            "Fetch this alpha's PnL and checks from the Board before deciding anything further.",
            "fetch",
        )
    crowded = next((c for c in pool_correlation if c[1] >= _CROWDED_CORRELATION), None)
    if crowded is not None:
        alpha_id, corr = crowded
        return (
            f"This correlates {corr:.2f} with {alpha_id} already in your pool — swap in a field "
            "from a different dataset or category rather than a variant of the same idea.",
            "field_swap",
        )
    if permutation_p_value is not None and permutation_p_value > _NOISE_P_VALUE:
        return (
            f"The sign-flip test can't tell this from noise (p={permutation_p_value:.2f}) and "
            "nothing else here is failing a check — close this hypothesis rather than spend more "
            "budget chasing it.",
            "close",
        )
    if train_sharpe is not None and train_sharpe < _WEAK_TRAIN_SHARPE and reading.status != "ready":
        return (
            f"Train Sharpe is only {train_sharpe:.2f} with nothing else flagged — close this "
            "hypothesis; a repair cannot add a signal that is not there.",
            "close",
        )
    if reading.status == "ready":
        return (
            "Nothing is flagging here — follow the Board checklist to submit it by hand.",
            "ready",
        )
    return (
        "Nothing specific is flagging here yet; fetch more BRAIN data before the next decision.",
        "fetch",
    )


def postrun(
    *,
    reading: ck.Reading,
    train_sharpe: float | None,
    test_sharpe: float | None,
    tear_verdict: str | None,
    pool_correlation: list[tuple[str, float]],
    permutation_p_value: float | None,
    has_pnl: bool,
) -> PostRunSummary:
    verdict = verdict_line(
        status=reading.status,
        train_sharpe=train_sharpe,
        test_sharpe=test_sharpe,
        tear_verdict=tear_verdict,
    )
    action, kind = next_action(
        reading=reading,
        train_sharpe=train_sharpe,
        pool_correlation=pool_correlation,
        permutation_p_value=permutation_p_value,
        has_pnl=has_pnl,
    )
    return PostRunSummary(verdict=verdict, action=action, action_kind=kind)
