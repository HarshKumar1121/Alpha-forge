"""The Hypothesis Court: attack an idea with falsification tests before it becomes an alpha.

Real research desks don't ask "can I find a good backtest for this?" — with enough tries you
always can. They ask "what would PROVE this idea wrong?", run those tests first, and only build
alphas from ideas that survive. That is what this module does.

    1. Seed        turn the hypothesis into one plain signal per field, in the CLAIMED direction
                   (the data does not get to pick the sign — that would be fitting).
    2. Objections  standard ones always (below) + bespoke ones from a critic, each expressed as
                   a test the machine can run. An objection without a test is an opinion; it
                   is reported but cannot sink the idea.
    3. Tests       direction    does the data agree with the claimed sign?           (fatal)
                   stability    does it work in most years, not one lucky stretch?   (fatal)
                   sub_universe does it survive in liquid large caps?                (fatal)
                   known_factor is it just reversal / momentum / size / low-vol?     (serious)
                   horizon      does delaying it behave as the claimed horizon says? (serious)
                   neutralization does it survive industry neutralization?           (serious)
                   decay        does smoothing keep it? (slow claims only)          (minor)
                   contrast     critic-proposed "it's really just X" comparisons     (serious)
    4. Verdict     REJECTED (any fatal failure) / WOUNDED (refine it) / SURVIVES.
    5. Promotion   survivors go to the budgeted planner, which turns them into alphas.

All judgements use TRAIN years only. Tests are simulations, so they're cached and budgeted.
Thresholds, severities and the known-factor library live in ``config/forge/court.yaml``.

Ported from alpha-copilot ``research/court.py``; recast onto the async Protocols.
"""

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

import pandas as pd

from .. import analytics as an
from .. import config
from ..expr import ParseError, analyse, on_result
from .planner import Cand, Report, Researcher
from .profiler import probes
from .templates import flip

if TYPE_CHECKING:
    from ..alpha_types import AlphaType
    from ..simulator import Simulator
    from ..store import ResearchStore

TESTS = (
    "direction",
    "stability",
    "sub_universe",
    "known_factor",
    "horizon",
    "neutralization",
    "decay",
    "contrast",
)
VERDICTS = ("SURVIVES", "WOUNDED", "REJECTED")
#: Years at the end of the PnL that the Court never looks at: the held-out test period.
TEST_YEARS = 2

type Critic = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def court_config() -> dict[str, Any]:
    return config.court()["court"]


@dataclass(slots=True)
class Objection:
    kind: str  # one of TESTS
    claim: str  # the attack, in words
    params: dict[str, Any] = field(default_factory=dict[str, Any])
    source: str = "standard"


@dataclass(slots=True)
class TestResult:
    __test__ = False  # tell pytest this is not a test class

    kind: str
    field: str
    passed: bool | None  # None = could not run (budget, missing data) — never counts as a pass
    severity: int
    value: float | None
    detail: str
    claim: str = ""


