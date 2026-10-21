"""The Portfolio page: submitted Alphas combined at equal weight.

Equal weight is what BRAIN itself does. Its own pool figure (``before-and-after-performance``)
is the mean of the members' daily PnL and turnover over the days it exported, leaving out the
final days each Alpha's own figures count. Filtered to one partition (region, delay, Power Pool
or Regular), this reproduces it to the dollar, measured, in total and every yearly row.

Across regions the calendars differ, so the headline puts every Alpha on the union of their
trading days, counting a day an Alpha did not trade as zero: once it has started, each Alpha
keeps an equal share of capital whether or not its market was open.
"""

import asyncio
import itertools
from bisect import bisect_left
from collections import Counter
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import numpy as np

from ..vault import metrics

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

    from ..vault.metrics import Bools, Floats, Stats
    from ..vault.store import AlphaVault, PnlGrid

    type Framed = tuple[list[date], Floats, Floats, Bools]


def _blocks(found: dict[str, Stats | None]) -> dict[str, Any]:
    return {name: None if block is None else asdict(block) for name, block in found.items()}


def _split(meta: dict[str, dict[str, Any]], alpha_ids: list[str]) -> date | None:
    """The test start most members share. Every Alpha simulated with the default settings
    has the same one; a mixed pool takes the majority rather than failing."""
    starts = Counter(meta[a]["test_start"] for a in alpha_ids if meta.get(a, {}).get("test_start"))
    return starts.most_common(1)[0][0] if starts else None


def compute(
    series: dict[str, dict[date, tuple[float, float]]],
    meta: dict[str, dict[str, Any]],
    alpha_ids: list[str],
    cost_bps: float,
) -> dict[str, Any]:
    """Everything the Portfolio page shows for these Alphas. Ids without a series are left
    to the caller to report."""
    ids = [a for a in dict.fromkeys(alpha_ids) if series.get(a)]
    # Most recently submitted first, so the correlation grid reads newest to oldest.
    ids.sort(key=lambda a: str(meta.get(a, {}).get("date_submitted") or ""), reverse=True)
    if not ids:
        return {
            "alphas": 0,
            "ids": [],
            "test_start": None,
            "stats": None,
            "after_cost": None,
            "yearly": [],
            "dates": [],
            "curve": [],
            "periods": None,
            "after_cost_curve": [],
            "correlation": [],
            "top_pairs": [],
            "measured_pairs": 0,
            "highest": None,
        }
    framed = frame(series, meta, ids)
    dates, pnl, turnover = exported(*framed)
    filled = np.nan_to_num(pnl)
    count = len(ids)
    # BRAIN averages over the Alphas already trading that day: one that has not made its
    # first trade yet is left out of the count rather than diluting the others with zeros.
    started = np.maximum.accumulate((filled != 0) | (turnover != 0), axis=0)
    live = np.maximum(started.sum(axis=1), 1)
    book = filled.sum(axis=1) / live
    traded = turnover.sum(axis=1) / live
    split = _split(meta, ids)
    net = metrics.after_cost(book, traded, cost_bps)

    return {
        "alphas": count,
        "ids": ids,
        "test_start": split.isoformat() if split else None,
        "stats": _blocks(metrics.windows(dates, book, traded, split)),
        "after_cost": _blocks(metrics.windows(dates, net, traded, split)),
        # Whole calendar years, without the final days, as BRAIN's yearly rows are.
        "yearly": _yearly(dates, book, traded),
        "dates": [d.isoformat() for d in dates],
        "curve": np.cumsum(book).tolist(),
        "after_cost_curve": np.cumsum(net).tolist(),
        "periods": _periods(dates, book, traded, split),
        # Without the final days, though BRAIN's window ends with them, as for every pair here:
        # its correlations match to four decimals that way.
        **_correlation(ids, dates, pnl, metrics.window_start(framed[0][-1])),
    }


