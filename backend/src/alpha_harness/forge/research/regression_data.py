"""Getting each candidate field's *series* for the Regression Lab.

BRAIN never hands out raw field values (``forge/simulator.py``'s module docstring, point 3): a
field's behaviour only comes back as a simulated alpha's PnL, exactly like ``factors.py``'s
``factor_series``. This module is a thin field-flavoured sibling of that function, not a new
concept — same ``Researcher``/``Cand`` machinery, same per-scope cache idiom, just one
level-probe expression (``rank(field)``, the simplest P1 "level" probe ``profiler.py`` already
knows about) per field instead of one per named factor.

Quota/BRAIN note (CLAUDE.md rule 3): fetching a field's series *is* a BRAIN simulation, tagged
to a lane and going through the Quota Governor like any other spend — see
``api/forge_regression.py``, which calls this module from inside an ``explore``-lane admission.
Only the regression math itself (``regression.py``) is a pure function over already-fetched
series and spends nothing.
"""

from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from .. import analytics as an
from ..store import InMemoryResearchStore
from .planner import Cand, Researcher

if TYPE_CHECKING:
    from ..alpha_types import AlphaType
    from ..simulator import Simulator

#: One store per scope, prefixed so a field fetched for the Regression Lab is never confused
#: with (or re-paid against) the same field id fetched by ``factor_series`` or the planner —
#: each keeps its own cache namespace even though all three key off "region/delay/universe".
_stores: dict[str, InMemoryResearchStore] = {}


def _store_for(scope: str) -> InMemoryResearchStore:
    return _stores.setdefault(f"regression:{scope}", InMemoryResearchStore())


async def field_series(
    simulator: Simulator,
    alpha_type: AlphaType,
    settings: dict[str, Any],
    field_ids: list[str],
    *,
    store: InMemoryResearchStore | None = None,
) -> dict[str, pd.Series]:
    """Simulate (or reuse the cached result for) the simplest level probe, ``rank(field_id)``,
    for every field in ``field_ids`` under ``settings``, and return each one's daily PnL.

    A field already fetched for this scope is never paid for twice (CLAUDE.md rule 3): the
    per-scope store is checked by ``Researcher.sim`` before anything is sent to the simulator.
    """
    if not field_ids:
        return {}
    scope = "{region}/{delay}/{universe}".format(**settings)
    researcher = Researcher(
        store or _store_for(scope),
        simulator,
        alpha_type,
        {},
        [],
        budget=len(field_ids),
        scope=scope,
    )
    cands = [
        Cand(f"rank({fid})", dict(settings), fid, "_regression", "regression", stage="regression")
        for fid in field_ids
    ]
    await researcher.sim(cands, role="regression")
    out: dict[str, pd.Series] = {}
    for fid, cand in zip(field_ids, cands, strict=True):
        if cand.alpha_id is None:
            continue
        out[fid] = await researcher.pnl_series(cand.alpha_id)
    return out


async def target_series(
    simulator: Simulator,
    alpha_type: AlphaType,
    settings: dict[str, Any],
    kind: Literal["vault", "gym", "field"],
    ref: str,
    *,
    store: InMemoryResearchStore | None = None,
) -> pd.Series:
    """The target to regress against, by kind:

    - ``"field"``: a raw field used as the target — just ``field_series`` for one id.
    - ``"vault"``: an existing alpha's recorded PnL (the research store's cache, falling back to
      the simulator's own ``pnl`` call for an alpha this scope's store hasn't seen yet — no new
      storage, reusing whatever the vault already persists).
    - ``"gym"``: ``ref`` is a free-form expression run through the simulator as a synthetic
      exploration target (free on the Gym, since ``GymSimulator.costs_quota`` is ``False``).
    """
    if kind == "field":
        series = await field_series(simulator, alpha_type, settings, [ref], store=store)
        return series.get(ref, pd.Series(dtype=float))

    scope = "{region}/{delay}/{universe}".format(**settings)
    the_store = store or _store_for(scope)

    if kind == "vault":
        rows = await the_store.load_pnl(ref)
        if not rows:
            rows = await simulator.pnl(ref)
        return an.to_series(rows) if rows else pd.Series(dtype=float)

    if kind == "gym":
        researcher = Researcher(the_store, simulator, alpha_type, {}, [], budget=1, scope=scope)
        cand = Cand(ref, dict(settings), "_gym_target", "_regression_target", "regression")
        await researcher.sim([cand], role="regression")
        if cand.alpha_id is None:
            return pd.Series(dtype=float)
        return await researcher.pnl_series(cand.alpha_id)

    raise ValueError(f"unknown target kind: {kind!r}")