@dataclass(slots=True)
class Case:
    hypothesis: dict[str, Any]
    seeds: dict[str, str]
    results: list[TestResult]
    verdicts: dict[str, str]  # per field
    verdict: str  # overall
    critique: str = ""
    refinement: str = ""
    spent: int = 0
    promoted: dict[str, Any] | None = None

    def lines(self) -> list[str]:
        h = self.hypothesis
        out = [
            f"HYPOTHESIS: {h.get('title')}  (claimed sign {h.get('expected_sign', '+')}, "
            f"horizon {h.get('horizon') or '?'})"
        ]
        if self.critique:
            out.append(f"CRITIC: {self.critique}")
        for f, seed in self.seeds.items():
            out.append(f"\n[{f}] seed: {seed}  →  {self.verdicts.get(f)}")
            for r in (r for r in self.results if r.field == f):
                mark = {True: "PASS", False: "FAIL", None: "SKIP"}[r.passed]
                out.append(f"   {mark:4s} {'!' * r.severity:3s} {r.kind:14s} {r.detail}")
                if r.claim and r.passed is False:
                    out.append(f"        objection: {r.claim}")
        per = " · ".join(f"{v} on {f}" for f, v in self.verdicts.items())
        out.append(f"\nVERDICT: {per}   (tests cost {self.spent} simulations)")
        if self.refinement:
            out.append(f"Suggested refinement: {self.refinement}")
        return out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def standard_objections(h: dict[str, Any]) -> list[Objection]:
    horizon = str(h.get("horizon") or "").lower()
    obs = [
        Objection("direction", "The data may say the opposite of your story."),
        Objection("stability", "One lucky stretch can carry a 10-year Sharpe."),
        Objection("sub_universe", "It may only live in illiquid microcaps nobody can trade."),
        Objection("known_factor", "It may just be a well-known factor in disguise."),
        Objection("neutralization", "It may be an industry bet, not a stock-level idea."),
    ]
    if horizon:
        obs.append(
            Objection(
                "horizon",
                f"You claimed a '{horizon}' horizon; check the signal's life actually matches.",
            )
        )
        if "day" not in horizon:
            obs.append(Objection("decay", "A slow idea should survive smoothing."))
    return obs


def _size(universe: str) -> float:
    digits = "".join(ch for ch in str(universe) if ch.isdigit())
    return float(digits) if digits else 3000.0


def _train_only(s: pd.Series) -> pd.Series:
    if not len(s):
        return s
    end = pd.Timestamp(s.index[-1]) - pd.DateOffset(years=TEST_YEARS)
    return s[s.index < end]