def highest_correlations(ids: list[str], framed: Framed) -> dict[str, float | None]:
    """Each Alpha's highest correlation with any other of ``ids``, measured as the grid below
    measures it. ``framed`` is their :func:`frame`."""
    if len(ids) < 2:
        return dict.fromkeys(ids)
    dates, pnl, _, closing = framed
    since = metrics.window_start(dates[-1])
    rho = metrics.correlations(np.where(closing, np.nan, pnl)[bisect_left(dates, since) :])
    np.fill_diagonal(rho, np.nan)
    return {
        a: None if np.isnan(row).all() else float(np.nanmax(row))
        for a, row in zip(ids, rho, strict=True)
    }


def net_grid(
    grid: PnlGrid, series: dict[str, dict[date, tuple[float, float]]], bps: float
) -> PnlGrid:
    """A :meth:`AlphaVault.pnl_grid` with each day's cost taken off. It holds only the days
    BRAIN exported, so no closing day is rebuilt from it."""
    ids, dates, pnl = grid
    # Without a turnover series an Alpha cannot be costed: dropped rather than left gross.
    costed = [j for j, a in enumerate(ids) if series.get(a)]
    ids, pnl = [ids[j] for j in costed], pnl[:, costed]
    traded = np.array(
        [[(series.get(a) or {}).get(d, (0.0, 0.0))[1] for a in ids] for d in dates]
    ).reshape(pnl.shape)
    return ids, dates, metrics.after_cost(pnl, traded, bps)


#: Alphas whose series :func:`net_figures_of` holds in memory at once.
NET_CHUNK = 200


async def net_figures_of(
    alphas: AlphaVault, alpha_ids: Iterable[str], bps: float
) -> dict[str, dict[str, float | None]]:
    """:func:`net_figures` for these Alphas, read from the vault a slice at a time: 3,000
    series at once peaked at 3 GB, against 0.5 GB and a third of the time in slices of 200."""
    found: dict[str, dict[str, float | None]] = {}
    for chunk in itertools.batched(dict.fromkeys(alpha_ids), NET_CHUNK, strict=False):
        series = await alphas.series(list(chunk))
        meta = await alphas.by_ids(list(series))
        found |= await asyncio.to_thread(net_figures, series, meta, bps)
    return found


def net_figures(
    series: dict[str, dict[date, tuple[float, float]]],
    meta: dict[str, dict[str, Any]],
    bps: float,
) -> dict[str, dict[str, float | None]]:
    """Each Alpha's figures recomputed on its PnL net of ``bps``, with the formulas that give
    BRAIN's own gross figures: in sample, and Sharpe in its train and test years. At zero bps,
    the gross figures BRAIN does not report, such as the K-ratio."""
    found: dict[str, dict[str, float | None]] = {}
    for alpha_id, days in series.items():
        info = meta.get(alpha_id, {})
        rows = metrics.with_closing([(d, p, t) for d, (p, t) in sorted(days.items())], info)
        if not rows:
            continue
        turnover = np.array([t for _, _, t in rows])
        pnl = metrics.after_cost(np.array([p for _, p, _ in rows]), turnover, bps)
        blocks = metrics.windows([d for d, _, _ in rows], pnl, turnover, info.get("test_start"))
        whole, train, test = blocks["in_sample"], blocks["train"], blocks["test"]
        found[alpha_id] = {
            "sharpe": whole.sharpe if whole else None,
            "fitness": whole.fitness if whole else None,
            "returns": whole.returns if whole else None,
            "drawdown": whole.drawdown if whole else None,
            "margin": whole.margin if whole else None,
            "train_sharpe": train.sharpe if train else None,
            "test_sharpe": test.sharpe if test else None,
            "k_ratio": metrics.k_ratio(pnl, turnover),
        }
    return found


