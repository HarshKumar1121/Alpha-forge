"""The Submittable Board on synthetic PnL: families, comparison sets, grouping, ranking.

Pure: :func:`board.build` takes entries with whatever was already fetched, so no BRAIN, no
database and no simulation is involved (CLAUDE.md rule 4).
"""

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from alpha_harness.forge.board import board as bd
from alpha_harness.forge.research.planner import apply_repair

TODAY, YESTERDAY = date(2026, 11, 1), date(2026, 10, 31)
DAYS = pd.bdate_range("2022-01-03", periods=600)
PASS = [{"name": "LOW_SHARPE", "result": "PASS"}, {"name": "SELF_CORRELATION", "result": "PASS"}]
FAIL_FITNESS = [
    {"name": "LOW_SHARPE", "result": "PASS"},
    {"name": "LOW_FITNESS", "result": "FAIL", "value": 0.8, "limit": 1.0},
]


def pnl(seed: int, base: np.ndarray | None = None, noise: float = 0.3) -> pd.Series:
    """Daily PnL: ``base`` plus independent noise (a fresh bet when ``base`` is ``None``)."""
    rng = np.random.default_rng(seed)
    own = rng.normal(0, 1, len(DAYS))
    return pd.Series(own if base is None else base + noise * own, index=DAYS)


def entry(alpha_id: str, sharpe: float, **kw: Any) -> bd.Entry:
    kw.setdefault("day", TODAY)
    kw.setdefault("alpha_type", "regular")
    kw.setdefault("region", "USA")
    kw.setdefault("checks", PASS)
    kw.setdefault("checks_source", "check")
    return bd.Entry(alpha_id=alpha_id, train_sharpe=sharpe, **kw)


def families(board: bd.Board) -> list[bd.FamilyOut]:
    return [f for d in board.days for t in d.types for f in t.families]


BASE = np.random.default_rng(0).normal(0, 1, len(DAYS))


def test_correlated_pnl_forms_one_family_led_by_the_best_train_sharpe() -> None:
    board = bd.build(
        [
            entry("A", 1.2, pnl=pnl(1, BASE)),
            entry("B", 1.8, pnl=pnl(2, BASE)),
            entry("C", 1.5, pnl=pnl(3, BASE)),
            entry("D", 1.0, pnl=pnl(4)),
        ],
        0.6,
    )
    fams = families(board)
    assert len(fams) == 2
    big = next(f for f in fams if f.members)
    assert big.leader.alpha_id == "B"
    assert {m.alpha_id for m in big.members} == {"A", "C"}
    assert big.basis == "pnl"
    member = big.members[0]
    assert member.leader_correlation is not None and member.leader_correlation > 0.6
    assert "Same bet as B" in member.why_not[0]


def test_power_pool_and_regular_never_share_a_family_even_with_identical_pnl() -> None:
    pp_checks = [*PASS, {"name": "POWER_POOL_CORRELATION", "result": "PENDING"}]
    board = bd.build(
        [entry("R", 1.5, pnl=pnl(1, BASE)), entry("P", 1.4, pnl=pnl(2, BASE), checks=pp_checks)],
        0.6,
    )
    sets = {f.leader.alpha_id: f.comparison_set for f in families(board)}
    assert sets == {"R": "Regular · USA", "P": "Power Pool · USA"}


def test_regions_are_separate_comparison_sets() -> None:
    board = bd.build(
        [entry("U", 1.5, pnl=pnl(1, BASE)), entry("E", 1.4, pnl=pnl(2, BASE), region="EUR")],
        0.6,
    )
    assert len(families(board)) == 2


def test_without_pnl_same_template_on_same_field_is_one_structural_family() -> None:
    board = bd.build(
        [
            entry("S1", 1.1, field="close", template_id="t1"),
            entry("S2", 1.6, field="close", template_id="t1"),
            entry("S3", 1.3, field="volume", template_id="t1"),
        ],
        0.6,
    )
    fams = families(board)
    assert len(fams) == 2
    pair = next(f for f in fams if f.members)
    assert (pair.basis, pair.leader.alpha_id) == ("structure", "S2")
    assert board.missing.pnl == 3


def test_board_groups_by_day_newest_first_then_alpha_type() -> None:
    board = bd.build(
        [
            entry("Y", 1.0, day=YESTERDAY, pnl=pnl(1)),
            entry("T1", 1.0, pnl=pnl(2)),
            entry("T2", 1.0, pnl=pnl(3), alpha_type="atom"),
        ],
        0.6,
    )
    assert [d.day for d in board.days] == [TODAY, YESTERDAY]
    assert [t.alpha_type for t in board.days[0].types] == ["atom", "regular"]
    assert board.days[0].types[1].ready == 1


