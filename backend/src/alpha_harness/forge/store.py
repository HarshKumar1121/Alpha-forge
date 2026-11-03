"""Where research results are kept: the simulation cache, PnL, field profiles, the pool.

The planner and the Court need exactly this much persistence, and no more. It is a Protocol so
that the research code does not care where it lives: :class:`InMemoryResearchStore` serves the
Gym and the tests now; a database-backed store over alpha-harness's SQLAlchemy layer lands with
the phase that owns those tables.

The cache is the important part. ``find`` is how "an expression + settings already paid for is
never paid for again" (CLAUDE.md rule 3) is enforced, and it keys on the same request hash
alpha-harness's engine uses for its own dedupe, so the two can never disagree about what counts
as the same simulation.

Ported from the subset of alpha-copilot ``store.py`` that the planner and Court call.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from ..engine.lifecycle import request_hash

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from ..brain.schemas import SimulationRequest

type PnlRows = list[tuple[date, float]]


@dataclass(frozen=True, slots=True)
class Experiment:
    """One simulation the research layer paid for (or reused), as the journal records it."""

    request: SimulationRequest
    alpha_id: str
    alpha: dict[str, Any]
    alpha_type: str
    role: str  # variant | probe | robustness | court
    stage: str
    hypothesis_id: str | int | None = None


@dataclass(frozen=True, slots=True)
class Hit:
    alpha_id: str
    alpha: dict[str, Any]


class ResearchStore(Protocol):
    async def find(self, request: SimulationRequest) -> Hit | None:
        """An earlier result for an identical request, or ``None``."""
        ...

    async def record(self, experiment: Experiment) -> None: ...

    async def save_pnl(self, alpha_id: str, rows: Sequence[tuple[date, float]]) -> None: ...

    async def load_pnl(self, alpha_id: str) -> PnlRows:
        """Empty when nothing was saved."""
        ...

    async def get_profile(self, field: str, scope: str) -> dict[str, Any] | None: ...

    async def save_profile(self, field: str, scope: str, profile: dict[str, Any]) -> None: ...

    async def pool(self, kind: str, region: str | None) -> list[str]:
        """Alpha ids of what you already submitted, to check new alphas against."""
        ...

    async def get_check(self, alpha_id: str) -> dict[str, Any] | None:
        """A BRAIN ``/check`` body already fetched for this alpha, or ``None``.

        Lets a pipeline resume after a restart without spending a second ``/check`` call on an
        alpha it already has an answer for.
        """
        ...

    async def save_check(self, alpha_id: str, body: dict[str, Any]) -> None: ...

    async def get_correlation(self, alpha_id: str, kind: str) -> dict[str, Any] | None:
        """A BRAIN correlation body already fetched for this alpha and ``kind`` ("self" or
        "power-pool"), or ``None``."""
        ...

    async def save_correlation(self, alpha_id: str, kind: str, body: dict[str, Any]) -> None: ...


@dataclass(slots=True)
class InMemoryResearchStore:
    """Everything in dicts. Gone when the process ends; enough for the Gym and for tests."""

    experiments: list[Experiment] = field(default_factory=list[Experiment])
    _cache: dict[str, Hit] = field(default_factory=dict[str, Hit])
    _pnl: dict[str, PnlRows] = field(default_factory=dict[str, PnlRows])
    _profiles: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict[tuple[str, str], dict[str, Any]]
    )
    _pool: dict[tuple[str, str | None], list[str]] = field(
        default_factory=dict[tuple[str, str | None], list[str]]
    )
    _checks: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    _correlations: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict[tuple[str, str], dict[str, Any]]
    )

    async def find(self, request: SimulationRequest) -> Hit | None:
        return self._cache.get(request_hash(request))

    async def record(self, experiment: Experiment) -> None:
        self.experiments.append(experiment)
        self._cache.setdefault(
            request_hash(experiment.request), Hit(experiment.alpha_id, experiment.alpha)
        )

    async def save_pnl(self, alpha_id: str, rows: Sequence[tuple[date, float]]) -> None:
        if rows:
            self._pnl[alpha_id] = list(rows)

    async def load_pnl(self, alpha_id: str) -> PnlRows:
        return list(self._pnl.get(alpha_id, []))

    async def get_profile(self, field: str, scope: str) -> dict[str, Any] | None:
        return self._profiles.get((field, scope))

    async def save_profile(self, field: str, scope: str, profile: dict[str, Any]) -> None:
        self._profiles[field, scope] = profile

    async def pool(self, kind: str, region: str | None) -> list[str]:
        return list(self._pool.get((kind, region), []))

    def set_pool(self, kind: str, region: str | None, alpha_ids: list[str]) -> None:
        self._pool[kind, region] = list(alpha_ids)

    async def get_check(self, alpha_id: str) -> dict[str, Any] | None:
        return self._checks.get(alpha_id)

    async def save_check(self, alpha_id: str, body: dict[str, Any]) -> None:
        self._checks[alpha_id] = body

    async def get_correlation(self, alpha_id: str, kind: str) -> dict[str, Any] | None:
        return self._correlations.get((alpha_id, kind))

    async def save_correlation(self, alpha_id: str, kind: str, body: dict[str, Any]) -> None:
        self._correlations[alpha_id, kind] = body
