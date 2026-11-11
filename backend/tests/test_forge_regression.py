"""Multi-Field Regression Lab: pure OLS/VIF/stability math (``regression.py``), field-series
caching (``regression_data.py``) against a fake simulator, and the two routes
(``forge_regression.py``) against a real ``BatchEngine``/``QuotaGovernor`` with a scripted,
no-network tracker — CLAUDE.md rule 4: no real BRAIN quota is ever spent here.
"""

import asyncio
import math
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx2
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from alpha_harness.api import forge_regression
from alpha_harness.db.models import SimStatus, SimulationRecord, utcnow
from alpha_harness.db.sqlite import Database
from alpha_harness.engine.lifecycle import record_launch, transition
from alpha_harness.engine.slots import BatchEngine
from alpha_harness.forge.alpha_types import AlphaType
from alpha_harness.forge.pipelines.brain_adapter import BrainSimulator
from alpha_harness.forge.pipelines.fake_engine import FakeSimulator, ScriptedResult
from alpha_harness.forge.quota.governor import QuotaGovernor
from alpha_harness.forge.research import regression as reg
from alpha_harness.forge.research import regression_data as rdata
from alpha_harness.forge.store import InMemoryResearchStore
from alpha_harness.main import create_app
from alpha_harness.tasks import TaskRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from alpha_harness.brain.schemas import SimulationRequest

CLIENT = {"x-harness-client": "1"}
SETTINGS = {"instrumentType": "EQUITY", "region": "USA", "delay": 1, "universe": "TOP3000"}
USA = {"region": "USA", "delay": 1, "universe": "TOP3000"}


def _series(seed: int, n: int = 400, mu: float = 0.0, sd: float = 0.01) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mu, sd, n), index=pd.bdate_range("2018-01-01", periods=n))


# ============================================================================================
# Pure math: regression.py
# ============================================================================================


def test_vif_flags_a_redundant_pair() -> None:
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2018-01-01", periods=400)
    field_a = pd.Series(rng.normal(0, 0.01, 400), index=idx)
    field_b = field_a + pd.Series(rng.normal(0, 0.0005, 400), index=idx)  # ~corr 0.99 with a
    field_c = pd.Series(rng.normal(0, 0.01, 400), index=idx)  # independent

    vifs = reg.vif(pd.DataFrame({"a": field_a, "b": field_b, "c": field_c}))
    assert vifs["a"] > 5
    assert vifs["b"] > 5
    assert vifs["c"] < 2

    target = field_a + field_c + pd.Series(rng.normal(0, 0.01, 400), index=idx)
    result = reg.ols_fit(target, {"a": field_a, "b": field_b, "c": field_c})
    by_id = {f.field_id: f for f in result.fields}
    assert by_id["a"].vif_flag is True
    assert by_id["b"].vif_flag is True
    assert by_id["c"].vif_flag is False


def test_pure_noise_field_gets_a_high_p_value() -> None:
    informative = _series(1)
    noise = _series(100)
    rng = np.random.default_rng(3)
    target = informative * 0.5 + pd.Series(
        rng.normal(0, 0.01, len(informative)), index=informative.index
    )
    result = reg.ols_fit(target, {"informative": informative, "noise_field": noise})
    by_id = {f.field_id: f for f in result.fields}
    assert by_id["noise_field"].p_value > 0.3
    assert by_id["informative"].p_value < 0.01


def test_half_only_significant_field_caught_by_stability_check() -> None:
    n = 400
    idx = pd.bdate_range("2018-01-01", periods=n)
    rng = np.random.default_rng(0)
    field_x = pd.Series(rng.normal(0, 0.01, n), index=idx)
    noise = pd.Series(rng.normal(0, 0.01, n), index=idx)
    mid = n // 2
    # Strong positive effect in the first half, exactly zero (independent) in the second.
    effect = pd.Series(0.0, index=idx)
    effect.iloc[:mid] = field_x.iloc[:mid] * 3.0
    target = effect + noise

    stability = reg.split_half_stability(target, {"field_x": field_x})
    row = next(r for r in stability if r.field_id == "field_x")
    assert row.significant_half1 is True
    assert row.significant_half2 is False
    assert row.stable is False

    result = reg.ols_fit(target, {"field_x": field_x})
    vif_flags = {f.field_id: f.vif_flag for f in result.fields}
    summary = reg.plain_summary(result, vif_flags)
    assert "field_x" in summary
    keep, drop, reason = reg.recommendation(result, vif_flags)
    assert "field_x" in drop
    assert "field_x" not in keep
    assert "field_x" in reason


