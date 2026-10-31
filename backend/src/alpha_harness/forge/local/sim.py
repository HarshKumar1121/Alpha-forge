"""A local, BRAIN-like backtest — the pipeline from signal to PnL, every step visible.

    signal --(universe mask)--> --(decay)--> --(neutralize)--> --(scale to book)-->
    --(truncate)--> weights w[t]  ;  pnl[t+1] = sum(w[t] * returns[t+1])

Timing: ``w[t]`` uses data up to the close of day t and earns day t+1's return. So there is no
look-ahead *if* your fields are themselves point-in-time (the Gym's are).

Besides BRAIN's headline stats it reports what a real desk looks at first when judging a signal
(the alphalens-style "factor tear sheet"):

* IC — Spearman correlation between today's signal and tomorrow's returns, across stocks.
  A good equity signal has a mean IC of only ~0.01-0.05. Sharpe comes from *breadth*:
  roughly  Sharpe ≈ IC * sqrt(#independent bets per year)  (Grinold's fundamental law).
* IC decay — IC against returns h days ahead. Fast decay = short-horizon signal = high turnover.
* Quintile returns — average next-day return of each fifth of the ranked universe.
  You want a monotonic staircase, not one lucky extreme bucket.

Ported from alpha-copilot ``local/sim.py``. The check limits now come from the ``regular``
alpha type's YAML instead of constants here (CLAUDE.md rule 7).
"""

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from ...brain.platform import BOOK_SIZE, FITNESS_TURNOVER_FLOOR, TRADING_DAYS
from .engine import Engine, decay_linear, group_apply

if TYPE_CHECKING:
    from ..alpha_types import AlphaType

BOOK = BOOK_SIZE
DAYS = TRADING_DAYS
#: Horizons, in days, at which IC decay is measured.
IC_HORIZONS = (1, 2, 5, 10, 21)


@dataclass(frozen=True, slots=True)
class SimSettings:
    universe: int | None = None  # top-N by cap each day; None = all
    decay: int = 0
    neutralization: str = "MARKET"  # NONE | MARKET | <group name>
    truncation: float = 0.08
    test_years: float = 2.0


@dataclass(frozen=True, slots=True)
class CheckLimits:
    """BRAIN-like Regular checks, so practice locally feels like the real thing."""

    min_sharpe: float = 1.25
    min_fitness: float = 1.0
    min_turnover: float = 0.01
    max_turnover: float = 0.7
    max_weight: float = 0.1
    sub_universe_ratio: float = 0.75

    @classmethod
    def from_alpha_type(cls, alpha_type: AlphaType) -> CheckLimits:
        """Read the limits from an alpha type's ``is.*`` gates and ``gym_checks`` block."""
        gates, extra = alpha_type.gates, alpha_type.raw.get("gym_checks") or {}
        default = cls()

        def bound(key: str, side: str, fallback: float) -> float:
            value = (gates.get(key) or {}).get(side)
            return fallback if value is None else float(value)

        return cls(
            min_sharpe=bound("is.sharpe", "min", default.min_sharpe),
            min_fitness=bound("is.fitness", "min", default.min_fitness),
            min_turnover=bound("is.turnover", "min", default.min_turnover),
            max_turnover=bound("is.turnover", "max", default.max_turnover),
            max_weight=float(extra.get("concentrated_weight", default.max_weight)),
            sub_universe_ratio=float(extra.get("sub_universe_ratio", default.sub_universe_ratio)),
        )


