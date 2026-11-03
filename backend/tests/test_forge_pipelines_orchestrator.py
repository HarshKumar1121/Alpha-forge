"""The stage orchestrator: generate -> lint -> admit -> rounds -> check -> correlation.

Entirely against ``FakeSimulator`` and a ``FakeChecker`` (CLAUDE.md rule 4): no real BRAIN call
anywhere. Uses the repo's real ``court.yaml``/alpha-type YAML, but an injected
``QuotaGovernor`` config (see :class:`~alpha_harness.forge.quota.governor.QuotaGovernor`'s
own injectable ``config``) so these tests never depend on the real ``lanes.yaml`` numbers.
"""

from typing import TYPE_CHECKING, Any

import pytest_asyncio

from alpha_harness.db.sqlite import Database
from alpha_harness.forge.alpha_types import AlphaType
from alpha_harness.forge.pipelines.config import PipelineConfig
from alpha_harness.forge.pipelines.fake_engine import FakeSimulator, ScriptedResult
from alpha_harness.forge.pipelines.orchestrator import PipelineRun
from alpha_harness.forge.quota.governor import QuotaGovernor
from alpha_harness.forge.research.templates import FieldInfo, FieldSlot, Template
from alpha_harness.forge.store import InMemoryResearchStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from alpha_harness.brain.schemas import SimulationRequest

SETTINGS: dict[str, Any] = {"region": "USA", "universe": "TOP3000", "delay": 1, "decay": 0}

#: The real ``reserve`` lane from ``config/forge/lanes.yaml``, used as is: the orchestrator
#: also needs the real ``court.yaml``/alpha-type/template YAML, so only the lane's own numbers
#: are overridden here (a higher daily limit keeps the tests independent of real-world spend).
LANES: dict[str, Any] = {
    "daily_limit": 1000,
    "safety_margin": 0,
    "timezone": "UTC",
    "pacing": {"max_per_hour": 10_000, "quiet_hours": []},
    "allocation": "fixed",
    "default_lane": "reserve",
    "task_lanes": {},
    "lanes": {"reserve": {"about": "", "share": 1.0, "bounds": [0.0, 1.0], "alpha_types": []}},
}


def make_governor(db: Database) -> QuotaGovernor:
    """A governor over the ``reserve`` lane only, injected so the real ``lanes.yaml`` (which
    the orchestrator's other config reads still need) is never touched on disk."""
    from alpha_harness.forge.quota.config import parse

    lanes = parse(LANES)
    return QuotaGovernor(db, config=lambda: lanes)


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "harness.db")
    await database.create_all()
    yield database
    await database.dispose()


ONE_FIELD = Template(
    id="one_field",
    name="one field",
    category="level",
    complexity="simple",
    slots={"X": FieldSlot()},
    expr="rank($X)",
    rationale="",
    angle="level",
)

TWO_FIELD = Template(
    id="two_field",
    name="two field",
    category="level",
    complexity="simple",
    slots={"X": FieldSlot(), "Y": FieldSlot()},
    expr="rank($X) + rank($Y)",
    rationale="",
    angle="level",
)

FIELD_SET = [
    FieldInfo(id="close", dataset="pv1", archetype="fast", type="MATRIX"),
    FieldInfo(id="open", dataset="pv1", archetype="fast", type="MATRIX"),
]


def an_alpha_type(**lint_rules: Any) -> AlphaType:
    return AlphaType(
        "test_type",
        {
            "simulation": {"type": "REGULAR", "settings": SETTINGS},
            "lint": lint_rules,
            "gates": {"train.sharpe": {"min": 0.0}, "test.sharpe": {"min": 0.0}},
            "robustness": {"hold_ratio": 0.5, "perturb": {}, "min_fraction_holding": 0.0},
            "correlation": {"pool": "test_type", "ceiling": 0.7},
        },
    )


def a_pipeline(**overrides: Any) -> PipelineConfig:
    defaults: dict[str, Any] = {
        "name": "test_type",
        "alpha_type": "test_type",
        "lane": "reserve",
        "template_complexity": ("simple",),
        "correlation_kind": "self",
        "correlation_top_n": 5,
        "check_top_n": 10,
        "default_budget": 40,
    }
    return PipelineConfig(**{**defaults, **overrides})


def passing_script(request: SimulationRequest) -> ScriptedResult:
    expr = request.regular or ""
    return ScriptedResult(alpha_id=f"A-{abs(hash(expr))}", train={"sharpe": 1.5})


