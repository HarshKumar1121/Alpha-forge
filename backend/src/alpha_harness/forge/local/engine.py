"""Evaluate a Fast Expression on a panel of DataFrames (rows = dates, columns = stocks).

This is what BRAIN does on its servers, written out so you can see it. Every operator here is a
few lines of pandas — read them. Knowing exactly what ``ts_rank`` or ``group_neutralize`` does to
a matrix is the difference between writing alphas and guessing at them.

Semantics follow BRAIN's documentation as closely as a small engine can. Results will NOT match
BRAIN exactly (different data, different details); use this to learn and to pre-screen ideas.

Ported from alpha-copilot ``local/engine.py``.
"""

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from ..expr import Node, parse

if TYPE_CHECKING:
    from collections.abc import Callable

type Frame = pd.DataFrame
type Value = Any  # a Frame or a scalar


class EvalError(ValueError):
    pass


# --- helpers ---------------------------------------------------------------------------------


def _num(node: Node) -> float:
    if node.kind == "num":
        return float(node.value)
    if node.kind == "unary" and node.value == "-" and node.children[0].kind == "num":
        return -float(node.children[0].value)
    raise EvalError("Expected a number (e.g. a lookback) here")


def _d(x: Value) -> int:
    """A lookback. Negative or zero would read the future or nothing, so both are refused."""
    d = int(x)
    if d < 1:
        raise EvalError("Lookback must be >= 1")
    return d


def group_apply(x: Frame, groups: pd.Series, how: str) -> Frame:
    """Apply a cross-sectional transform within each group, every day.

    ``groups`` maps stock -> group label: static here, though on BRAIN it can change over time.
    """
    g = groups.reindex(x.columns)
    t = x.T.groupby(g.to_numpy())
    if how == "demean":
        out = x.T - t.transform("mean")
    elif how == "rank":
        out = t.rank(pct=True)
    elif how == "zscore":
        out = (x.T - t.transform("mean")) / t.transform("std")
    elif how == "mean":
        out = t.transform("mean")
    else:
        raise EvalError(how)
    return out.T


def decay_linear(x: Frame, d: int) -> Frame:
    """Weighted average of the last d days, newest weight d, oldest weight 1. NaNs skipped."""
    num = pd.DataFrame(0.0, index=x.index, columns=x.columns)
    den = num.copy()
    for k in range(d):
        lagged = x.shift(k)
        wk = float(d - k)
        num += lagged.fillna(0.0) * wk
        den += lagged.notna() * wk
    return num / den.where(den > 0)


def _ts_rank(x: Frame, d: int) -> Frame:
    return x.rolling(d, min_periods=max(2, d // 2)).rank(pct=True)


def _trade_when(cond: Frame, alpha: Frame, exit_: Value) -> Frame:
    """If exit > 0 -> flat (NaN). Else if cond > 0 -> new alpha. Else keep yesterday's."""
    out = alpha.where(cond > 0).ffill()
    if isinstance(exit_, pd.DataFrame):
        return out.mask(exit_ > 0)
    if float(exit_) > 0:
        return out * np.nan
    return out


def _if_else(c: Frame, a: Value, b: Value) -> Frame:
    if isinstance(a, pd.DataFrame):
        return a.where(c > 0, b)
    if isinstance(b, pd.DataFrame):
        return b.where(~(c > 0), a)
    return pd.DataFrame(np.where(c > 0, a, b), index=c.index, columns=c.columns)


def _winsorize(x: Frame, std: float = 4) -> Frame:
    mean, sd = x.mean(axis=1), x.std(axis=1)
    return x.clip(lower=mean - std * sd, upper=mean + std * sd, axis=0)


OPERATORS: dict[str, Callable[..., Value]] = {
    # cross-sectional
    "rank": lambda x: x.rank(axis=1, pct=True),
    "zscore": lambda x: x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1), axis=0),
    "scale": lambda x: x.div(x.abs().sum(axis=1), axis=0),
    "normalize": lambda x: x.sub(x.mean(axis=1), axis=0),
    "winsorize": _winsorize,
    # element-wise
    "abs": lambda x: abs(x),
    "log": lambda x: np.log(x.where(x > 0)),
    "sign": lambda x: np.sign(x),
    "sqrt": lambda x: np.sqrt(x.where(x >= 0)),
    "signed_power": lambda x, e: np.sign(x) * abs(x) ** e,
    "max": lambda x, y: np.maximum(x, y),
    "min": lambda x, y: np.minimum(x, y),
    "inverse": lambda x: 1 / x,
    "reverse": lambda x: -x,
    # time series
    "ts_delay": lambda x, d: x.shift(_d(d)),
    "ts_delta": lambda x, d: x - x.shift(_d(d)),
    "ts_mean": lambda x, d: x.rolling(_d(d), min_periods=1).mean(),
    "ts_sum": lambda x, d: x.rolling(_d(d), min_periods=1).sum(),
    "ts_std_dev": lambda x, d: x.rolling(_d(d), min_periods=2).std(),
    "ts_min": lambda x, d: x.rolling(_d(d), min_periods=1).min(),
    "ts_max": lambda x, d: x.rolling(_d(d), min_periods=1).max(),
    "ts_rank": lambda x, d: _ts_rank(x, _d(d)),
    "ts_zscore": lambda x, d: (
        (x - x.rolling(_d(d), min_periods=2).mean()) / x.rolling(_d(d), min_periods=2).std()
    ),
    "ts_av_diff": lambda x, d: x - x.rolling(_d(d), min_periods=1).mean(),
    "ts_corr": lambda x, y, d: x.rolling(_d(d), min_periods=3).corr(y),
    "ts_decay_linear": lambda x, d: decay_linear(x, _d(d)),
    "ts_backfill": lambda x, d: x.ffill(limit=_d(d)),
    # conditional
    "trade_when": _trade_when,
    "if_else": _if_else,
}