@dataclass(slots=True)
class SimResult:
    expression: str
    settings: SimSettings
    weights: pd.DataFrame
    pnl: pd.Series
    turnover: pd.Series
    stats: dict[str, dict[str, float]]  # is / train / test
    ic: dict[str, float]
    ic_decay: dict[int, float]
    quintiles: list[float]
    yearly: dict[int, float]
    sub_universe_sharpe: float
    test_start: pd.Timestamp
    checks: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def summary(self) -> str:
        s, tr, te = self.stats["is"], self.stats["train"], self.stats["test"]
        fails = [c["name"] for c in self.checks if c["result"] == "FAIL"]
        return "\n".join(
            [
                f"Expression : {self.expression}",
                f"Sharpe     : IS {s['sharpe']:.2f} | train {tr['sharpe']:.2f} | "
                f"test {te['sharpe']:.2f}",
                f"Fitness {s['fitness']:.2f}  Returns {s['returns']:.1%}  "
                f"Turnover {s['turnover']:.1%}  Drawdown {s['drawdown']:.1%}  "
                f"Margin {s['margin'] * 1e4:.1f}bps",
                f"IC mean {self.ic['mean']:.4f}  IC t-stat {self.ic['t']:.1f}  "
                f"IC>0 on {self.ic['hit']:.0%} of days",
                "IC decay   : " + "  ".join(f"h{h}:{v:+.4f}" for h, v in self.ic_decay.items()),
                "Quintiles  : "
                + "  ".join(f"Q{i + 1}:{q * 1e4:+.1f}bp" for i, q in enumerate(self.quintiles)),
                "Yearly     : " + "  ".join(f"{y}:{v:+.1f}" for y, v in self.yearly.items()),
                f"Large-cap half Sharpe: {self.sub_universe_sharpe:.2f}",
                "Checks     : " + ("all pass" if not fails else "FAIL " + ", ".join(fails)),
            ]
        )


_STAT_KEYS = ("sharpe", "returns", "turnover", "fitness", "drawdown", "margin")


def _stats(pnl: pd.Series, turnover: pd.Series, traded: pd.Series) -> dict[str, float]:
    sd = float(pnl.std()) if len(pnl) >= 20 else 0.0
    if not sd > 0:
        return dict.fromkeys(_STAT_KEYS, math.nan)
    sharpe = float(pnl.mean()) / sd * math.sqrt(DAYS)
    returns = float(pnl.mean()) * DAYS / (BOOK / 2)
    to = float(turnover.mean())
    cum = pnl.cumsum()
    dd = float((cum.cummax() - cum).max()) / (BOOK / 2)
    total_traded = float(traded.sum())
    margin = float(pnl.sum()) / total_traded if total_traded else math.nan
    fitness = sharpe * math.sqrt(abs(returns) / max(to, FITNESS_TURNOVER_FLOOR))
    return {
        "sharpe": sharpe,
        "returns": returns,
        "turnover": to,
        "fitness": fitness,
        "drawdown": dd,
        "margin": margin,
    }


def weights_from(
    signal: pd.DataFrame, cap: pd.DataFrame, groups: dict[str, pd.Series], st: SimSettings
) -> pd.DataFrame:
    x = signal.copy()
    if st.universe:
        x = x.where(cap.rank(axis=1, ascending=False) <= st.universe)
    if st.decay > 1:
        x = decay_linear(x, st.decay)
    if st.neutralization == "MARKET":
        x = x.sub(x.mean(axis=1), axis=0)
    elif st.neutralization != "NONE":
        x = group_apply(x, groups[st.neutralization.lower()], "demean")
    w = x.div(x.abs().sum(axis=1), axis=0)
    # Truncation: cap any one stock's share of the book, then re-scale (twice is plenty).
    for _ in range(2):
        w = w.clip(-st.truncation, st.truncation)
        w = w.div(w.abs().sum(axis=1), axis=0)
    return (w * BOOK).fillna(0.0)


def _spearman_rows(a: pd.DataFrame, b: pd.DataFrame) -> pd.Series:
    ra, rb = a.rank(axis=1), b.rank(axis=1)
    mask = ra.notna() & rb.notna()
    ra, rb = ra.where(mask), rb.where(mask)
    ra = ra.sub(ra.mean(axis=1), axis=0)
    rb = rb.sub(rb.mean(axis=1), axis=0)
    return (ra * rb).sum(axis=1) / np.sqrt((ra**2).sum(axis=1) * (rb**2).sum(axis=1))


def _sharpe(pnl: pd.Series) -> float:
    sd = float(pnl.std())
    return float(pnl.mean()) / sd * math.sqrt(DAYS) if sd > 0 else math.nan


