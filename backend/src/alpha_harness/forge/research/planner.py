"""Budgeted research: get the most information out of a fixed number of simulations.

Rounds (successive halving, a standard way to spend scarce compute):

    R0 profile   learn each field's behaviour (free from raw data on the Gym; probes on BRAIN,
                 which double as candidates)
    R1 explore   one simulation per (field x behaviour-matched template), default window,
                 sign from the probes or your hypothesis
    R2 expand    keep the top third by TRAIN Sharpe; try neighbouring windows and one
                 alternative neutralization — to test the idea, not hunt for a magic number
    R3 robust    perturb the finalists' settings; fragile ones are dropped
    R4 judge     gates of the alpha type (test years looked at ONCE, here), Deflated Sharpe
                 for the number of sims spent, then families: one representative per bet

Every simulation is cached: an expression + settings already paid for is never paid again.

The planner runs against a :class:`~..simulator.Simulator` and a
:class:`~..store.ResearchStore`, so the same code researches on the Gym today and on BRAIN's
engine once Phase 6 adapts it. Shares and thresholds live in ``config/forge/court.yaml`` under
``planner`` and ``repairs``.

Ported from alpha-copilot ``research/planner.py``; recast onto the async Protocols.
"""

import dataclasses
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .. import analytics as an
from .. import config
from ..alpha_types import gate, score_tier
from ..expr import analyse, on_result
from ..simulator import request_for
from ..store import Experiment
from . import families as fam
from .profiler import Profile, merge, probes, profile_from_probes, profile_local
from .templates import Template, library, match, render_single

if TYPE_CHECKING:
    from collections.abc import Callable

    import pandas as pd

    from ...brain.schemas import SimulationRequest
    from ..alpha_types import AlphaType
    from ..simulator import Simulator
    from ..store import ResearchStore

#: Which probe angle a probe candidate expresses (a probe's "family" is its angle).
PROBE_ANGLES = ("level", "change", "history")


def planner_config() -> dict[str, Any]:
    return config.court()["planner"]


@dataclass(slots=True)
class Cand:
    """One candidate alpha: what to simulate, where it came from, and what it did."""

    expression: str
    settings: dict[str, Any]
    field: str
    template: str
    family: str  # the template's category, or a probe's angle
    # Not `field(...)`: this class has an attribute named `field`, which shadows it here.
    params: dict[str, Any] = dataclasses.field(default_factory=dict[str, Any])
    stage: str = "r1"
    sign: int = 1  # -1 when the template was flipped because the data said "less is better"
    alpha: dict[str, Any] | None = None
    cached: bool = False
    robustness: float | None = None
    verdict: str = ""

    def m(self, block: str, key: str) -> float:
        v = ((self.alpha or {}).get(block) or {}).get(key)
        return float(v) if v is not None else math.nan

    @property
    def train_sharpe(self) -> float:
        t = self.m("train", "sharpe")
        return t if not math.isnan(t) else self.m("is", "sharpe")

    @property
    def alpha_id(self) -> str | None:
        return (self.alpha or {}).get("id")


@dataclass(slots=True)
class Report:
    profiles: dict[str, Profile]
    candidates: list[Cand]
    finalists: list[Cand]
    families: list[dict[str, Any]]
    spent: int
    cached: int
    budget: int
    dsr: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        top = sorted(self.candidates, key=lambda c: -_nz(c.train_sharpe))[:15]
        return {
            "spent": self.spent,
            "cached": self.cached,
            "budget": self.budget,
            "profiles": self.profiles,
            "families": self.families,
            "dsr": self.dsr,
            "top_variants": [
                {
                    "expression": c.expression,
                    "stage": c.stage,
                    "template": c.template,
                    "train_sharpe": c.train_sharpe,
                    "turnover": c.m("is", "turnover"),
                    "neutralization": c.settings.get("neutralization"),
                    "decay": c.settings.get("decay"),
                    "verdict": c.verdict,
                }
                for c in top
            ],
            "summary": self.lines(),
        }

    def lines(self) -> list[str]:
        out = [f"Spent {self.spent}/{self.budget} simulations (+{self.cached} reused from cache)."]
        for f, p in self.profiles.items():
            out.append(f"  {f}: {p.get('archetype')} — " + " ".join(p.get("notes", [])[:2]))
        screened = [c for c in self.candidates if c.verdict.startswith("screened")]
        out.append(
            f"{len(self.candidates)} variants tried, {len(screened)} screened out, "
            f"{len(self.finalists)} passed robustness + gates, {len(self.families)} families:"
        )
        if reasons := _screen_reasons(screened):
            top = sorted(reasons.items(), key=lambda kv: -kv[1])[:4]
            out.append("  screened for: " + ", ".join(f"{k} x{v}" for k, v in top))
        for r in self.families:
            mark = "SUBMIT?" if r["submit_candidate"] else "collides with pool"
            out.append(
                f"  F{r['family']} ({r['size']} alike) {mark}: {r['expression']}  "
                f"train {r['train_sharpe']:.2f}  DSR {self.dsr.get(r['leader'], math.nan):.2f}"
            )
        return out