GROUP_OPERATORS = {
    "group_neutralize": "demean",
    "group_rank": "rank",
    "group_zscore": "zscore",
    "group_mean": "mean",
}

#: Every operator the local engine can evaluate. Templates using others are skipped on the Gym.
LOCAL_OPERATORS = frozenset(OPERATORS) | frozenset(GROUP_OPERATORS)

_BINOPS: dict[str, Callable[[Value, Value], Value]] = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a / b,
    "^": lambda a, b: a**b,
    "<": lambda a, b: (a < b) * 1.0,
    "<=": lambda a, b: (a <= b) * 1.0,
    ">": lambda a, b: (a > b) * 1.0,
    ">=": lambda a, b: (a >= b) * 1.0,
    "==": lambda a, b: (a == b) * 1.0,
    "!=": lambda a, b: (a != b) * 1.0,
    "&&": lambda a, b: ((a > 0) & (b > 0)) * 1.0,
    "||": lambda a, b: ((a > 0) | (b > 0)) * 1.0,
}


class Engine:
    def __init__(self, fields: dict[str, Frame], groups: dict[str, pd.Series]) -> None:
        self.fields = fields
        self.groups = groups

    def __call__(self, expression: str) -> Frame:
        prog = parse(expression)
        env: dict[str, Value] = {}
        for name, node in prog.assignments:
            env[name] = self._eval(node, env)
        out = self._eval(prog.result, env)
        if not isinstance(out, pd.DataFrame):
            raise EvalError("The Alpha must vary across stocks; it evaluated to a constant.")
        return out.replace([np.inf, -np.inf], np.nan)

    def _eval(self, n: Node, env: dict[str, Value]) -> Value:
        match n.kind:
            case "num":
                return float(n.value)
            case "str":
                return n.value.strip("'\"")
            case "name":
                return self._name(n.value, env)
            case "unary":
                v = self._eval(n.children[0], env)
                return -v if n.value == "-" else (1.0 - (v > 0)) * 1.0
            case "binop":
                a, b = (self._eval(c, env) for c in n.children)
                return _BINOPS[n.value](a, b)
            case "ternary":
                c, a, b = (self._eval(x, env) for x in n.children)
                return _if_else(c, a, b)
            case "call":
                return self._call(n, env)
            case _:
                raise EvalError(f"Can't evaluate {n.kind}")

    def _name(self, name: str, env: dict[str, Value]) -> Value:
        if name in env:
            return env[name]
        if name in self.fields:
            return self.fields[name]
        if name in self.groups:
            raise EvalError(f"{name} is a GROUP field: use it inside a group_ operator.")
        raise EvalError(f"Unknown field {name!r}. Available: {sorted(self.fields)[:12]}…")

    def _call(self, n: Node, env: dict[str, Value]) -> Value:
        name = n.value
        if name in GROUP_OPERATORS:
            if len(n.children) != 2 or n.children[1].kind != "name":
                raise EvalError(f"{name}(x, group)")
            g = n.children[1].value
            if g not in self.groups:
                raise EvalError(f"Unknown group {g!r}. Available: {sorted(self.groups)}")
            return group_apply(
                self._eval(n.children[0], env), self.groups[g], GROUP_OPERATORS[name]
            )
        if name not in OPERATORS:
            raise EvalError(
                f"Operator {name!r} isn't in the local engine (it may exist on BRAIN). "
                f"Local: {sorted(OPERATORS)}"
            )
        args = [self._eval(c, env) for c in n.children]
        kwargs = {k: _num(v) for k, v in n.kwargs.items()}
        try:
            return OPERATORS[name](*args, **kwargs)
        except TypeError as exc:
            raise EvalError(f"{name}: wrong arguments ({exc})") from exc
