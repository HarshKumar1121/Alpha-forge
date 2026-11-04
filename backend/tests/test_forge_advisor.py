"""The Run Advisor's pure synthesis: :mod:`alpha_harness.forge.advisor.advisor`.

Offline and deterministic (CLAUDE.md rule 4) — every input is a plain dataclass built by hand,
the same shapes the route (``test_forge_advisor_route.py``) assembles from the Board, the
catalog and ``forge_template_stats``.
"""

from alpha_harness.forge.advisor import advisor as adv
from alpha_harness.forge.board import checks as ck
from alpha_harness.forge.nuggets.suggest import PairingSuggestion
from alpha_harness.forge.research.field_profile import FieldProfile

FIELD_A = FieldProfile(
    field_id="anl4_eps_mean",
    dataset_id="analyst4",
    dataset_name="Analyst4",
    description="EPS estimate mean",
    field_type="MATRIX",
    coverage=0.72,
    date_coverage=0.9,
    category_name="analyst",
    subcategory_name="eps",
)
FIELD_B = FieldProfile(
    field_id="anl4_eps_low",
    dataset_id="analyst4",
    dataset_name="Analyst4",
    description="EPS estimate low",
    field_type="MATRIX",
    coverage=0.65,
    date_coverage=0.9,
    category_name="analyst",
    subcategory_name="eps",
)
SPARSE_FIELD = FieldProfile(
    field_id="rare_event",
    dataset_id="events1",
    dataset_name="Events",
    description="A rare corporate event flag",
    field_type="MATRIX",
    coverage=0.4,
    date_coverage=0.05,
    category_name="event",
    subcategory_name=None,
)


def ready() -> ck.Reading:
    return ck.read([{"name": "LOW_SHARPE", "result": "PASS"}])


def refused(check: str = "LOW_FITNESS") -> ck.Reading:
    return ck.read([{"name": check, "result": "FAIL", "value": 0.8, "limit": 1.0}])


# --- preflight --------------------------------------------------------------------------


def test_preflight_describes_each_fields_cadence_and_coverage() -> None:
    summary = adv.preflight([FIELD_A, FIELD_B], [], [])
    assert "anl4_eps_mean is daily" in summary.paragraph
    assert "72% coverage" in summary.paragraph
    assert summary.warnings == []


def test_preflight_flags_a_sparse_field() -> None:
    summary = adv.preflight([SPARSE_FIELD], [], [])
    assert "sparse" in summary.paragraph
    assert "long stretches of nothing to trade on" in summary.paragraph


def test_preflight_surfaces_an_invalid_pairing_as_a_warning() -> None:
    pairing = PairingSuggestion(
        field_a="anl4_eps_mean",
        field_b="anl4_eps_low",
        valid=False,
        reason="anl4_eps_mean and anl4_eps_low look like the same measure twice.",
    )
    summary = adv.preflight([FIELD_A, FIELD_B], [pairing], [])
    assert summary.warnings == [pairing.reason]
    assert "Pairing check:" in summary.paragraph
    assert pairing.reason in summary.paragraph


def test_preflight_says_so_when_a_pairing_is_structurally_sound() -> None:
    pairing = PairingSuggestion(
        field_a="anl4_eps_mean", field_b="anl4_eps_low", valid=True, reason="Same dataset."
    )
    summary = adv.preflight([FIELD_A, FIELD_B], [pairing], [])
    assert summary.warnings == []
    assert "structurally sound together" in summary.paragraph


def test_preflight_reports_no_track_record_when_template_stats_is_empty() -> None:
    summary = adv.preflight([FIELD_A], [], [])
    assert "no logged attempts yet" in summary.paragraph


def test_preflight_reports_the_busiest_scope_cuts_track_record() -> None:
    similar = [
        adv.SimilarAttempt(
            "analyst", "regular", sims=5, checked=5, passed=2, median_train_sharpe=0.9
        ),
        adv.SimilarAttempt(
            "analyst", "regular", sims=40, checked=38, passed=30, median_train_sharpe=1.4
        ),
    ]
    summary = adv.preflight([FIELD_A], [], similar)
    assert "run 40 times before" in summary.paragraph
    assert "30/38 passed" in summary.paragraph
    assert "median train Sharpe of 1.40" in summary.paragraph


# --- postrun ----------------------------------------------------------------------------


def test_postrun_verdict_combines_train_test_and_status() -> None:
    summary = adv.postrun(
        reading=ready(),
        train_sharpe=1.8,
        test_sharpe=1.7,
        tear_verdict=None,
        pool_correlation=[],
        permutation_p_value=None,
        has_pnl=True,
    )
    assert "train Sharpe is 1.80" in summary.verdict
    assert "holds up at 1.70" in summary.verdict
    assert "submittable" in summary.verdict


def test_postrun_flags_overfitting_when_test_sharpe_fades() -> None:
    summary = adv.postrun(
        reading=ready(),
        train_sharpe=2.0,
        test_sharpe=1.0,
        tear_verdict=None,
        pool_correlation=[],
        permutation_p_value=None,
        has_pnl=True,
    )
    assert "sign of overfitting" in summary.verdict


def test_postrun_action_is_the_repair_for_the_first_failing_check() -> None:
    summary = adv.postrun(
        reading=refused("LOW_FITNESS"),
        train_sharpe=0.9,
        test_sharpe=None,
        tear_verdict=None,
        pool_correlation=[],
        permutation_p_value=None,
        has_pnl=True,
    )
    assert summary.action_kind == "repair"
    assert "LOW_FITNESS_FAST" in summary.action


def test_postrun_action_is_wait_when_a_check_is_still_pending() -> None:
    reading = ck.read([{"name": "LOW_SHARPE", "result": "PENDING"}])
    summary = adv.postrun(
        reading=reading,
        train_sharpe=1.2,
        test_sharpe=None,
        tear_verdict=None,
        pool_correlation=[],
        permutation_p_value=None,
        has_pnl=True,
    )
    assert summary.action_kind == "wait"


def test_postrun_action_is_fetch_when_nothing_failed_but_pnl_is_missing() -> None:
    summary = adv.postrun(
        reading=ready(),
        train_sharpe=1.2,
        test_sharpe=None,
        tear_verdict=None,
        pool_correlation=[],
        permutation_p_value=None,
        has_pnl=False,
    )
    assert summary.action_kind == "fetch"


def test_postrun_action_is_a_field_swap_when_crowded_against_the_pool() -> None:
    summary = adv.postrun(
        reading=ready(),
        train_sharpe=1.5,
        test_sharpe=1.4,
        tear_verdict=None,
        pool_correlation=[("OTHER1", 0.85)],
        permutation_p_value=0.01,
        has_pnl=True,
    )
    assert summary.action_kind == "field_swap"
    assert "OTHER1" in summary.action


def test_postrun_action_is_close_when_the_permutation_test_cant_tell_it_from_noise() -> None:
    summary = adv.postrun(
        reading=ready(),
        train_sharpe=1.5,
        test_sharpe=1.4,
        tear_verdict=None,
        pool_correlation=[],
        permutation_p_value=0.4,
        has_pnl=True,
    )
    assert summary.action_kind == "close"
    assert "p=0.40" in summary.action


def test_postrun_action_is_ready_when_status_ready_and_nothing_flags() -> None:
    summary = adv.postrun(
        reading=ready(),
        train_sharpe=1.5,
        test_sharpe=1.4,
        tear_verdict=None,
        pool_correlation=[],
        permutation_p_value=0.05,
        has_pnl=True,
    )
    assert summary.action_kind == "ready"