def _screen_reasons(screened: list[Cand]) -> dict[str, int]:
    """Count screen-out reasons, named as BRAIN's checks name them."""
    names = {
        "is.sharpe": "LOW_SHARPE",
        "is.fitness": "LOW_FITNESS",
        "train.sharpe": "LOW_TRAIN_SHARPE",
    }
    reasons: dict[str, int] = {}
    for c in screened:
        kinds: set[str] = set()
        for r in c.verdict.removeprefix("screened: ").split("; "):
            k = r.split("=")[0].split(" <")[0].replace("BRAIN check ", "").replace(" FAILED", "")
            if k == "is.turnover":
                k = "LOW_TURNOVER" if "<" in r else "HIGH_TURNOVER"
            kinds.add(names.get(k, k))
        for k in kinds:
            reasons[k] = reasons.get(k, 0) + 1
    return reasons


def estimate(n_fields: int, budget: int) -> dict[str, int]:
    """How a budget would be split, before spending anything."""
    cfg = planner_config()
    cap = int(cfg["probe_share_max"] * budget)
    per = 3 if 3 * n_fields <= cap else (1 if n_fields <= cap else 0)
    probes_ = per * n_fields
    r1 = min(int(cfg["round1_share"] * budget), budget - probes_)
    r2 = min(int(cfg["round2_share"] * budget), budget - probes_ - r1)
    return {
        "probes": probes_,
        "probes_per_field": per,
        "explore_max": r1,
        "expand_max": r2,
        "robustness_max": max(budget - probes_ - r1 - r2, 0),
    }


