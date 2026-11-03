"""Reading BRAIN's checks for the board: each check's role, the verdict, and what to do next.

The verdict itself is :func:`alpha_harness.vault.yields.verdict`, the one rule the rest of the
app already uses: submittable means BRAIN says so, never a local guess. This module only adds
what the board shows around it:

- each check's *role*: ``gating`` decides submission; ``quota`` is about today's submission
  allowance, not the alpha (``QUOTA_CHECKS``), so it never makes an alpha "not ready", it only
  means "submit tomorrow"; ``label`` is one of ``IGNORED_CHECKS``, shown but never deciding;
- why a refused alpha was refused, one reason per failing gating check, each with the
  ``court.yaml`` repair that answers it (``check_repairs``) or plain advice (``check_advice``).
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from ...brain.platform import IGNORED_CHECKS, QUOTA_CHECKS
from ...vault.yields import REFUSING, Verdict, is_power_pool, verdict, without_quota_checks
from .. import config

Role = Literal["gating", "quota", "label"]
#: The board's word for an alpha: BRAIN's verdict, plus ``unchecked`` when BRAIN said nothing.
Status = Literal["ready", "pending", "not_ready", "unchecked"]
#: BRAIN's own description checks, shown with a Power Pool leader's draft.
DESCRIPTION_CHECKS = ("POWER_POOL_DESCRIPTION_LENGTH", "POWER_POOL_DESCRIPTION_FORMAT")

_STATUS: dict[Verdict | None, Status] = {
    "submittable": "ready",
    "pending": "pending",
    "refused": "not_ready",
    None: "unchecked",
}


@dataclass(frozen=True, slots=True)
class CheckRow:
    name: str
    result: str
    role: Role
    value: Any = None
    limit: Any = None


@dataclass(frozen=True, slots=True)
class Repair:
    """A suggested fix for one failing check. ``key`` is ``None`` when only advice exists."""

    check: str
    key: str | None
    why: str
    needs: tuple[str, ...] = ()


@dataclass(slots=True)
class Reading:
    status: Status
    rows: list[CheckRow]
    power_pool: bool
    #: Gating checks that failed (FAIL or ERROR), BRAIN's names.
    failing: list[str] = field(default_factory=list[str])
    pending: list[str] = field(default_factory=list[str])
    #: Quota checks that failed: the alpha is fine, today's allowance is not.
    quota_blocked: list[str] = field(default_factory=list[str])


def role_of(name: str) -> Role:
    upper = name.upper()
    if upper in QUOTA_CHECKS:
        return "quota"
    if upper in IGNORED_CHECKS:
        return "label"
    return "gating"


def read(checks: list[dict[str, Any]], simulation_mode: str | None = None) -> Reading:
    """Every check with its role, and the board status BRAIN's verdict implies."""
    rows = [
        CheckRow(
            name=str(c.get("name", "")),
            result=str(c.get("result", "")).upper(),
            role=role_of(str(c.get("name", ""))),
            value=c.get("value"),
            limit=c.get("limit"),
        )
        for c in checks
        if c.get("name")
    ]
    gating = [r for r in rows if r.role == "gating"]
    return Reading(
        status=_STATUS[verdict(without_quota_checks(checks), simulation_mode)],
        rows=rows,
        power_pool=is_power_pool(checks),
        failing=[r.name for r in gating if r.result in REFUSING],
        pending=[r.name for r in gating if r.result == "PENDING"],
        quota_blocked=[r.name for r in rows if r.role == "quota" and r.result in REFUSING],
    )


def repair_for(check: str) -> Repair:
    """The ``court.yaml`` repair for a failing check, or advice when none applies."""
    court = config.court()
    key = (court.get("check_repairs") or {}).get(check.upper())
    rule = (court.get("repairs") or {}).get(key) if key else None
    if rule:
        return Repair(check, key, str(rule.get("why", "")), tuple(rule.get("needs") or ()))
    advice = (court.get("check_advice") or {}).get(check.upper())
    return Repair(check, None, str(advice or "No known repair for this check."))


def description_rows(reading: Reading) -> list[CheckRow]:
    return [r for r in reading.rows if r.name.upper() in DESCRIPTION_CHECKS]
