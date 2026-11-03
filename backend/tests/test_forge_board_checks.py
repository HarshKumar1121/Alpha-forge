"""Reading BRAIN's checks for the Submittable Board, from check lists shaped as BRAIN sends them.

The fixtures are BRAIN's ``is.checks`` arrays for the cases the board must tell apart: ready,
still being judged, refused, refused-but-repairable, ready-but-out-of-allowance, labels only.
"""

from typing import Any

import pytest

from alpha_harness.brain.schemas import QUICK_MODE
from alpha_harness.forge.board import checks as ck


def c(
    name: str, result: str, value: float | None = None, limit: float | None = None
) -> dict[str, Any]:
    return {"name": name, "result": result, "value": value, "limit": limit}


PASSING = [
    c("LOW_SHARPE", "PASS", 1.9, 1.25),
    c("LOW_FITNESS", "PASS", 1.3, 1.0),
    c("LOW_TURNOVER", "PASS", 0.2, 0.01),
    c("HIGH_TURNOVER", "PASS", 0.2, 0.7),
    c("CONCENTRATED_WEIGHT", "PASS"),
    c("LOW_SUB_UNIVERSE_SHARPE", "PASS", 1.1, 0.8),
    c("SELF_CORRELATION", "PASS", 0.41, 0.7),
]
LABELS = [c("MATCHES_COMPETITION", "WARNING"), c("PROD_CORRELATION", "WARNING", 0.8, 0.7)]


def test_every_gating_check_passing_is_ready_and_labels_never_decide() -> None:
    reading = ck.read(PASSING + LABELS)
    assert reading.status == "ready"
    assert {r.name: r.role for r in reading.rows}["PROD_CORRELATION"] == "label"
    assert reading.failing == []


def test_a_pending_self_correlation_keeps_it_pending() -> None:
    checks = [*PASSING[:-1], c("SELF_CORRELATION", "PENDING")]
    reading = ck.read(checks)
    assert reading.status == "pending"
    assert reading.pending == ["SELF_CORRELATION"]


def test_any_failure_refuses_and_names_the_failing_checks() -> None:
    checks = [
        *PASSING[:1],
        c("LOW_FITNESS", "FAIL", 0.7, 1.0),
        c("HIGH_TURNOVER", "FAIL", 0.8, 0.7),
    ]
    reading = ck.read(checks)
    assert reading.status == "not_ready"
    assert reading.failing == ["LOW_FITNESS", "HIGH_TURNOVER"]


def test_a_failed_quota_check_is_about_today_not_the_alpha() -> None:
    reading = ck.read([*PASSING, c("REGULAR_SUBMISSION", "FAIL", 4, 4)])
    assert reading.status == "ready"
    assert reading.quota_blocked == ["REGULAR_SUBMISSION"]
    assert {r.name: r.role for r in reading.rows}["REGULAR_SUBMISSION"] == "quota"


def test_labels_alone_or_nothing_is_unchecked_never_ready() -> None:
    assert ck.read(LABELS).status == "unchecked"
    assert ck.read([]).status == "unchecked"


def test_quick_mode_is_never_submittable() -> None:
    assert ck.read(PASSING, QUICK_MODE).status == "not_ready"


def test_power_pool_is_the_correlation_checks_presence_not_its_result() -> None:
    pending = ck.read([*PASSING, c("POWER_POOL_CORRELATION", "PENDING")])
    assert pending.power_pool
    assert pending.status == "pending"
    # The description checks share the prefix and say nothing about eligibility.
    described = ck.read([*PASSING, c("POWER_POOL_DESCRIPTION_FORMAT", "WARNING")])
    assert not described.power_pool
    assert [r.name for r in ck.description_rows(described)] == ["POWER_POOL_DESCRIPTION_FORMAT"]


@pytest.mark.parametrize(
    ("check", "key"),
    [
        ("HIGH_TURNOVER", "HIGH_TURNOVER"),
        ("LOW_FITNESS", "LOW_FITNESS_FAST"),
        ("LOW_TURNOVER", "LOW_TURNOVER"),
        ("CONCENTRATED_WEIGHT", "CONCENTRATED_WEIGHT"),
        ("LOW_SUB_UNIVERSE_SHARPE", "LOW_SUB_UNIVERSE_SHARPE"),
    ],
)
def test_repairable_checks_map_to_their_court_repair(check: str, key: str) -> None:
    repair = ck.repair_for(check)
    assert repair.key == key
    assert repair.why


def test_a_repair_names_what_it_needs() -> None:
    assert ck.repair_for("LOW_SUB_UNIVERSE_SHARPE").needs == ("adv20",)


@pytest.mark.parametrize("check", ["LOW_SHARPE", "SELF_CORRELATION", "POWER_POOL_CORRELATION"])
def test_unrepairable_checks_get_advice_not_a_repair(check: str) -> None:
    repair = ck.repair_for(check)
    assert repair.key is None
    assert repair.why