def simulate(
    expression: str,
    fields: dict[str, pd.DataFrame],
    groups: dict[str, pd.Series],
    settings: SimSettings | None = None,
    *,
    diagnostics: bool = True,
    limits: CheckLimits | None = None,
) -> SimResult:
    st = settings or SimSettings()
    sig = Engine(fields, groups)(expression)
    ret = fields["returns"]
    cap = fields["cap"]
    w = weights_from(sig, cap, groups, st)
    fwd = ret.shift(-1)
    pnl = (w * fwd).sum(axis=1).iloc[:-1]
    traded = w.diff().abs().sum(axis=1).iloc[:-1]
    turnover = traded / BOOK
    start = (w.abs().sum(axis=1) > 0).idxmax()
    pnl, traded, turnover = pnl[start:], traded[start:], turnover[start:]

    split = pd.Timestamp(pnl.index[-1]) - pd.DateOffset(days=int(365 * st.test_years))
    blocks = {"is": slice(None), "train": slice(None, split), "test": slice(split, None)}
    stats = {k: _stats(pnl[v], turnover[v], traded[v]) for k, v in blocks.items()}

    if not diagnostics:
        nan_ic = {"mean": math.nan, "t": math.nan, "hit": math.nan}
        return SimResult(
            expression, st, w, pnl, turnover, stats, nan_ic, {}, [], {}, math.nan, split
        )

    ic_series = _spearman_rows(sig, fwd).dropna()
    ic_sd = float(ic_series.std()) if len(ic_series) > 2 else 0.0
    ic = {
        "mean": float(ic_series.mean()),
        "t": float(ic_series.mean()) / ic_sd * math.sqrt(len(ic_series)) if ic_sd > 0 else math.nan,
        "hit": float((ic_series > 0).mean()),
    }
    ic_decay = {h: float(_spearman_rows(sig, ret.shift(-h)).mean()) for h in IC_HORIZONS}

    ranks = sig.rank(axis=1, pct=True)
    quintiles = [
        float(
            pd.Series(fwd.where((ranks > q / 5) & (ranks <= (q + 1) / 5)).to_numpy().ravel()).mean()
        )
        for q in range(5)
    ]
    years = pd.DatetimeIndex(pnl.index).year
    yearly = {
        int(y): round(_sharpe(g), 2)
        for y, g in pnl.groupby(years)
        if len(g) > 20 and float(g.std()) > 0
    }

    # The large-cap half: fewer, more liquid names, where a tradeable effect must still live.
    big = cap.rank(axis=1, pct=True) > 0.5
    w_big = weights_from(sig.where(big), cap, groups, st)
    sub = _sharpe((w_big * fwd).sum(axis=1).iloc[:-1][start:])

    res = SimResult(
        expression, st, w, pnl, turnover, stats, ic, ic_decay, quintiles, yearly, sub, split
    )
    res.checks = checks(res, limits or CheckLimits())
    return res


def checks(r: SimResult, limits: CheckLimits) -> list[dict[str, Any]]:
    """BRAIN-like Regular D1 checks, so practice locally feels like the real thing."""
    s = r.stats["is"]
    max_w = float((r.weights.abs().max(axis=1) / BOOK).max())
    rules = [
        ("LOW_SHARPE", s["sharpe"], limits.min_sharpe, "min"),
        ("LOW_FITNESS", s["fitness"], limits.min_fitness, "min"),
        ("LOW_TURNOVER", s["turnover"], limits.min_turnover, "min"),
        ("HIGH_TURNOVER", s["turnover"], limits.max_turnover, "max"),
        ("CONCENTRATED_WEIGHT", max_w, limits.max_weight, "max"),
        # Half the stocks = less breadth, so Sharpe should shrink by ~sqrt(1/2) even for a real
        # effect (fundamental law). Approximates BRAIN's rule; BRAIN's own check is final.
        (
            "LOW_SUB_UNIVERSE_SHARPE",
            r.sub_universe_sharpe,
            limits.sub_universe_ratio * math.sqrt(0.5) * s["sharpe"],
            "min",
        ),
    ]
    out: list[dict[str, Any]] = []
    for name, v, lim, kind in rules:
        ok = (v >= lim) if kind == "min" else (v <= lim)
        out.append(
            {
                "name": name,
                "value": v,
                "limit": lim,
                "result": "PASS" if ok and not math.isnan(v) else "FAIL",
            }
        )
    return out


def as_brain_alpha(r: SimResult) -> dict[str, Any]:
    """Shape a local result like a BRAIN alpha body, so gates and the journal reuse it."""
    return {
        "id": None,
        "is": {**r.stats["is"], "checks": r.checks},
        "train": r.stats["train"],
        "test": r.stats["test"],
    }
