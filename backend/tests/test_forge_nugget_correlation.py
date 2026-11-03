"""Correlation in two stages, never merged: self-prescreen (local pool) and prod-final
(BRAIN's own verdict) are distinct values with distinct labels and captions, even when they
disagree (CLAUDE.md rule 6 spirit: never let a free local estimate pass as BRAIN's verdict).
"""

from typing import Any

import pandas as pd
import pytest

from alpha_harness.forge.nuggets.correlation import prod_final, self_prescreen


def _series(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2024-01-01", periods=len(values)))


def _long_series(sign: float = 1.0, n: int = 260) -> pd.Series:
    """260 days (over ``analytics.correlation``'s 250-day minimum overlap), alternating so a
    perfectly correlated and perfectly anti-correlated series are easy to construct."""
    values = [sign * (1.0 if i % 2 == 0 else -1.0) for i in range(n)]
    return pd.Series(values, index=pd.date_range("2024-01-01", periods=n))


class FakeEndpoints:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body
        self.calls: list[tuple[str, str]] = []

    async def correlations(self, alpha_id: str, kind: str) -> dict[str, Any]:
        self.calls.append((alpha_id, kind))
        return self.body


def test_self_prescreen_picks_the_most_correlated_pool_member() -> None:
    candidate = _long_series(1.0)
    pool = {"a": _long_series(1.0), "b": _long_series(-1.0)}
    stage = self_prescreen(candidate, pool)
    assert stage.label == "self_prescreen"
    assert stage.against == "a"
    assert stage.value == pytest.approx(1.0)
    assert "not BRAIN" in stage.caption


def test_self_prescreen_is_none_on_an_empty_pool() -> None:
    stage = self_prescreen(_series([1.0, 2.0, 3.0]), {})
    assert stage.value is None
    assert stage.against is None


async def test_prod_final_reads_brains_own_verdict() -> None:
    endpoints = FakeEndpoints({"records": [{"correlation": 0.42}, {"correlation": -0.9}]})
    stage = await prod_final(endpoints, "ALPHA1", kind="self")
    assert stage.label == "prod_final"
    assert stage.value == pytest.approx(0.9)
    assert endpoints.calls == [("ALPHA1", "self")]
    assert "Authoritative" in stage.caption


async def test_self_prescreen_and_prod_final_are_never_merged() -> None:
    """The two stages can legitimately disagree — that disagreement must stay visible, not
    be averaged away into one number."""
    candidate = _long_series(1.0)
    pool = {"a": _long_series(1.0)}
    self_stage = self_prescreen(candidate, pool)
    prod_stage = await prod_final(FakeEndpoints({"records": [{"correlation": 0.1}]}), "A1")

    assert self_stage.label != prod_stage.label
    assert self_stage.value != prod_stage.value
    assert self_stage.caption != prod_stage.caption
    # Nothing merges them: each is read and reported on its own.
    assert {"self_prescreen", "prod_final"} == {self_stage.label, prod_stage.label}