def test_not_ready_alpha_says_why_and_suggests_the_court_repair() -> None:
    board = bd.build([entry("F", 1.4, pnl=pnl(1), checks=FAIL_FITNESS)], 0.6)
    lead = families(board)[0].leader
    assert lead.status == "not_ready"
    assert lead.why_not == ["LOW_FITNESS failed (value 0.8, limit 1)."]
    assert [(r.check, r.key) for r in lead.repairs] == [("LOW_FITNESS", "LOW_FITNESS_FAST")]
    assert board.checklist == []


def test_unchecked_and_pending_alphas_say_what_is_missing() -> None:
    board = bd.build(
        [
            entry("U", 1.0, pnl=pnl(1), checks=[], checks_source=None),
            entry("P", 1.0, pnl=pnl(2), checks=[{"name": "SELF_CORRELATION", "result": "PENDING"}]),
        ],
        0.6,
    )
    by_id = {f.leader.alpha_id: f.leader for f in families(board)}
    assert by_id["U"].status == "unchecked"
    assert "not checked" in by_id["U"].why_not[0]
    assert by_id["P"].status == "pending"
    assert "SELF_CORRELATION" in by_id["P"].why_not[0]


def test_checklist_ranks_ready_leaders_by_what_they_add_not_by_sharpe() -> None:
    def perf(before: float, after: float) -> dict[str, Any]:
        return {"score": {"before": before, "after": after}}

    board = bd.build(
        [
            entry("HIGH_SHARPE", 2.5, pnl=pnl(1), performance=perf(100, 101)),
            entry("ADDS_MOST", 1.3, pnl=pnl(2), performance=perf(100, 140)),
            entry("NO_PERF", 3.0, pnl=pnl(3)),
            entry("REFUSED", 4.0, pnl=pnl(4), checks=FAIL_FITNESS),
        ],
        0.6,
    )
    assert [i.alpha_id for i in board.checklist] == ["ADDS_MOST", "HIGH_SHARPE", "NO_PERF"]
    assert board.checklist[0].score_delta == 40
    assert all(i.steps for i in board.checklist)


def test_test_sharpe_is_shown_but_never_ranks() -> None:
    board = bd.build(
        [
            entry("TRAIN_BEST", 2.0, pnl=pnl(1, BASE), test_sharpe=0.1),
            entry("TEST_BEST", 1.0, pnl=pnl(2, BASE), test_sharpe=3.0),
        ],
        0.6,
    )
    fam = families(board)[0]
    assert fam.leader.alpha_id == "TRAIN_BEST"
    assert fam.members[0].test_sharpe == 3.0


def test_quota_blocked_leader_is_ready_with_a_wait_step() -> None:
    checks = [*PASS, {"name": "REGULAR_SUBMISSION", "result": "FAIL"}]
    board = bd.build([entry("Q", 1.5, pnl=pnl(1), checks=checks)], 0.6)
    assert families(board)[0].leader.status == "ready"
    assert any("daily reset" in s for s in board.checklist[0].steps)


def test_power_pool_leader_carries_its_description_and_brain_format_checks() -> None:
    checks = [
        *PASS,
        {"name": "POWER_POOL_CORRELATION", "result": "PASS"},
        {"name": "POWER_POOL_DESCRIPTION_FORMAT", "result": "WARNING"},
    ]
    desc = bd.DescriptionOut(text="Idea: x", edited=True)
    board = bd.build([entry("PP", 1.5, pnl=pnl(1), checks=checks, description=desc)], 0.6)
    lead = families(board)[0].leader
    assert lead.description is not None
    assert [c.name for c in lead.description.checks] == ["POWER_POOL_DESCRIPTION_FORMAT"]
    assert board.checklist[0].power_pool


def test_correlation_over_ceiling_is_flagged_by_kind() -> None:
    board = bd.build(
        [entry("C", 1.5, pnl=pnl(1), checks=[], checks_source=None, correlation={"max": 0.75})],
        0.6,
    )
    lead = families(board)[0].leader
    assert lead.correlation is not None
    assert (lead.correlation.kind, lead.correlation.over) == ("self", True)


def test_apply_repair_lays_settings_over_and_wraps_the_expression() -> None:
    expr, settings = apply_repair("rank(close)", {"decay": 0}, "close", {"settings": {"decay": 6}})
    assert (expr, settings) == ("rank(close)", {"decay": 6})
    wrapped, _ = apply_repair("rank(close)", {}, "close", {"expr": "$x * rank(adv20)"})
    assert wrapped.replace(" ", "") == "(rank(close))*rank(adv20)"