def test_adjusted_r_squared_penalizes_a_useless_added_field() -> None:
    good = _series(5)
    useless = _series(6)
    rng = np.random.default_rng(7)
    target = good * 0.6 + pd.Series(rng.normal(0, 0.01, len(good)), index=good.index)

    without = reg.ols_fit(target, {"good": good})
    with_extra = reg.ols_fit(target, {"good": good, "useless": useless})

    # OLS can only raise (or hold) raw R^2 by adding a field; it should only move by a sliver.
    assert with_extra.r_squared >= without.r_squared - 1e-12
    assert with_extra.r_squared - without.r_squared < 0.05
    # Adjusted R^2 pays for the extra field and should come out lower.
    assert with_extra.adj_r_squared < without.adj_r_squared

    lower = reg.adjusted_r_squared(r2=0.42, n=1000, k=6)
    higher_k_marginal_r2 = reg.adjusted_r_squared(r2=0.4201, n=1000, k=7)
    assert higher_k_marginal_r2 < lower


# --- a few more direct units, for solid coverage ------------------------------------------------


def test_adjusted_r_squared_matches_closed_form() -> None:
    assert reg.adjusted_r_squared(0.5, 102, 1) == pytest.approx(1 - 0.5 * 101 / 100)


def test_ols_fit_drops_field_short_of_min_overlap() -> None:
    target = _series(30)
    short = pd.Series([1.0, 2.0], index=pd.bdate_range("2018-01-01", periods=2))
    good = _series(31)
    result = reg.ols_fit(target, {"short": short, "good": good})
    assert "short" in result.dropped
    assert any(f.field_id == "good" for f in result.fields)


def test_ols_fit_with_no_usable_fields_returns_empty_result() -> None:
    result = reg.ols_fit(_series(40), {})
    assert result.fields == []
    assert math.isnan(result.r_squared)
    assert result.n_obs == 0


def test_correlation_matrix_is_symmetric_with_unit_diagonal() -> None:
    a, b = _series(50), _series(51)
    corr = reg.correlation_matrix({"a": a, "b": b})
    assert corr["a"]["a"] == pytest.approx(1.0)
    assert corr["a"]["b"] == pytest.approx(corr["b"]["a"])


def test_t_sf_matches_known_two_sided_p_values() -> None:
    # t=1.96 at large dof should land close to the normal-approx 0.05 two-sided threshold.
    assert reg._t_sf(1.96, 1_000_000) == pytest.approx(0.05, abs=0.005)
    assert reg._t_sf(0.0, 100) == pytest.approx(1.0, abs=1e-9)


def test_realistic_five_field_mix_identifies_winner_redundancy_and_noise() -> None:
    """2 independent signals, 1 redundant pair, 1 pure noise field — the case the Regression
    Lab actually has to make legible, not the trivial "one field against itself" case."""
    rng = np.random.default_rng(42)
    n = 400
    idx = pd.bdate_range("2018-01-01", periods=n)
    strong = pd.Series(rng.normal(0, 0.01, n), index=idx)
    weaker = pd.Series(rng.normal(0, 0.01, n), index=idx)
    redundant_a = pd.Series(rng.normal(0, 0.01, n), index=idx)
    redundant_b = redundant_a + pd.Series(rng.normal(0, 0.0005, n), index=idx)  # ~corr 0.99
    noise = pd.Series(rng.normal(0, 0.01, n), index=idx)
    target = (
        strong * 1.0
        + weaker * 0.3
        + redundant_a * 0.5
        + pd.Series(rng.normal(0, 0.01, n), index=idx)
    )
    fields = {
        "strong": strong,
        "weaker": weaker,
        "redundant_a": redundant_a,
        "redundant_b": redundant_b,
        "noise": noise,
    }

    result = reg.ols_fit(target, fields)
    vif_flags = {f.field_id: f.vif_flag for f in result.fields}

    assert vif_flags["redundant_a"] is True
    assert vif_flags["redundant_b"] is True
    assert vif_flags["strong"] is False
    assert vif_flags["noise"] is False  # noise isn't collinear, just uninformative

    winner = reg.strongest_field(result)
    assert winner is not None
    assert winner.field_id == "strong"
    assert "strong is your strongest independent signal" in reg.winner_message(winner)

    groups = reg.redundancy_groups(result.corr_matrix)
    assert ["redundant_a", "redundant_b"] in groups
    assert all({"strong", "weaker", "noise"}.isdisjoint(g) for g in groups)

    summary = reg.plain_summary(result, vif_flags)
    assert "redundant" in summary.lower()


