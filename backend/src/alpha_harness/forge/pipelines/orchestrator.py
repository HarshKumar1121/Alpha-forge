"""The stage orchestrator: a pipeline config's own rules, run end to end on a Simulator.

    generate     every (bound template x field set) candidate the pipeline's template
                 complexities allow
    lint         drop anything the alpha type's own rules would refuse, before it can cost
                 a simulation (CLAUDE.md: a simulation is spent only once it is worth it)
    admit        cap the round's budget to what the Quota Governor says the lane may still
                 plan today (CLAUDE.md rule 3)
    rounds       hand the lint-cleared candidates to :class:`~..research.planner.Researcher`,
                 which runs expand -> robustness -> judge (cache-aware, budget-aware) exactly as
                 it does for a hypothesis-driven run — nothing about successive halving is
                 reinvented here
    check        BRAIN's own ``/check`` for the top candidates that passed the train gate
    correlation  self or power-pool correlation (the pipeline says which) for the top few
                 finalists only — rate limits make this expensive, so it is never run on
                 everything

Checks and correlations are cached in the :class:`~..store.ResearchStore`, so a pipeline that
is re-run after a restart never re-spends a BRAIN call for an alpha it already has an answer
for — the same "already paid for, never paid again" rule the simulation cache enforces.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from ..alpha_types import gate
from ..research.eligibility import judge
from ..research.planner import Cand, Researcher, _nz
from ..research.templates import expand_filled

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ..alpha_types import AlphaType
    from ..quota.governor import QuotaGovernor
    from ..research.planner import Report
    from ..research.templates import FieldInfo, Template
    from ..simulator import Simulator
    from ..store import ResearchStore
    from .config import PipelineConfig


class Checker(Protocol):
    """The two BRAIN calls this orchestrator needs once Researcher's rounds are done."""

    async def check_alpha(self, alpha_id: str) -> dict[str, Any]: ...

    async def correlations(self, alpha_id: str, kind: str = "self") -> dict[str, Any]: ...


@dataclass(slots=True)
class PipelineReport:
    research: Report
    #: (expression, reason) for every generated candidate the eligibility lint dropped.
    rejected: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    checks: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    correlations: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])


class PipelineRun:
    """Runs one pipeline config over one field set, within ``budget`` real simulations."""

    def __init__(
        self,
        store: ResearchStore,
        simulator: Simulator,
        governor: QuotaGovernor,
        checker: Checker,
        pipeline: PipelineConfig,
        alpha_type: AlphaType,
        field_set: Sequence[FieldInfo],
        templates: Sequence[Template],
        *,
        budget: int | None = None,
        target_dataset: str | None = None,
        log: Callable[[str], None] = lambda _: None,
    ) -> None:
        self.store, self.simulator, self.governor, self.checker = (
            store,
            simulator,
            governor,
            checker,
        )
        self.pipeline, self.type, self.field_set, self.templates = (
            pipeline,
            alpha_type,
            field_set,
            templates,
        )
        self.budget = pipeline.default_budget if budget is None else budget
        self.target_dataset = target_dataset
        self.log = log

    def _catalog(self) -> dict[str, dict[str, Any]]:
        return {f.id: {"dataset": f.dataset, "type": f.type} for f in self.field_set}

    def generate(self) -> list[Cand]:
        """Every candidate a bound template x field set yields, for this pipeline's complexities."""
        cands: list[Cand] = []
        for t in self.templates:
            if t.complexity not in self.pipeline.template_complexity:
                continue
            for filled in expand_filled(t, self.field_set, require_archetype=False):
                field_name = next(iter(filled.fields.values()), "")
                cands.append(
                    Cand(
                        filled.expression,
                        dict(self.type.settings),
                        field_name,
                        t.id,
                        t.category,
                        dict(filled.params),
                    )
                )
        return cands

    def lint(self, cands: list[Cand]) -> tuple[list[Cand], list[tuple[str, str]]]:
        """Candidates the alpha type's own rules would not refuse, and why the rest were cut."""
        catalog = self._catalog()
        eligible: list[Cand] = []
        rejected: list[tuple[str, str]] = []
        for c in cands:
            verdict = judge(
                c.expression, self.type, catalog=catalog, target_dataset=self.target_dataset
            )
            if verdict.ok:
                eligible.append(c)
            else:
                rejected.append((c.expression, "; ".join(verdict.reasons)))
        return eligible, rejected

    async def run(self) -> PipelineReport:
        self.log(f"generate: {len(self.templates)} template(s) x {len(self.field_set)} field(s)…")
        generated = self.generate()
        eligible, rejected = self.lint(generated)
        self.log(f"lint: {len(eligible)}/{len(generated)} eligible for {self.type.name}")

        admission = await self.governor.admit(self.pipeline.lane, [1] * self.budget)
        capped = admission.admitted
        self.log(f"admit: {capped}/{self.budget} sims admitted in lane {self.pipeline.lane!r}")

        researcher = Researcher(
            self.store,
            self.simulator,
            self.type,
            {},
            [f.id for f in self.field_set],
            capped,
            log=self.log,
        )
        report = await researcher.run_from(await researcher.sim(eligible, "variant"))

        checks = await self._fetch_checks(report)
        correlations = await self._fetch_correlations(report)
        return PipelineReport(report, rejected, checks, correlations)

    async def _fetch_checks(self, report: Report) -> dict[str, dict[str, Any]]:
        train_only = self.type.without_test_gates()
        passed = [
            c
            for c in sorted(report.candidates, key=lambda c: -_nz(c.train_sharpe))
            if c.alpha_id is not None and gate(c.alpha or {}, train_only).passed
        ][: self.pipeline.check_top_n]
        out: dict[str, dict[str, Any]] = {}
        for c in passed:
            aid = c.alpha_id
            if aid is None:
                raise ValueError("candidate missing alpha_id after filtering in _fetch_checks")
            cached = await self.store.get_check(aid)
            if cached is None:
                cached = await self.checker.check_alpha(aid)
                await self.store.save_check(aid, cached)
            out[aid] = cached
        return out

    async def _fetch_correlations(self, report: Report) -> dict[str, dict[str, Any]]:
        kind = self.pipeline.correlation_kind
        top = [c for c in report.finalists if c.alpha_id is not None][
            : self.pipeline.correlation_top_n
        ]
        out: dict[str, dict[str, Any]] = {}
        for c in top:
            aid = c.alpha_id
            if aid is None:
                raise ValueError(
                    "candidate missing alpha_id after filtering in _fetch_correlations"
                )
            cached = await self.store.get_correlation(aid, kind)
            if cached is None:
                cached = await self.checker.correlations(aid, kind=kind)
                await self.store.save_correlation(aid, kind, cached)
            out[aid] = cached
        return out
