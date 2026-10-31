"""The Hypothesis Court against the Gym's answer key.

Ported from alpha-copilot ``tests/test_court.py``. The critic's JSON-parsing test stays behind
with the LLM critic, which this phase does not port; the Court takes any plain callable.
"""

from typing import Any

import pytest

from alpha_harness.forge.alpha_types import load
from alpha_harness.forge.local.client import GymSimulator
from alpha_harness.forge.local.engine import LOCAL_OPERATORS
from alpha_harness.forge.research.court import Court, Critic, TestResult
from alpha_harness.forge.store import InMemoryResearchStore

type Env = tuple[InMemoryResearchStore, GymSimulator]


@pytest.fixture(scope="module")
def env() -> Env:
    # One store for the module: later trials reuse earlier ones' cached simulations.
    return InMemoryResearchStore(), GymSimulator()


def court(
    env: Env,
    fields: list[str],
    sign: str = "+",
    horizon: str = "weeks",
    critic: Critic | None = None,
    budget: int = 15,
) -> Court:
    store, gym = env
    mk = gym.market.fields
    hypothesis: dict[str, Any] = {
        "id": 1,
        "title": "t",
        "mechanism": "a real mechanism here",
        "template": "-",
        "expected_sign": sign,
        "horizon": horizon,
    }
    return Court(
        store,
        gym,
        load("regular"),
        hypothesis,
        fields,
        test_budget=budget,
        local_data={f: mk[f] for f in fields} | {"cap": mk["cap"]},
        available=set(mk),
        operators=set(LOCAL_OPERATORS),
        scope="gym",
        critic=critic,
    )


async def test_verdicts_match_planted_truth(env: Env) -> None:
    assert (await court(env, ["anl_revision"]).run()).verdicts["anl_revision"] == "SURVIVES"
    value = await court(env, ["book_to_price"], horizon="months").run()
    assert value.verdicts["book_to_price"] == "REJECTED"  # small caps only
    case = await court(env, ["news_sentiment"]).run()
    assert case.verdicts["news_sentiment"] == "WOUNDED"  # real, but not a weeks effect
    assert any(r.kind == "horizon" and r.passed is False for r in case.results)


async def test_wrong_direction_is_fatal_and_cheap(env: Env) -> None:
    case = await court(env, ["anl_revision"], sign="-").run()
    assert case.verdict == "REJECTED"
    d = next(r for r in case.results if r.kind == "direction")
    assert d.passed is False
    assert "OPPOSITE" in d.detail
    assert all(r.passed is None for r in case.results if r.kind != "direction")


async def test_known_factor_is_caught(env: Env) -> None:
    case = await court(env, ["returns"], sign="-", horizon="days").run()
    kf = next(r for r in case.results if r.kind == "known_factor")
    assert kf.passed is False
    assert "reversal" in kf.detail


async def test_critic_contrast_objection_runs(env: Env) -> None:
    def critic(_h: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "critique": "Maybe it's just size.",
            "refinement": "x",
            "objections": [
                {"kind": "contrast", "claim": "It's size.", "params": {"expression": "-rank(cap)"}},
                {"kind": "nonsense", "claim": "ignored"},
            ],
        }

    case = await court(env, ["anl_revision"], critic=critic).run()
    contrast = [r for r in case.results if r.kind == "contrast"]
    assert len(contrast) == 1
    assert contrast[0].passed is True
    assert case.critique == "Maybe it's just size."


async def test_a_failing_critic_does_not_stop_the_court(env: Env) -> None:
    def broken(_h: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("model offline")

    case = await court(env, ["anl_revision"], critic=broken).run()
    assert "critic unavailable" in case.critique
    assert case.verdicts["anl_revision"] == "SURVIVES"


def test_judge_rules(env: Env) -> None:
    c = court(env, ["alt_5"], budget=2)

    def r(kind: str, passed: bool | None, severity: int) -> TestResult:
        return TestResult(kind, "f", passed, severity, None, "")

    assert c.judge([r("direction", True, 3), r("stability", False, 3)]) == "REJECTED"
    assert c.judge([r("direction", True, 3), r("horizon", False, 2)]) == "WOUNDED"
    assert c.judge([r("direction", True, 3), r("decay", None, 1)]) == "SURVIVES"
    assert c.judge([r("direction", None, 3)]) == "REJECTED"  # untested never passes


async def test_promotion_builds_alphas(env: Env) -> None:
    c = court(env, ["anl_revision"])
    case = await c.run()
    rep = await c.promote(case, 20)
    assert rep is not None
    assert rep.families
    assert all("anl_revision" in f["expression"] for f in rep.families)
    assert case.promoted is not None