def incremental_sharpes(ids: list[str], framed: Framed) -> dict[str, float | None]:
    """Each Alpha's Incremental Sharpe: the in-sample Sharpe of the equal-weighted Portfolio of
    ``ids`` with it, less the Sharpe of the same Portfolio without it. ``framed`` is their
    :func:`frame`.

    Both are combined exactly as :func:`compute` combines them.
    """
    if len(ids) < 2:
        return dict.fromkeys(ids)
    _, pnl, turnover = exported(*framed)
    filled = np.nan_to_num(pnl)
    started = np.maximum.accumulate((filled != 0) | (turnover != 0), axis=0)
    total, traded, live = filled.sum(axis=1), turnover.sum(axis=1), started.sum(axis=1)

    def sharpe(book: Floats, moved: Floats, count: Floats) -> float | None:
        held = np.maximum(count, 1)
        found = metrics.stats(book / held, moved / held)
        return found.sharpe if found else None

    whole = sharpe(total, traded, live)
    found: dict[str, float | None] = {}
    for col, alpha_id in enumerate(ids):
        without = sharpe(total - filled[:, col], traded - turnover[:, col], live - started[:, col])
        found[alpha_id] = whole - without if whole is not None and without is not None else None
    return found


def portfolio_correlations(ids: list[str], framed: Framed) -> dict[str, float | None]:
    """Each Alpha's correlation with the equal-weighted Portfolio of every *other* Alpha in
    ``ids``: the Portfolio as it stands without it. ``framed`` is their :func:`frame`.

    The rest are combined as :func:`compute` combines them, averaged over those already
    trading that day, and correlated with the Alpha's own daily PnL over BRAIN's correlation
    window, on the days BRAIN exported, as every correlation here is measured.
    """
    if len(ids) < 2:
        return dict.fromkeys(ids)
    # BRAIN's window ends on the last day it counts, rebuilt or not, as for every pair here.
    start = metrics.window_start(framed[0][-1])
    dates, pnl, turnover = exported(*framed)
    filled = np.nan_to_num(pnl)
    started = np.maximum.accumulate((filled != 0) | (turnover != 0), axis=0)
    total, live = filled.sum(axis=1), started.sum(axis=1)
    since = bisect_left(dates, start)
    own = pnl[since:]
    found: dict[str, float | None] = {}
    for col, alpha_id in enumerate(ids):
        others = (live - started[:, col])[since:]
        rest = np.full(len(others), np.nan)
        np.divide((total - filled[:, col])[since:], others, out=rest, where=others > 0)
        keep = ~np.isnan(own[:, col]) & ~np.isnan(rest)
        x, y = own[keep, col], rest[keep]
        measurable = len(x) >= metrics.MIN_OVERLAP and x.std() > 0 and y.std() > 0
        found[alpha_id] = float(np.corrcoef(x, y)[0, 1]) if measurable else None
    return found


def exported(
    dates: list[date], pnl: Floats, turnover: Floats, closing: Bools
) -> tuple[list[date], Floats, Floats]:
    """A :func:`frame` cut to the days BRAIN exported. Its own pool figures leave out the final
    days it counts in each Alpha's own: measured, the pool's PnL matches to the dollar only
    without them."""
    pnl = np.where(closing, np.nan, pnl)
    turnover = np.where(closing, 0.0, turnover)
    keep = ~np.isnan(pnl).all(axis=1)
    kept = [d for d, k in zip(dates, keep, strict=True) if k]
    return kept, pnl[keep], turnover[keep]