class Researcher:
    """Runs the rounds above for one hypothesis over a few fields, within ``budget`` sims.

    ``local_data`` (field -> dates x stocks, plus ``cap``) is only available on the Gym; on
    BRAIN the probes do the profiling. ``operators``, when given, keeps templates the
    simulator cannot evaluate out of the plan.
    """

    def __init__(
        self,
        store: ResearchStore,
        simulator: Simulator,
        alpha_type: AlphaType,
        hypothesis: dict[str, Any],
        fields: list[str],
        budget: int,
        *,
        local_data: dict[str, pd.DataFrame] | None = None,
        vector_fields: set[str] | None = None,
        available: set[str] | None = None,
        operators: set[str] | None = None,
        scope: str = "",
        log: Callable[[str], None] = lambda _: None,
    ) -> None:
        self.store, self.simulator, self.type, self.h = store, simulator, alpha_type, hypothesis
        self.fields, self.budget = fields, budget
        self.local, self.vector = local_data or {}, vector_fields or set()
        self.available, self.operators, self.log = available, operators, log
        self.cfg = planner_config()
        self.settings: dict[str, Any] = {
            **alpha_type.settings,
            **(hypothesis.get("settings") or {}),
        }
        self.scope = scope or "{region}/{delay}/{universe}".format(**self.settings)
        self.spent = self.cached = 0
        self.train_only = alpha_type.without_test_gates()
        self.prior_sign = -1 if str(hypothesis.get("expected_sign", "+")).strip() == "-" else 1

    # -- simulation with cache & budget ------------------------------------------------------------
    def left(self) -> int:
        return self.budget - self.spent

    def request(self, c: Cand) -> SimulationRequest:
        return request_for(c.expression, c.settings, self.type.sim_type)

    async def is_cached(self, c: Cand) -> bool:
        return await self.store.find(self.request(c)) is not None

    async def sim(self, cands: list[Cand], role: str = "variant") -> list[Cand]:
        """Simulate what isn't cached, within the budget; return the candidates with results."""
        todo: list[Cand] = []
        for c in cands:
            hit = await self.store.find(self.request(c))
            if hit:
                c.alpha, c.cached = {**hit.alpha, "id": hit.alpha_id}, True
                self.cached += 1
            else:
                todo.append(c)
        todo = todo[: max(self.left(), 0)]
        size = max(self.simulator.batch_size, 1)
        for i in range(0, len(todo), size):
            batch = todo[i : i + size]
            outcomes = await self.simulator.simulate([self.request(c) for c in batch])
            # A simulator-side cache hit (BRAIN dedupe) is free; everything else is spent.
            self.spent += sum(not o.cached for o in outcomes)
            for c, o in zip(batch, outcomes, strict=True):
                if not o.ok or o.alpha_id is None or o.alpha is None:
                    c.verdict = f"ERROR:{o.error}"
                    continue
                c.alpha = {**o.alpha, "id": o.alpha_id}
                if self.simulator.ephemeral:
                    await self.store.save_pnl(o.alpha_id, await self.simulator.pnl(o.alpha_id))
                await self.store.record(
                    Experiment(
                        request=o.request,
                        alpha_id=o.alpha_id,
                        alpha=c.alpha,
                        alpha_type=self.type.name,
                        role=role,
                        stage=c.stage,
                        hypothesis_id=self.h.get("id"),
                    )
                )
        return [c for c in cands if c.alpha]

    async def pnl_series(self, alpha_id: str) -> pd.Series:
        rows = await self.store.load_pnl(alpha_id) or await self.simulator.pnl(alpha_id)
        await self.store.save_pnl(alpha_id, rows)
        return an.to_series(rows)

    # -- rounds ------------------------------------------------------------------------------------
    async def profile_fields(self) -> tuple[dict[str, Profile], list[Cand]]:
        n = len(self.fields)
        cap = int(self.cfg["probe_share_max"] * self.budget)
        per_field = 3 if 3 * n <= cap else (1 if n <= cap else 0)
        profiles: dict[str, Profile] = {}
        probe_cands: list[Cand] = []
        for f in self.fields:
            local = profile_local(self.local[f], self.local.get("cap")) if f in self.local else None
            exprs = list(probes(f, f in self.vector).items())[:per_field]
            cs = [Cand(e, dict(self.settings), f, f"probe_{k}", k, stage="probe") for k, e in exprs]
            cached = await self.store.get_profile(f, self.scope)
            if cached and str(cached.get("source", "")).endswith("probes"):
                profiles[f] = merge(local, cached)
                # The probes are candidates too. Their results are already paid for, so bring
                # them back from the cache: a re-run then ranks exactly what the first run did.
                hits = [c for c in cs if await self.is_cached(c)]
                probe_cands += await self.sim(hits, "probe")
                continue
            if per_field == 0:
                profiles[f] = merge(local, None)
                continue
            done = {c.template.removeprefix("probe_"): c.alpha for c in await self.sim(cs, "probe")}
            probe_cands += [c for c in cs if c.alpha]
            p = merge(local, profile_from_probes(done) if done else None)
            profiles[f] = p
            if done:
                await self.store.save_profile(f, self.scope, p)
        return profiles, probe_cands

    def sign_for(self, profile: Profile, t: Template) -> int:
        """Sign a template by the train Sharpe of the probe for its angle; else the claim's."""
        v = (profile.get("angles") or {}).get(t.angle)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return self.prior_sign
        return 1 if v >= 0 else -1

    async def explore(self, profiles: dict[str, Profile], seen: set[str]) -> list[Cand]:
        want_r1 = int(self.cfg["round1_share"] * self.budget)
        cands: list[Cand] = []
        for f, p in profiles.items():
            ts = match(
                p,
                available=self.available,
                alpha_type=self.type,
                field=f,
                vector=f in self.vector,
                operators=self.operators,
            )
            best = p.get("best_angle")
            ts.sort(key=lambda t: t.angle != best)  # best angle first
            for t in ts:
                params = t.default_params()
                e = render_single(t, f, params, sign=self.sign_for(p, t), vector=f in self.vector)
                if e not in seen:
                    seen.add(e)
                    cands.append(Cand(e, dict(self.settings), f, t.id, t.category, params))
        # Interleave fields so a small budget still covers every field.
        by_field: dict[str, list[Cand]] = {}
        for c in cands:
            by_field.setdefault(c.field, []).append(c)
        order = [c for grp in _zip_longest(*by_field.values()) for c in grp]
        return await self.sim(order[: min(want_r1, self.left())])

    async def expand(self, ranked: list[Cand], seen: set[str]) -> list[Cand]:
        keep = max(1, math.ceil(len(ranked) * self.cfg["keep_fraction"]))
        top = [c for c in ranked[:keep] if c.train_sharpe >= self.cfg["min_train_sharpe"]]
        lib = {t.id: t for t in library()}
        new: list[Cand] = []
        alt_neut = "MARKET" if self.settings.get("neutralization") != "MARKET" else "INDUSTRY"
        for c in top:
            t = lib.get(c.template)
            if t:
                for p in t.neighbours(c.params):
                    e = render_single(t, c.field, p, sign=c.sign, vector=c.field in self.vector)
                    if e not in seen:
                        seen.add(e)
                        new.append(
                            Cand(
                                e,
                                dict(self.settings),
                                c.field,
                                c.template,
                                c.family,
                                p,
                                "r2",
                                sign=c.sign,
                            )
                        )
            new.append(
                Cand(
                    c.expression,
                    {**c.settings, "neutralization": alt_neut},
                    c.field,
                    c.template,
                    c.family,
                    c.params,
                    stage="r2",
                )
            )
        want = min(int(self.cfg["round2_share"] * self.budget), self.left())
        fixes = self.repairs(ranked[: keep * 2], seen)[: max(1, want // 2)]  # at most half
        return await self.sim((fixes + new)[:want])

    def repairs(self, ranked: list[Cand], seen: set[str]) -> list[Cand]:
        """Strong-but-failing candidates get ONE targeted fix per failure, not a parameter hunt."""
        rules: dict[str, Any] = config.court().get("repairs") or {}
        out: list[Cand] = []
        for c in ranked:
            if c.stage == "repair" or c.train_sharpe < self.cfg["min_train_sharpe"] or not c.alpha:
                continue  # never repair a repair
            g = gate(c.alpha, self.train_only)
            if g.passed:
                continue
            key = _repair_for(" ".join(g.reasons), c.m("is", "turnover"))
            r = rules.get(key) if key is not None else None
            if key is None or not r:
                continue
            if (
                r.get("needs")
                and self.available is not None
                and not set(r["needs"]) <= self.available
            ):
                continue
            settings = {**c.settings, **(r.get("settings") or {})}
            expr = c.expression
            if "expr" in r:
                inner = f"ts_backfill({c.field}, 63)"
                rule = str(r["expr"]).replace("$inner", inner)
                expr = on_result(c.expression, lambda x, rule=rule: rule.replace("$x", f"({x})"))
            seen_key = expr + str(sorted(settings.items()))
            if seen_key in seen:
                continue
            seen.add(seen_key)
            out.append(
                Cand(
                    expr,
                    settings,
                    c.field,
                    f"{c.template}+{key.lower()}",
                    c.family,
                    c.params,
                    stage="repair",
                )
            )
        return out

    async def robustness(self, finalists: list[Cand]) -> None:
        rob = self.type.robustness
        hold = rob.get("hold_ratio", 0.7)
        for c in finalists:
            kids = [
                Cand(
                    c.expression,
                    {**c.settings, k: v},
                    c.field,
                    c.template,
                    c.family,
                    c.params,
                    "r3",
                )
                for k, vals in (rob.get("perturb") or {}).items()
                for v in vals
                if c.settings.get(k) != v
            ]
            if self.left() < len(kids) and not all([await self.is_cached(k) for k in kids]):
                c.verdict = "not perturbed (budget)"
                continue
            done = await self.sim(kids, role="robustness")
            base = c.train_sharpe
            ok = [k for k in done if k.train_sharpe >= hold * base]
            c.robustness = len(ok) / len(done) if done else None

    # -- orchestration -----------------------------------------------------------------------------
    async def run(self) -> Report:
        self.log(f"R0 profiling {len(self.fields)} field(s)…")
        profiles, probe_cands = await self.profile_fields()
        seen = {c.expression for c in probe_cands}
        self.log(f"R1 exploring behaviour-matched templates… ({self.left()} sims left)")
        r1 = await self.explore(profiles, seen)
        pool = probe_cands + r1
        ranked = sorted(pool, key=lambda c: -_nz(c.train_sharpe))
        self.log(f"R2 expanding the top third… ({self.left()} sims left)")
        r2 = await self.expand(ranked, seen)
        allc = pool + r2
        ranked = sorted(allc, key=lambda c: -_nz(c.train_sharpe))
        # Pre-gate on train-only rules (no test peeking), then one finalist per field+family,
        # so robustness sims are never spent on near-duplicates or certain failures.
        prelim = [
            c
            for c in ranked
            if c.train_sharpe >= self.cfg["min_train_sharpe"]
            and gate(c.alpha or {}, self.train_only).passed
        ]
        finalists: list[Cand] = []
        taken: set[tuple[str, str]] = set()
        for c in prelim:
            if (c.field, c.family) not in taken:
                taken.add((c.field, c.family))
                finalists.append(c)
            if len(finalists) >= self.cfg["robustness_top"]:
                break
        for c in ranked:
            if c not in finalists and not c.verdict:
                g = gate(c.alpha or {}, self.train_only)
                c.verdict = "" if g.passed else "screened: " + "; ".join(g.reasons)
        self.log(f"R3 robustness on {len(finalists)} finalist(s)… ({self.left()} sims left)")
        await self.robustness(finalists)
        need = self.type.robustness.get("min_fraction_holding", 0.6)
        judged: list[Cand] = []
        for c in finalists:
            # The one place the test period is looked at: the full gates, once.
            g = gate(c.alpha or {}, self.type)
            if not g.passed:
                c.verdict = "; ".join(g.reasons)
            elif c.robustness is not None and c.robustness < need:
                c.verdict = f"fragile ({c.robustness:.0%} of perturbations held)"
            else:
                c.verdict = "pass"
                judged.append(c)
        return await self.judge(profiles, allc, judged)

    async def judge(
        self, profiles: dict[str, Profile], allc: list[Cand], judged: list[Cand]
    ) -> Report:
        n = max(len(allc), 1)
        trial_sharpes = [c.train_sharpe for c in allc if not math.isnan(c.train_sharpe)]
        series: dict[str, pd.Series] = {}
        scores: dict[str, float] = {}
        meta: dict[str, dict[str, Any]] = {}
        dsr: dict[str, float] = {}
        for c in judged:
            aid = c.alpha_id
            if aid is None:
                continue
            s = await self.pnl_series(aid)
            series[aid] = s
            scores[aid] = c.train_sharpe
            dsr[aid] = an.deflated_sharpe(s, n, trial_sharpes)
            meta[aid] = {
                "expression": c.expression,
                "train_sharpe": c.train_sharpe,
                "tier": score_tier(c.alpha or {}, self.type),
                "operators": analyse(c.expression).operator_count,
            }
        families = fam.cluster(series, scores, self.cfg["family_corr"])
        region = self.settings.get("region")
        members = await self.store.pool(self.type.correlation.get("pool", self.type.name), region)
        pool = {m: await self.pnl_series(m) for m in members}
        fam.against_pool(families, series, pool, self.type.correlation.get("ceiling", 0.7))
        return Report(
            profiles,
            allc,
            judged,
            fam.report(families, meta),
            self.spent,
            self.cached,
            self.budget,
            dsr,
        )


def _repair_for(reasons: str, turnover: float) -> str | None:
    """The one fixable failure worth a repair simulation, by priority."""
    if "HIGH_TURNOVER" in reasons or ("is.turnover" in reasons and turnover > 0.5):
        return "HIGH_TURNOVER"
    if "fitness" in reasons and turnover > 0.3:
        return "LOW_FITNESS_FAST"
    for k in ("LOW_TURNOVER", "CONCENTRATED_WEIGHT", "LOW_SUB_UNIVERSE_SHARPE"):
        if k in reasons:
            return k
    return None


def _nz(x: float) -> float:
    return -math.inf if math.isnan(x) else x


def _zip_longest(*groups: list[Cand]) -> list[list[Cand]]:
    return [
        [g[i] for g in groups if i < len(g)]
        for i in range(max((len(g) for g in groups), default=0))
    ]
