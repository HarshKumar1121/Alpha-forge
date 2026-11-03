"""``BrainSimulator``: the Simulator Protocol implemented over alpha-harness's own engine.

One instance per pipeline run, scoped to a single lane: everything it enqueues is tagged
``forge.<lane>`` so the Quota Governor's existing lane-of-task convention (CLAUDE.md rule 3)
admits and paces it correctly — no new admission call is needed here. The engine (already
running its own background tick loop) is what talks to BRAIN, honours Retry-After, and survives
a restart; this adapter only enqueues onto it and polls the local database for its own rows to
finish, which is a local read, not a BRAIN call, so it never violates the rate-limit rule either.
"""

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from sqlalchemy import select

from ...db.models import SimStatus, SimulationRecord
from ...vault.metrics import daily_pnl, with_turnover
from ..simulator import SimOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from ...brain.schemas import SimulationRequest
    from ...db.sqlite import Database
    from ...engine.slots import BatchEngine

log = structlog.get_logger(__name__)

#: Terminal statuses that mean a result exists and is usable.
OK_STATUSES = (SimStatus.COMPLETE, SimStatus.WARNING, SimStatus.SKIPPED)
#: How long one ``simulate()`` call waits for every request before giving up on stragglers.
#: BRAIN queues can run long; a pipeline round awaits this once rather than polling forever.
DEFAULT_TIMEOUT_SECONDS = 6 * 60 * 60.0


class AlphaFetcher(Protocol):
    """The slice of :class:`~...brain.endpoints.BrainEndpoints` this adapter actually needs —
    named separately so a test can stub just these two calls instead of the whole client."""

    async def alpha_body(self, alpha_id: str) -> dict[str, Any]: ...

    async def get_recordset(self, alpha_id: str, name: str) -> Any: ...


class BrainSimulator:
    """Satisfies :class:`~..simulator.Simulator` by driving the shared :class:`BatchEngine`."""

    name = "brain"
    costs_quota = True
    ephemeral = False
    batch_size = 10

    def __init__(
        self,
        engine: BatchEngine,
        db: Database,
        endpoints: AlphaFetcher,
        lane: str,
        *,
        poll_interval: float = 2.0,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._engine = engine
        self._db = db
        self._endpoints = endpoints
        self._lane = lane
        self._poll_interval = poll_interval
        self._timeout = timeout

    async def simulate(self, requests: Sequence[SimulationRequest]) -> list[SimOutcome]:
        """Enqueue every request onto the shared engine, then await all of them at once.

        Index-aligned with ``requests``, as the Protocol promises. A request that ends in
        error becomes an outcome with ``error`` set; only an engine/database failure raises.
        """
        asked = list(requests)
        if not asked:
            return []

        enqueued = await self._engine.enqueue(asked, task=f"forge.{self._lane}")
        outcomes: list[SimOutcome | None] = [None] * len(asked)
        pending: dict[int, int] = {}  # record id -> request index, for rows still in flight
        for entry in enqueued["outcomes"]:
            index = entry["index"]
            if entry["status"] == str(SimStatus.SKIPPED):
                outcomes[index] = await self._outcome(
                    asked[index],
                    entry["recordId"],
                    SimStatus.SKIPPED,
                    entry["alphaId"],
                    cached=True,
                )
            else:
                pending[entry["recordId"]] = index

        await self._await_pending(asked, outcomes, pending)
        return [outcome for outcome in outcomes if outcome is not None]

    async def _await_pending(
        self,
        asked: list[SimulationRequest],
        outcomes: list[SimOutcome | None],
        pending: dict[int, int],
    ) -> None:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._timeout
        while pending:
            async with self._db.session() as session:
                rows = (
                    await session.scalars(
                        select(SimulationRecord).where(SimulationRecord.id.in_(list(pending)))
                    )
                ).all()
            for row in rows:
                status = SimStatus(row.status)
                if not status.terminal:
                    continue
                index = pending.pop(row.id)
                outcomes[index] = await self._outcome(
                    asked[index], row.id, status, row.alpha_id, cached=False
                )
            if not pending:
                return
            if loop.time() >= deadline:
                for record_id, index in pending.items():
                    outcomes[index] = SimOutcome(
                        request=asked[index],
                        alpha_id=None,
                        alpha=None,
                        error=f"Timed out waiting for simulation record {record_id}.",
                    )
                return
            await asyncio.sleep(self._poll_interval)

    async def _outcome(
        self,
        request: SimulationRequest,
        record_id: int,
        status: SimStatus,
        alpha_id: str | None,
        *,
        cached: bool,
    ) -> SimOutcome:
        if status not in OK_STATUSES:
            return SimOutcome(
                request=request,
                alpha_id=alpha_id,
                alpha=None,
                error=f"Simulation {record_id} ended as {status}.",
            )
        if alpha_id is None:
            return SimOutcome(
                request=request,
                alpha_id=None,
                alpha=None,
                error=f"Simulation {record_id} finished without an alpha id.",
            )
        try:
            alpha: dict[str, Any] = await self._endpoints.alpha_body(alpha_id)
        except Exception as exc:  # noqa: BLE001 - one bad fetch must not fail the whole batch
            log.warning("pipelines.alpha_body_failed", alpha_id=alpha_id, error=str(exc))
            return SimOutcome(request=request, alpha_id=alpha_id, alpha=None, error=str(exc))
        return SimOutcome(request=request, alpha_id=alpha_id, alpha=alpha, cached=cached)

    async def pnl(self, alpha_id: str) -> list[tuple[date, float]]:
        recordset = await self._endpoints.get_recordset(alpha_id, "pnl")
        return daily_pnl(recordset.rows())

    async def turnover(self, alpha_id: str) -> list[tuple[date, float]]:
        """Daily turnover aligned to the ``pnl`` recordset's dates (see
        ``vault.metrics.with_turnover`` for the one-day-early shift this corrects for). Not
        calibrated against ``yearly-stats`` (``vault.metrics.calibrate`) — good enough for a
        chart's shape, not BRAIN's own displayed figure to the decimal."""
        pnl_recordset = await self._endpoints.get_recordset(alpha_id, "pnl")
        turnover_recordset = await self._endpoints.get_recordset(alpha_id, "turnover")
        days = with_turnover(daily_pnl(pnl_recordset.rows()), turnover_recordset.rows())
        return [(d, t) for d, _, t in days]

    async def yearly_stats(self, alpha_id: str) -> list[dict[str, Any]]:
        recordset = await self._endpoints.get_recordset(alpha_id, "yearly-stats")
        return recordset.rows()
