"""A scriptable, in-memory ``Simulator`` double for pipeline stage tests.

CLAUDE.md rule 4: no code path may spend real BRAIN quota in tests. The orchestrator built in
Phase 6b talks only to the :class:`~..simulator.Simulator` Protocol, never to the engine
directly, so a fake at that level is enough to exercise stage order, budget limits,
successive-halving selection, and resume-after-restart without touching :class:`BatchEngine`,
:class:`QuotaGovernor`, or the network at all.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..simulator import SimOutcome

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date

    from ...brain.schemas import SimulationRequest


@dataclass
class ScriptedResult:
    """What :class:`FakeSimulator` should hand back for one request.

    ``metrics`` is folded into the outcome's ``alpha`` body under ``train``/``test``, so a test
    can plant a Sharpe or a check result for a round-selection or correlation-choice assertion
    without constructing a full BRAIN-shaped alpha by hand.
    """

    alpha_id: str | None = None
    train: dict[str, Any] = field(default_factory=dict)
    test: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    cached: bool = False
    error: str | None = None


class FakeSimulator:
    """Satisfies :class:`~..simulator.Simulator`. ``costs_quota=True``, like ``BrainSimulator``,
    so a test exercising budget/lane logic sees the same accounting it would against BRAIN.
    """

    name = "fake"
    costs_quota = True
    ephemeral = False
    batch_size = 10

    def __init__(
        self,
        script: Callable[[SimulationRequest], ScriptedResult] | None = None,
        pnl_by_alpha: dict[str, list[tuple[date, float]]] | None = None,
    ) -> None:
        #: Per-request outcome chosen by a test. Defaults to a bare pass with no metrics.
        self._script = script or (lambda request: ScriptedResult(alpha_id=f"FAKE-{id(request)}"))
        self._pnl = pnl_by_alpha or {}
        #: Every call's requests, in order, for assertions on what was actually asked for.
        self.calls: list[list[SimulationRequest]] = []
        self.spent = 0

    async def simulate(self, requests: Sequence[SimulationRequest]) -> list[SimOutcome]:
        asked = list(requests)
        self.calls.append(asked)
        outcomes: list[SimOutcome] = []
        for request in asked:
            planned = self._script(request)
            if planned.error is not None:
                outcomes.append(
                    SimOutcome(request=request, alpha_id=None, alpha=None, error=planned.error)
                )
                continue
            if not planned.cached:
                self.spent += 1
            alpha = {
                "id": planned.alpha_id,
                "is": {"checks": planned.checks},
                "train": planned.train,
                "test": planned.test,
            }
            outcomes.append(
                SimOutcome(
                    request=request,
                    alpha_id=planned.alpha_id,
                    alpha=alpha,
                    cached=planned.cached,
                )
            )
        return outcomes

    async def pnl(self, alpha_id: str) -> list[tuple[date, float]]:
        return list(self._pnl.get(alpha_id, []))

    async def turnover(self, alpha_id: str) -> list[tuple[date, float]]:  # noqa: ARG002 - no fixture yet
        return []

    async def yearly_stats(self, alpha_id: str) -> list[dict[str, Any]]:  # noqa: ARG002 - no fixture yet
        return []