class Court:
    def __init__(
        self,
        store: ResearchStore,
        simulator: Simulator,
        alpha_type: AlphaType,
        hypothesis: dict[str, Any],
        fields: list[str],
        *,
        test_budget: int = 15,
        local_data: dict[str, pd.DataFrame] | None = None,
        available: set[str] | None = None,
        vector_fields: set[str] | None = None,
        operators: set[str] | None = None,
        scope: str = "",
        critic: Critic | None = None,
        log: Callable[[str], None] = lambda _: None,
    ) -> None:
        self.cfg = court_config()
        self.h, self.fields, self.log, self.critic = hypothesis, fields[:3], log, critic
        self.sign = -1 if str(hypothesis.get("expected_sign", "+")).strip() == "-" else 1
        self.r = Researcher(
            store,
            simulator,
            alpha_type,
            hypothesis,
            self.fields,
            test_budget,
            local_data=local_data,
            available=available,
            vector_fields=vector_fields,
            operators=operators,
            scope=scope,
        )
        self.available, self.operators = available, operators

    # -- helpers -----------------------------------------------------------------------------------
    async def _run(self, expr: str, field_: str, label: str, **settings: Any) -> Cand | None:
        c = Cand(expr, {**self.r.settings, **settings}, field_, label, "court", stage="court")
        done = await self.r.sim([c], role="court")
        return done[0] if done else None

    async def _series(self, c: Cand | None) -> pd.Series | None:
        if c is None or c.alpha_id is None:
            return None
        return _train_only(await self.r.pnl_series(c.alpha_id))

    def seed(self, f: str) -> str:
        """The plainest test of the claim on field ``f``, signed by the CLAIM, not the data."""
        tpl = str(self.h.get("template") or "")
        if tpl and "$" not in tpl and tpl != "-" and f in tpl:
            return tpl
        horizon = str(self.h.get("horizon") or "").lower()
        angle = "level" if "day" in horizon else str(self.h.get("angle") or "level")
        e = probes(f, f in self.r.vector)[angle]
        return e if self.sign > 0 else flip(e)

    # -- the trial ---------------------------------------------------------------------------------
    async def run(self) -> Case:
        objections = standard_objections(self.h)
        critique = refinement = ""
        if self.critic:
            try:
                out = self.critic(self.h, {"fields": self.fields, "tests": list(TESTS)})
                critique, refinement = out.get("critique", ""), out.get("refinement", "")
                objections += [
                    Objection(o["kind"], o.get("claim", ""), o.get("params") or {}, "critic")
                    for o in out.get("objections", [])
                    if isinstance(o, dict) and o.get("kind") in TESTS
                ]
            except Exception as exc:  # noqa: BLE001 - the Court still sits without its critic
                critique = f"(critic unavailable: {exc})"
        results: list[TestResult] = []
        seeds: dict[str, str] = {}
        for f in self.fields:
            seeds[f] = self.seed(f)
            results += await self.try_field(f, seeds[f], objections)
        verdicts = {f: self.judge([r for r in results if r.field == f]) for f in self.fields}
        # The overall verdict is the best any field earned: one surviving field is a lead.
        overall = min(verdicts.values(), key=VERDICTS.index) if verdicts else "REJECTED"
        return Case(self.h, seeds, results, verdicts, overall, critique, refinement, self.r.spent)

    async def try_field(self, f: str, seed: str, objections: list[Objection]) -> list[TestResult]:
        cfg, sev = self.cfg, self.cfg["severity"]
        base = await self._run(seed, f, "seed")
        if base is None:
            return [TestResult("direction", f, None, 3, None, "seed could not be simulated")]
        b = base.train_sharpe
        series = await self._series(base)
        out: list[TestResult] = []

        def add(o: Objection, passed: bool | None, value: float | None, detail: str) -> None:
            out.append(TestResult(o.kind, f, passed, sev.get(o.kind, 2), value, detail, o.claim))

        def retention(c: Cand | None) -> float | None:
            if c is None or math.isnan(c.train_sharpe) or b <= 0:
                return None
            return c.train_sharpe / b

        for o in objections:
            k = o.kind
            if k == "direction":
                ok = b >= cfg["min_train_sharpe"]
                why = (
                    "data agrees"
                    if ok
                    else "the data says the OPPOSITE"
                    if b <= -cfg["min_train_sharpe"]
                    else "too weak to tell"
                )
                add(o, ok, b, f"train Sharpe {b:+.2f} in the claimed direction — {why}")
            elif b < cfg["min_train_sharpe"]:
                add(o, None, None, "skipped: no signal in the claimed direction to test")
            elif k == "stability":
                ys = (
                    [v for v in an.yearly_sharpes(series).values() if not math.isnan(v)]
                    if series is not None
                    else []
                )
                frac = sum(v > 0 for v in ys) / len(ys) if ys else math.nan
                add(
                    o,
                    None if math.isnan(frac) else frac >= cfg["stable_years"],
                    frac,
                    f"{frac:.0%} of train years positive" if ys else "no yearly data",
                )
            elif k == "sub_universe":
                u = o.params.get("universe", cfg["sub_universe"])
                ret = retention(await self._run(seed, f, "sub_universe", universe=u))
                # Fewer stocks = fewer bets: even a real effect loses ~sqrt(n_sub/n) of its
                # Sharpe (fundamental law). Judge what's left AFTER that expected loss.
                breadth = math.sqrt(_size(u) / _size(self.r.settings.get("universe", "TOP3000")))
                adj = None if ret is None else ret / breadth
                add(
                    o,
                    None if adj is None else adj >= cfg["sub_universe_ratio"],
                    adj,
                    f"keeps {ret:.0%} of Sharpe in {u} = {adj:.0%} after the expected breadth loss"
                    if ret is not None and adj is not None
                    else "not run",
                )
            elif k == "known_factor":
                await self._known_factor(o, series, add)
            elif k == "horizon":
                d = int(o.params.get("days", cfg["delay_days"]))
                delayed = on_result(seed, lambda x, d=d: f"ts_delay({x}, {d})")
                ret = retention(await self._run(delayed, f, "horizon"))
                fast_claim = "day" in str(self.h.get("horizon") or "").lower()
                if ret is None:
                    add(o, None, None, "not run")
                else:
                    ok = ret < cfg["retention"] if fast_claim else ret >= cfg["retention"]
                    add(
                        o,
                        ok,
                        ret,
                        f"delayed {d}d keeps {ret:.0%} — "
                        + ("consistent with" if ok else "contradicts")
                        + f" a '{self.h.get('horizon')}' claim",
                    )
            elif k == "neutralization":
                n = o.params.get("neutralization", "INDUSTRY")
                if n == self.r.settings.get("neutralization"):
                    n = "MARKET"
                ret = retention(await self._run(seed, f, "neutralization", neutralization=n))
                add(
                    o,
                    None if ret is None else ret >= cfg["retention"],
                    ret,
                    f"{n} neutralization keeps {ret:.0%}" if ret is not None else "not run",
                )
            elif k == "decay":
                d = int(o.params.get("decay", 8))
                ret = retention(await self._run(seed, f, "decay", decay=d))
                add(
                    o,
                    None if ret is None else ret >= cfg["retention"],
                    ret,
                    f"decay {d} keeps {ret:.0%}" if ret is not None else "not run",
                )
            elif k == "contrast":
                await self._contrast(o, series, add)
        return out

    async def _known_factor(
        self,
        o: Objection,
        series: pd.Series | None,
        add: Callable[[Objection, bool | None, float | None, str], None],
    ) -> None:
        corrs: dict[str, float] = {}
        for name, fe in (self.cfg.get("known_factors") or {}).items():
            if self.available is not None and not set(analyse(fe).fields) <= self.available:
                continue
            other = await self._series(await self._run(fe, "_factor", name))
            if series is not None and other is not None:
                corrs[name] = an.correlation(series, other, min_overlap=120)
        corrs = {k: v for k, v in corrs.items() if not math.isnan(v)}
        if not corrs:
            add(o, None, None, "no factor library available")
            return
        worst = max(corrs, key=lambda n: abs(corrs[n]))
        v = corrs[worst]
        known = abs(v) >= self.cfg["max_factor_corr"]
        add(
            o,
            not known,
            v,
            f"max |corr| {abs(v):.2f} with {worst}" + (" — it's a known factor" if known else ""),
        )

    async def _contrast(
        self,
        o: Objection,
        series: pd.Series | None,
        add: Callable[[Objection, bool | None, float | None, str], None],
    ) -> None:
        ce = str(o.params.get("expression", ""))
        try:
            cf = set(analyse(ce).fields)
        except ParseError:
            add(o, None, None, f"critic's contrast didn't parse: {ce!r}")
            return
        if self.available is not None and not cf <= self.available:
            add(o, None, None, f"contrast uses unknown fields {sorted(cf - self.available)}")
            return
        other = await self._series(await self._run(ce, "_contrast", "contrast"))
        v = (
            an.correlation(series, other, min_overlap=120)
            if series is not None and other is not None
            else math.nan
        )
        add(
            o,
            None if math.isnan(v) else abs(v) < self.cfg["max_factor_corr"],
            v,
            f"corr {v:+.2f} with {ce}" if not math.isnan(v) else "not run",
        )

    def judge(self, results: list[TestResult]) -> str:
        if any(r.passed is False and r.severity >= 3 for r in results):
            return "REJECTED"
        ran = [r for r in results if r.passed is not None]
        if not ran:
            return "REJECTED"
        score = sum(r.severity for r in ran if r.passed) / sum(r.severity for r in ran)
        serious = any(r.passed is False and r.severity >= 2 for r in ran)
        return "SURVIVES" if score >= self.cfg["survive_score"] and not serious else "WOUNDED"

    async def promote(self, case: Case, budget: int, *, force: bool = False) -> Report | None:
        """Turn surviving fields into alphas with the budgeted planner."""
        keep = [
            f for f, v in case.verdicts.items() if v == "SURVIVES" or (force and v == "WOUNDED")
        ]
        if not keep:
            return None
        planner = Researcher(
            self.r.store,
            self.r.simulator,
            self.r.type,
            self.h,
            keep,
            budget,
            local_data=self.r.local,
            available=self.available,
            vector_fields=self.r.vector,
            operators=self.operators,
            scope=self.r.scope,
            log=self.log,
        )
        rep = await planner.run()
        case.promoted = rep.to_dict()
        return rep
