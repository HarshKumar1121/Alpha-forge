"""Which submitted Alphas may enter Osmosis: the eligibility gate.

An Alpha is eligible only when its stored submission checks carry ``OSMOSIS_ALLOCATION`` with
result ``PASS``. Every other outcome is ineligible, each with a plain reason:

- ``WARNING`` means BRAIN did not generate the Daily Osmosis Rank (``messages.ts``). It is the
  label BRAIN gives many checked Alphas, so it is not read as a pass (design decision Q1).
- ``PENDING`` means BRAIN has not judged it yet; run Check Submission and wait.
- A missing entry means nobody has checked this Alpha since its results were stored.

This is deliberately separate from the vault's submittable verdict. ``OSMOSIS_ALLOCATION`` is in
``IGNORED_CHECKS`` (``brain/platform.py``), so the verdict never sees it: reusing
``gating_results`` here would call an Alpha eligible while BRAIN says its rank failed. A test
pins that difference.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

#: BRAIN's name for the check, matched whole (``OSMOSIS_ALLOCATION_X`` is a different check).
OSMOSIS_CHECK = "OSMOSIS_ALLOCATION"
#: The only result that makes an Alpha eligible.
ELIGIBLE_RESULT = "PASS"

Status = Literal["eligible", "pending", "refused", "warning", "absent", "unknown"]


@dataclass(frozen=True)
class OsmosisStatus:
    """One Alpha's Osmosis standing, with the reason shown on screen."""

    status: Status
    #: BRAIN's result as stored (``PASS``, ``FAIL``...), or ``None`` when there is none.
    result: str | None
    reason: str

    @property
    def eligible(self) -> bool:
        return self.status == "eligible"


def osmosis_status(checks: Sequence[Any] | None) -> OsmosisStatus:
    """The Osmosis standing read from one Alpha's stored check array.

    Reads only the stored array; it never calls BRAIN. If the array holds more than one
    ``OSMOSIS_ALLOCATION`` entry, the last one wins, matching how a re-check overwrites.
    """
    entries = [
        c
        for c in checks or []
        if isinstance(c, Mapping) and str(c.get("name", "")).upper() == OSMOSIS_CHECK
    ]
    if not entries:
        return OsmosisStatus(
            "absent", None, "No stored OSMOSIS_ALLOCATION result. Run Check Submission first."
        )
    raw = entries[-1].get("result")
    if raw is None or str(raw).strip() == "":
        return OsmosisStatus("pending", None, "BRAIN has not judged the Osmosis check yet.")
    result = str(raw).strip().upper()
    if result == ELIGIBLE_RESULT:
        return OsmosisStatus("eligible", result, "Osmosis check passed.")
    if result == "PENDING":
        return OsmosisStatus("pending", result, "BRAIN has not judged the Osmosis check yet.")
    if result in {"FAIL", "ERROR"}:
        return OsmosisStatus("refused", result, f"Osmosis check returned {result}.")
    if result == "WARNING":
        return OsmosisStatus(
            "warning", result, "BRAIN did not generate the Daily Osmosis Rank for this Alpha."
        )
    return OsmosisStatus("unknown", result, f"Unrecognised Osmosis result: {result}.")


def eligible_ids(statuses: Mapping[str, OsmosisStatus]) -> list[str]:
    """The Alpha IDs whose standing is eligible, in the order given."""
    return [alpha_id for alpha_id, s in statuses.items() if s.eligible]