def test_strongest_field_is_none_when_nothing_is_both_significant_and_stable() -> None:
    result = reg.ols_fit(_series(70), {"noise": _series(71)})
    winner = reg.strongest_field(result)
    assert winner is None
    assert "No field here" in reg.winner_message(winner)


def test_redundancy_groups_ignores_fields_with_no_strong_partner() -> None:
    matrix = {
        "a": {"a": 1.0, "b": 0.95, "c": 0.1},
        "b": {"a": 0.95, "b": 1.0, "c": 0.05},
        "c": {"a": 0.1, "b": 0.05, "c": 1.0},
    }
    groups = reg.redundancy_groups(matrix)
    assert groups == [["a", "b"]]


def test_plain_summary_suggests_a_transformation_when_fit_is_very_weak() -> None:
    target = _series(80)
    weak = _series(81)
    result = reg.ols_fit(target, {"weak": weak})
    vif_flags = {f.field_id: f.vif_flag for f in result.fields}

    generic = reg.plain_summary(result, vif_flags)
    assert "transformation" in generic or "different dataset category" in generic

    with_cadence = reg.plain_summary(result, vif_flags, cadence_by_field={"weak": "quarterly"})
    assert "update-event flag" in with_cadence


def test_recommendation_keeps_everything_when_nothing_fails() -> None:
    good1, good2 = _series(60), _series(61)
    rng = np.random.default_rng(62)
    target = (
        good1 * 0.6 + good2 * 0.4 + pd.Series(rng.normal(0, 0.005, len(good1)), index=good1.index)
    )
    result = reg.ols_fit(target, {"good1": good1, "good2": good2})
    vif_flags = {f.field_id: f.vif_flag for f in result.fields}
    keep, drop, reason = reg.recommendation(result, vif_flags)
    assert drop == []
    assert set(keep) == {"good1", "good2"}
    assert "Keep all" in reason


# ============================================================================================
# regression_data.py: field series through a fake Simulator, cached per scope
# ============================================================================================


async def test_field_series_simulates_once_then_reuses_the_cache() -> None:
    from datetime import date, timedelta

    field_ids = ["fnd6_x", "mdl_y"]
    expr_to_alpha_id = {f"rank({fid})": f"SIM-{fid}" for fid in field_ids}
    pnl = {
        aid: [(date(2020, 1, 1) + timedelta(days=i), float(i % 5)) for i in range(150)]
        for aid in expr_to_alpha_id.values()
    }

    def script(request: SimulationRequest) -> ScriptedResult:
        return ScriptedResult(alpha_id=expr_to_alpha_id[request.regular], train={"sharpe": 1.0})

    sim = FakeSimulator(script=script, pnl_by_alpha=pnl)
    alpha_type = AlphaType("test", {"simulation": {"type": "REGULAR", "settings": SETTINGS}})
    store = InMemoryResearchStore()

    series = await rdata.field_series(sim, alpha_type, SETTINGS, field_ids, store=store)
    assert set(series) == set(field_ids)
    assert sim.spent == len(field_ids)

    series_again = await rdata.field_series(sim, alpha_type, SETTINGS, field_ids, store=store)
    assert set(series_again) == set(field_ids)
    assert sim.spent == len(field_ids)  # second call for the same scope spent nothing new


async def test_target_series_field_kind_matches_field_series() -> None:
    from datetime import date, timedelta

    def script(_request: SimulationRequest) -> ScriptedResult:
        return ScriptedResult(alpha_id="SIM-1", train={"sharpe": 1.0})

    pnl_rows = [(date(2020, 1, 1) + timedelta(days=i), 1.0) for i in range(150)]
    sim = FakeSimulator(script=script, pnl_by_alpha={"SIM-1": pnl_rows})
    alpha_type = AlphaType("test", {"simulation": {"type": "REGULAR", "settings": SETTINGS}})
    store = InMemoryResearchStore()

    target = await rdata.target_series(sim, alpha_type, SETTINGS, "field", "close", store=store)
    assert len(target) == 150