def frame(
    series: dict[str, dict[date, tuple[float, float]]],
    meta: dict[str, dict[str, Any]],
    ids: list[str],
    bps: float = 0.0,
) -> tuple[list[date], Floats, Floats, Bools]:
    """These Alphas laid out day by day: the calendar, PnL, turnover, and which cells are
    closing days. With ``bps``, PnL is net of that cost on each day's turnover.

    The cost comes off only once the closing days are rebuilt: they are BRAIN's gross IS PnL
    less the stored days, so a series netted beforehand would pour every day's cost back into
    them as one spike.

    A cell is NaN where the Alpha has no row for that day. ``closing`` marks the final days
    rebuilt from the Alpha's own figures rather than exported by BRAIN; correlations leave
    them out, which is what makes them match BRAIN's to four decimals.
    """
    # Each Alpha's stored days, oldest first, plus the final days BRAIN counts in its IS and
    # test figures but exports in no recordset (see :func:`metrics.closing_days`).
    days = {
        a: metrics.with_closing(
            [(d, p, t) for d, (p, t) in sorted(series[a].items())], meta.get(a, {})
        )
        for a in ids
    }
    dates = sorted({d for a in ids for d, _, _ in days[a]})
    at = {d: i for i, d in enumerate(dates)}
    pnl = np.full((len(dates), len(ids)), np.nan)
    turnover = np.zeros((len(dates), len(ids)))
    closing = np.zeros((len(dates), len(ids)), dtype=bool)
    for col, alpha_id in enumerate(ids):
        for day, value, traded in days[alpha_id]:
            pnl[at[day], col] = value
            turnover[at[day], col] = traded
            closing[at[day], col] = day not in series[alpha_id]
    if bps:
        pnl = metrics.after_cost(pnl, turnover, bps)
    return dates, pnl, turnover, closing


def _yearly(dates: list[date], book: Floats, traded: Floats) -> list[dict[str, Any]]:
    return [
        {"year": year} | asdict(found)
        for year, _, found in metrics.yearly(dates, book, traded, None)
    ]


def _periods(
    dates: list[date], book: Floats, traded: Floats, split: date | None
) -> dict[str, dict[str, str] | None]:
    """First and last day of each block, from the first trade on as the stats count them."""
    active = np.flatnonzero((book != 0) | (traded != 0))
    start = dates[int(active[0])] if active.size else dates[0]
    end = dates[-1]

    def span(first: date, last: date) -> dict[str, str]:
        return {"start": first.isoformat(), "end": last.isoformat()}

    return {
        "in_sample": span(start, end),
        "train": span(start, split) if split and start <= split else None,
        "test": span(max(start, split), end) if split and split <= end else None,
    }


#: Past this many Alphas a full grid is too large to send and draw: only the
#: most correlated pairs are returned.
GRID_LIMIT = 150
TOP_PAIRS = 20


def _correlation(ids: list[str], dates: list[date], pnl: Floats, since: date) -> dict[str, Any]:
    """Pairwise daily PnL correlation from ``since``, BRAIN's window, and the pair that collides
    most. The full grid only up to ``GRID_LIMIT`` Alphas; past it, the ``TOP_PAIRS`` most
    correlated, so the answer does not grow with the square of them."""
    rho = metrics.correlations(pnl[bisect_left(dates, since) :])
    left, right = np.triu_indices(len(ids), k=1)
    measured = ~np.isnan(rho[left, right])
    # ``None`` rather than a number when no pair shared enough days: zero would read as
    # "perfectly uncorrelated", the most reassuring thing a failed measurement could claim.
    highest = lowest = None
    if measured.any():
        ends: list[dict[str, Any]] = []
        for k in (int(np.nanargmax(rho[left, right])), int(np.nanargmin(rho[left, right]))):
            a, b = int(left[k]), int(right[k])
            ends.append({"a": ids[a], "b": ids[b], "correlation": float(rho[a, b])})
        highest, lowest = ends
    if len(ids) <= GRID_LIMIT:
        matrix = [[None if np.isnan(v) else round(float(v), 4) for v in row] for row in rho]
        return {
            "correlation": matrix,
            "top_pairs": [],
            "measured_pairs": int(measured.sum()),
            "highest": highest,
            "lowest": lowest,
        }
    left, right = left[measured], right[measured]
    values = rho[left, right]
    top = np.argsort(-values, kind="stable")[:TOP_PAIRS]
    return {
        "correlation": [],
        "top_pairs": [
            {"a": ids[left[k]], "b": ids[right[k]], "correlation": round(float(values[k]), 4)}
            for k in top
        ],
        "measured_pairs": int(values.size),
        "highest": highest,
        "lowest": lowest,
    }