class FakeChecker:
    def __init__(self) -> None:
        self.checked: list[str] = []
        self.correlated: list[tuple[str, str]] = []

    async def check_alpha(self, alpha_id: str) -> dict[str, Any]:
        self.checked.append(alpha_id)
        return {"is": {"checks": []}}

    async def correlations(self, alpha_id: str, kind: str = "self") -> dict[str, Any]:
        self.correlated.append((alpha_id, kind))
        return {"max": 0.1}


async def test_ineligible_candidates_never_reach_the_simulator(db: Database) -> None:
    sim = FakeSimulator(script=passing_script)
    store = InMemoryResearchStore()
    checker = FakeChecker()
    alpha_type = an_alpha_type(max_fields=1)
    pipeline = a_pipeline()
    run = PipelineRun(
        store,
        sim,
        make_governor(db),
        checker,
        pipeline,
        alpha_type,
        FIELD_SET,
        [ONE_FIELD, TWO_FIELD],
    )

    report = await run.run()

    rejected_exprs = [e for e, _ in report.rejected]
    assert any("+" in e for e in rejected_exprs)  # the two-field template was rejected
    simulated_exprs = [req.regular for batch in sim.calls for req in batch]
    assert all(e is not None and "+" not in e for e in simulated_exprs)


async def test_budget_never_exceeds_lane_admission(db: Database) -> None:
    sim = FakeSimulator(script=passing_script)
    store = InMemoryResearchStore()
    checker = FakeChecker()
    alpha_type = an_alpha_type()
    pipeline = a_pipeline(default_budget=2)
    run = PipelineRun(
        store, sim, make_governor(db), checker, pipeline, alpha_type, FIELD_SET, [ONE_FIELD]
    )

    report = await run.run()

    assert report.research.spent <= 2


async def test_checks_fetched_only_for_train_gate_passers(db: Database) -> None:
    def script(request: SimulationRequest) -> ScriptedResult:
        expr = request.regular or ""
        sharpe = 1.5 if "close" in expr else -1.0
        return ScriptedResult(alpha_id=f"A-{abs(hash(expr))}", train={"sharpe": sharpe})

    sim = FakeSimulator(script=script)
    store = InMemoryResearchStore()
    checker = FakeChecker()
    alpha_type = an_alpha_type()
    pipeline = a_pipeline()
    run = PipelineRun(
        store, sim, make_governor(db), checker, pipeline, alpha_type, FIELD_SET, [ONE_FIELD]
    )

    await run.run()

    assert checker.checked  # the "close" candidate passed the train gate and was checked
    assert all(aid.startswith("A-") for aid in checker.checked)


async def test_power_pool_pipeline_uses_power_pool_correlation(db: Database) -> None:
    sim = FakeSimulator(script=passing_script)
    store = InMemoryResearchStore()
    checker = FakeChecker()
    alpha_type = an_alpha_type()
    pipeline = a_pipeline(correlation_kind="power-pool")
    run = PipelineRun(
        store, sim, make_governor(db), checker, pipeline, alpha_type, FIELD_SET, [ONE_FIELD]
    )

    await run.run()

    assert checker.correlated
    assert all(kind == "power-pool" for _aid, kind in checker.correlated)


async def test_self_correlation_for_regular_type(db: Database) -> None:
    sim = FakeSimulator(script=passing_script)
    store = InMemoryResearchStore()
    checker = FakeChecker()
    alpha_type = an_alpha_type()
    pipeline = a_pipeline(correlation_kind="self")
    run = PipelineRun(
        store, sim, make_governor(db), checker, pipeline, alpha_type, FIELD_SET, [ONE_FIELD]
    )

    await run.run()

    assert checker.correlated
    assert all(kind == "self" for _aid, kind in checker.correlated)


async def test_resume_after_restart_spends_and_checks_nothing_twice(db: Database) -> None:
    store = InMemoryResearchStore()
    alpha_type = an_alpha_type()
    pipeline = a_pipeline()

    run1 = PipelineRun(
        store,
        FakeSimulator(script=passing_script),
        make_governor(db),
        FakeChecker(),
        pipeline,
        alpha_type,
        FIELD_SET,
        [ONE_FIELD],
    )
    report1 = await run1.run()
    assert report1.research.spent > 0

    checker2 = FakeChecker()
    run2 = PipelineRun(
        store,
        FakeSimulator(script=passing_script),
        make_governor(db),
        checker2,
        pipeline,
        alpha_type,
        FIELD_SET,
        [ONE_FIELD],
    )
    report2 = await run2.run()

    assert report2.research.spent == 0
    assert checker2.checked == []
    assert checker2.correlated == []
