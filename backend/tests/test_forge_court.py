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
from alpha_harness.forge.research.templates import FieldInfo
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
    template_id: str = "",
    field_infos: list[FieldInfo] | None = None,
) -> Court:
    store, gym = env
    mk = gym.market.fields
    hypothesis: dict[str, Any] = {
        "id": 1,
        "title": "t",
        "mechanism": "a real mechanism here",
        "template": "-",
        "template_id": template_id,
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
        field_infos=field_infos,
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


# -- joint template seed: additive to the per-field seeds above, never a replacement -----------
# Same incident as the pipelines/templates-engine regression: a stray same-dataset field that is
# not actually the paired statistic must not bind, even though "same dataset" alone would allow
# it. ``analyst_dispersion`` (config/forge/templates/core_fundamental.yaml) hints X as the
# std/dispersion field and Y as the mean field, same_dataset_as X.
#
# These fields don't exist in the Gym's market, so they're built directly rather than through
# the ``court()`` fixture above (which indexes the Gym's own fields for ``local_data``) —
# ``joint_seed()`` is pure field-slot binding, it never simulates.


def _bare_court(
    env: Env,
    fields: list[str],
    *,
    template_id: str = "",
    field_infos: list[FieldInfo] | None = None,
) -> Court:
    store, gym = env
    hypothesis: dict[str, Any] = {
        "title": "t",
        "mechanism": "a real mechanism here",
        "template": "-",
        "template_id": template_id,
        "expected_sign": "+",
        "horizon": "weeks",
    }
    return Court(store, gym, load("regular"), hypothesis, fields, field_infos=field_infos)


def test_joint_seed_is_none_without_a_real_template_id(env: Env) -> None:
    c = _bare_court(env, ["anl4_eps_mean", "anl4_eps_std"])  # hypothesis["template_id"] == ""
    assert c.joint_seed() is None


def test_joint_seed_is_none_with_fewer_than_two_fields(env: Env) -> None:
    c = _bare_court(
        env,
        ["anl4_eps_mean"],
        template_id="analyst_dispersion",
        field_infos=[FieldInfo(id="anl4_eps_mean", dataset="analyst4")],
    )
    assert c.joint_seed() is None


def test_joint_seed_only_pairs_the_true_mean_std_fields(env: Env) -> None:
    infos = [
        FieldInfo(id="anl4_eps_mean", dataset="analyst4"),
        FieldInfo(id="anl4_eps_std", dataset="analyst4"),
        # Same dataset, but not the std field the X slot's hint asks for.
        FieldInfo(id="anl4_afv4_eps_high", dataset="analyst4"),
    ]
    correct = _bare_court(
        env,
        ["anl4_eps_mean", "anl4_eps_std"],
        template_id="analyst_dispersion",
        field_infos=infos,
    )
    seed = correct.joint_seed()
    assert seed is not None
    assert "anl4_eps_std" in seed
    assert "anl4_eps_mean" in seed

    decoy = _bare_court(
        env,
        ["anl4_eps_std", "anl4_afv4_eps_high"],
        template_id="analyst_dispersion",
        field_infos=infos,
    )
    assert decoy.joint_seed() is None


async def test_joint_seed_runs_additively_alongside_unchanged_per_field_seeds(env: Env) -> None:
    """The joint template seed is extra evidence, not a replacement: the per-field seeds and
    verdicts a plain trial already produces must come out exactly the same."""
    baseline = await court(env, ["anl_revision", "book_to_price"]).run()

    infos = [
        FieldInfo(id="anl_revision", dataset="gymds"),
        FieldInfo(id="book_to_price", dataset="gymds"),
    ]
    with_template = court(
        env,
        ["anl_revision", "book_to_price"],
        template_id="fund_ratio_two_fields",  # same_dataset_as only, no semantic hint to fail
        field_infos=infos,
    )
    assert with_template.joint_seed() is not None
    case = await with_template.run()

    assert case.seeds["anl_revision"] == baseline.seeds["anl_revision"]
    assert case.seeds["book_to_price"] == baseline.seeds["book_to_price"]
    assert case.verdicts["anl_revision"] == baseline.verdicts["anl_revision"]
    assert case.verdicts["book_to_price"] == baseline.verdicts["book_to_price"]
    assert "_joint" in case.seeds
    assert "_joint" in case.verdicts
