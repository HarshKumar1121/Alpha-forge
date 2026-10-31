"""The one interface research code uses to turn an expression into results.

The planner and the Hypothesis Court never talk to the Gym or to BRAIN directly; they talk to
a :class:`Simulator`. Today the only implementation is the Gym
(:class:`~.local.client.GymSimulator`). In Phase 6 an adapter over alpha-harness's engine
(slots, packer, tracker) implements the same Protocol, and the same planner then spends real
simulations. Code written against this interface must therefore be correct for BOTH — which
means it may only rely on what the Protocol promises, not on what the Gym happens to do.

What a caller may NOT assume
----------------------------
1. **Results are not instant.** ``simulate`` may take seconds on the Gym and many minutes on
   BRAIN (queues, Retry-After, 429s). Always await it; never poll it in a loop. The returned
   list is index-aligned with the requests; no other ordering is promised.
2. **One bad expression is a per-request outcome, not an exception.** A typo in one of ten
   candidates yields a :class:`SimOutcome` with ``error`` set; the other nine still run.
   ``simulate`` raises only when the simulator itself cannot work (signed out, network down).
3. **There is no raw field access.** A Simulator returns metrics and PnL, never field values:
   BRAIN does not hand them out. Field statistics need a :class:`FieldSource`, which only the
   Gym provides. On BRAIN, a field's behaviour must be learned from probe alphas
   (ARCHITECTURE.md §5.3) — so research code must work with ``local_data`` absent.
4. **There is no point-in-time guarantee.** The Gym's fields are point-in-time by construction.
   BRAIN's are whatever the dataset vendor stamps; look-ahead in a dataset is the caller's
   risk, and a too-good backtest should be treated as suspect, not celebrated.
5. **A simulation may cost quota.** ``costs_quota`` says so. Every quota-costing simulation must
   go through the Quota Governor with a lane (CLAUDE.md rule 3); the Gym costs nothing.
6. **``cached=True`` means the result was reused and nothing was spent.** Count it as free and
   never re-run it to "refresh" it.
7. **PnL is a separate, later call, and may be empty.** BRAIN serves it from a recordset that can
   lag the alpha. If ``ephemeral`` is True, results do not outlive the simulator object — save
   any PnL you need right away.
8. **Settings are BRAIN's vocabulary, and a backend may approximate them.** The Gym has 400
   synthetic stocks, one region and delay 1: it maps TOP3000 to all names, TOP1000 to the top
   third, SUBINDUSTRY to INDUSTRY, and ignores region and delay. A metric is only comparable
   with metrics from the same backend.
9. **There is no submit.** This Protocol has no such method and must never grow one
   (CLAUDE.md rule 1).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..brain.schemas import SimulationRequest, SimulationSettings, SimulationType

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    import pandas as pd


@dataclass(frozen=True, slots=True)
class SimOutcome:
    """What one request produced."""

    request: SimulationRequest
    #: ``None`` when the request failed.
    alpha_id: str | None
    #: BRAIN-shaped body: ``{"is": {..., "checks": [...]}, "train": {...}, "test": {...}}``.
    alpha: dict[str, Any] | None
    #: Reused from an earlier identical request; cost nothing.
    cached: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.alpha is not None


@runtime_checkable
class Simulator(Protocol):
    """Runs simulations. Read the module docstring before relying on anything else."""

    @property
    def name(self) -> str: ...

    @property
    def costs_quota(self) -> bool: ...

    @property
    def ephemeral(self) -> bool:
        """True when results vanish with this object, so PnL must be saved immediately."""
        ...

    @property
    def batch_size(self) -> int:
        """The most requests worth sending in one call (BRAIN multi-simulation takes 10)."""
        ...

    async def simulate(self, requests: Sequence[SimulationRequest]) -> list[SimOutcome]: ...

    async def pnl(self, alpha_id: str) -> list[tuple[date, float]]:
        """Daily PnL rows, oldest first. Empty when the simulator has none for this id."""
        ...


@runtime_checkable
class FieldSource(Protocol):
    """Raw field values. The Gym has them; BRAIN never will (see point 3 above)."""

    def field_names(self) -> set[str]: ...

    def frame(self, field: str) -> pd.DataFrame:
        """Dates x stocks."""
        ...

    def groups(self) -> dict[str, pd.Series]: ...


def request_for(
    expression: str, settings: Mapping[str, Any], sim_type: str = SimulationType.REGULAR
) -> SimulationRequest:
    """A simulation request from an expression and BRAIN settings (camelCase, as alpha-type
    YAML spells them). The request's own validators hold out the test period."""
    return SimulationRequest(
        type=SimulationType(sim_type),
        settings=SimulationSettings.model_validate(dict(settings)),
        regular=expression,
    )