async def test_target_series_vault_kind_prefers_the_store() -> None:
    from datetime import date

    sim = FakeSimulator()
    alpha_type = AlphaType("test", {"simulation": {"type": "REGULAR", "settings": SETTINGS}})
    store = InMemoryResearchStore()
    await store.save_pnl("ALPHA-1", [(date(2021, 1, 1), 1.0), (date(2021, 1, 2), 2.0)])

    target = await rdata.target_series(sim, alpha_type, SETTINGS, "vault", "ALPHA-1", store=store)
    assert len(target) == 2
    assert sim.spent == 0  # never reached the simulator: the store already had it


# ============================================================================================
# Routes exposed by forge_regression.py
# ============================================================================================


class FakeQueries:
    """A canned ``fields_by_id`` with one eps-mean/eps-avg duplicate pair and one clean field."""

    async def fields_by_id(
        self,
        _scope: Any,
        field_ids: list[str],
        *,
        whole_datasets: bool = False,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        rows = {
            "eps_mean": {
                "field_id": "eps_mean",
                "dataset_id": "fnd6",
                "dataset_name": "Fundamentals",
                "description": "Mean of quarterly EPS",
                "field_type": "MATRIX",
                "coverage": 0.9,
                "date_coverage": 0.8,
                "category_name": "Fundamental",
                "subcategory_name": None,
            },
            "eps_avg": {
                "field_id": "eps_avg",
                "dataset_id": "fnd6",
                "dataset_name": "Fundamentals",
                "description": "Mean of quarterly EPS",
                "field_type": "MATRIX",
                "coverage": 0.9,
                "date_coverage": 0.8,
                "category_name": "Fundamental",
                "subcategory_name": None,
            },
            "clean_field": {
                "field_id": "clean_field",
                "dataset_id": "mdl1",
                "dataset_name": "Model Scores",
                "description": "An unrelated model score",
                "field_type": "MATRIX",
                "coverage": 0.95,
                "date_coverage": 0.9,
                "category_name": "model_score",
                "subcategory_name": None,
            },
        }
        return [rows[f] for f in field_ids if f in rows]


class ScriptedTracker:
    """Real lifecycle transitions, no network — see ``test_forge_probe.py``."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def submit(self, request: SimulationRequest, record_id: int) -> None:  # noqa: ARG002
        async with self.db.session() as session:
            await transition(
                session,
                record_id,
                from_=[SimStatus.QUEUED],
                status=SimStatus.PENDING,
                sent_at=utcnow(),
            )
        await asyncio.sleep(0)
        async with self.db.session() as session:
            await record_launch(session, record_id, f"PLATFORM{record_id}", None)
        async with self.db.session() as session:
            await transition(
                session,
                record_id,
                from_=[SimStatus.RUNNING],
                status=SimStatus.COMPLETE,
                alpha_id=f"ALPHA{record_id}",
                finished_at=utcnow(),
            )

    async def notify(self) -> None:
        return None


class StubRecordset:
    """Enough of ``brain.schemas``'s recordset shape for ``BrainSimulator.pnl`` to read:
    cumulative ``pnl`` rows, one per trading day."""

    def __init__(self, n: int = 200) -> None:
        self._n = n

    def rows(self) -> list[dict[str, Any]]:
        from datetime import date, timedelta

        cum = 0.0
        out = []
        for i in range(self._n):
            cum += 1.0 if i % 2 == 0 else -0.5
            out.append({"date": (date(2020, 1, 1) + timedelta(days=i)).isoformat(), "pnl": cum})
        return out


class StubEndpoints:
    async def alpha_body(self, alpha_id: str) -> dict[str, Any]:
        return {"id": alpha_id, "is": {"sharpe": 0.5}, "train": {"sharpe": 0.5}}

    async def get_recordset(self, alpha_id: str, kind: str) -> StubRecordset:  # noqa: ARG002
        return StubRecordset()


async def _no_session() -> bool:
    return False


async def _noop(_payload: dict[str, Any]) -> None:
    return None


class Harness(SimpleNamespace):
    db: Database
    engine: Any
    endpoints: StubEndpoints
    tasks: TaskRegistry
    queries: FakeQueries


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    db = Database(tmp_path / "harness.db")
    await db.create_all()

    tracker = ScriptedTracker(db)
    engine = BatchEngine(db, object(), tracker, on_unauthorized=_no_session)  # type: ignore[arg-type]
    engine.max_batch = 1
    engine.governor = QuotaGovernor(db)
    engine._last_orphan_sweep = float("inf")  # pyright: ignore[reportPrivateUsage]

    state = Harness(
        db=db,
        engine=engine,
        endpoints=StubEndpoints(),
        tasks=TaskRegistry(on_change=_noop),
        queries=FakeQueries(),
    )
    try:
        yield state
    finally:
        await db.dispose()


@pytest.fixture
async def client(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx2.AsyncClient]:
    def fast_simulator(engine: Any, db: Any, endpoints: Any, lane: str) -> BrainSimulator:
        return BrainSimulator(engine, db, endpoints, lane, poll_interval=0.01)

    monkeypatch.setattr(forge_regression, "BrainSimulator", fast_simulator)
    app = create_app()
    app.state.harness = harness
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


async def drain(engine: BatchEngine) -> None:
    while await engine._fill_slots():  # pyright: ignore[reportPrivateUsage]
        pass


async def _post_while_draining(
    client: httpx2.AsyncClient, harness: Harness, path: str, **kwargs: Any
) -> httpx2.Response:
    post = asyncio.ensure_future(client.post(path, headers=CLIENT, **kwargs))
    while not post.done():
        await drain(harness.engine)
        await asyncio.sleep(0)
    return await post


async def test_prescreen_flags_duplicate_pair(client: httpx2.AsyncClient) -> None:
    res = await client.post(
        "/api/forge/regression/prescreen",
        params=USA,
        json={"fieldIds": ["eps_mean", "eps_avg"]},
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["duplicatePairs"]) == 1
    pair = body["duplicatePairs"][0]
    assert {pair["fieldA"], pair["fieldB"]} == {"eps_mean", "eps_avg"}


async def test_prescreen_no_duplicates_for_unrelated_fields(client: httpx2.AsyncClient) -> None:
    res = await client.post(
        "/api/forge/regression/prescreen",
        params=USA,
        json={"fieldIds": ["eps_mean", "clean_field"]},
        headers=CLIENT,
    )
    assert res.status_code == 200, res.text
    assert res.json()["duplicatePairs"] == []


async def test_run_uses_explore_lane_and_caches(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    body = {
        "scope": {"region": "USA", "delay": 1, "universe": "TOP3000"},
        "fieldIds": ["eps_mean", "clean_field"],
        "target": {"kind": "gym", "ref": "rank(close)"},
    }
    res = await _post_while_draining(client, harness, "/api/forge/regression/run", json=body)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["lane"] == "explore"
    assert out["admitted"] == 2
    assert out["asked"] == 2
    assert len(out["fields"]) <= 2
    assert "winner" in out and "message" in out["winner"]
    assert isinstance(out["redundancyGroups"], list)

    async with harness.db.session() as session:
        tasks = (await session.scalars(select(SimulationRecord.task).distinct())).all()
    assert tasks == ["forge.explore"]

    # A second run for the same scope + fields must not enqueue any new simulation records.
    async with harness.db.session() as session:
        before = (await session.scalars(select(SimulationRecord.id))).all()

    res2 = await _post_while_draining(client, harness, "/api/forge/regression/run", json=body)
    assert res2.status_code == 200, res2.text

    async with harness.db.session() as session:
        after = (await session.scalars(select(SimulationRecord.id))).all()
    assert len(after) == len(before)


async def test_suggestions_lists_saved_nuggets_in_scope_grouped_by_category(
    client: httpx2.AsyncClient, harness: Harness
) -> None:
    from alpha_harness.forge.nuggets.models import Nugget

    async with harness.db.session() as session:
        session.add(
            Nugget(
                nugget_id="n1",
                name="Fundamentals pair",
                region="USA",
                delay=1,
                universe="TOP3000",
                field_ids=["eps_mean", "clean_field"],
            )
        )
        session.add(  # out of scope: must not show up
            Nugget(
                nugget_id="n2",
                name="Wrong region",
                region="GLB",
                delay=1,
                universe="TOP3000",
                field_ids=["eps_mean", "clean_field"],
            )
        )
        session.add(  # too few fields: must be skipped
            Nugget(
                nugget_id="n3",
                name="Too short",
                region="USA",
                delay=1,
                universe="TOP3000",
                field_ids=["eps_mean"],
            )
        )
        await session.commit()

    res = await client.get("/api/forge/regression/suggestions", params=USA, headers=CLIENT)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 1
    assert body[0]["label"] == "Fundamentals pair"
    assert set(body[0]["fieldIds"]) == {"eps_mean", "clean_field"}
    assert body[0]["source"] == "nugget"
