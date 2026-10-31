# Alpha Forge v2: real-BRAIN-first build playbook

This version replaces Phases 3 onward of BUILD_PLAYBOOK.md.

## What changed and why

- **BRAIN is the simulator, and BRAIN's checks are the judge.** Every pipeline runs real
  simulations on real universes (USA TOP3000, ILLIQUID_MINVOL1M, GLB TOPDIV3000, EUR TOP2500,
  ASI MINVOL1M …). Those choices are read live from `OPTIONS /simulations`, so only the
  combinations your account can run are offered.
- **Submittable means BRAIN says so.** Status comes from `/alphas/{id}/check` and the
  self / power-pool correlation endpoints, not from local guesses.
- **The Gym stays as Practice mode** for learning and for free pre-screening. It is never
  presented as a stand-in for BRAIN.
- **Every pipeline is per alpha type** (Regular, Power Pool, ATOM, Region-agnostic, …), with that
  type's own generation rules, gates, correlation rules and description requirements.
- **The system learns**: template × dataset × type statistics drive what runs next, and a
  surrogate model and template evolution improve the library every week.
- **The AI agent has memory** of your hypotheses, failures and lessons, and uses them to
  correct you.

Rules unchanged: every simulation goes through the Quota Governor; there is no submit code
path; selection uses train metrics only.

## How to run each phase
`/clear` → Shift+Tab to plan mode → paste → review the plan → approve. Then verify in the
browser yourself and run:
`git add -A && git commit -m "phase N: …" && git tag phase-N-done`

---

## Phase 3: Quota Governor and Today screen

```
Goal: the daily simulation budget becomes a first-class system (ARCHITECTURE.md §5.1,
config/forge/lanes.yaml).
- Tables quota_ledger and lane_state (alpha-harness db patterns + migrations). Every simulation
  the engine starts is recorded with a lane; existing screens' manual sims default to 'reserve'.
- Admission control before work enters the engine: refuse or trim plans exceeding a lane's
  remaining quota or the daily limit minus safety margin. Pacing (max_per_hour, quiet hours).
  Daily reset at BRAIN's reset time (the header already shows "Simulation Quota Reset in" —
  reuse that source). If BRAIN reports remaining quota, trust the lower of BRAIN and ledger.
- Fixed allocation for now; unused quota flows to busy lanes, never past the limit. Cache hits
  cost 0.
- API /api/forge/quota (today: planned vs spent per lane) and /api/forge/quota/estimate.
- Today screen as home (DESIGN.md): quota gauge (spent / limit), per-lane bars, hourly spend,
  safety-margin warning, one-line explanation per lane.
Tests offline (fake engine): admission, pacing, reset boundary, concurrency never exceeds the
limit, cache hits cost 0, manual sims land in 'reserve'.
```
Verify: run ONE manual simulation from an existing screen; Today shows 1 sim in `reserve`.

## Phase 4: Real scope and data (regions, universes, datasets, field sets)

```
Goal: research starts from REAL BRAIN data scopes.
- Scope = instrumentType + region + delay + universe (+ neutralization options), built ONLY
  from alpha-harness's resolved OPTIONS /simulations schema (brain/settings_schema.py). A scope
  picker component reused by every Forge screen.
- Extend the existing Data Explorer (don't rebuild it): per scope, datasets and fields with
  filters for category, field type (MATRIX/VECTOR/GROUP), coverage, date coverage,
  userCount/alphaCount (documented crowding score), pyramid multiplier for that
  region/delay/category, my alpha count and my pass rate in that dataset.
- Field sets: named selections WITH their scope; manual picks or saved dynamic filters;
  notes; CRUD API under /api/forge/fieldsets.
- Dataset page: coverage histogram, crowding vs pyramid-multiplier scatter, my history there
  (sims, BRAIN check pass rate, families started). Every chart gets a one-line caption.
Tests: scope resolution from a recorded OPTIONS fixture (hand-written), field-set CRUD,
crowding score.
```
Verify: create a field set "USA TOP3000 analyst" with 5 fields, and another in GLB MINVOL1M.

## Phase 5: Template Studio v2 (real fields, real eligibility, your own templates)

```
Goal: templates bound to real fields, with per-alpha eligibility computed from BRAIN's rules.
- Bind templates to a field set: expand slots with the set's REAL fields (respect field type,
  archetype if profiled, same_dataset_as / different_dataset_from), the set's scope, windows
  and groups available in that scope. Preview the rendered alphas before anything runs.
- Eligibility per RENDERED alpha, not per template, using brain/platform.py rules:
  Power Pool (<= 8 operators, <= 3 data fields with GROUPING_FIELDS not counted, PV1_OPERATORS
  count as pv1 usage), ATOM (single dataset), each alpha type YAML in config/forge/alpha_types.
  Show why an alpha is NOT eligible.
- Power Pool description drafting: generate a draft with BRAIN's headings (Idea / Rationale for
  data used / Rationale for operators used, >= 100 chars) from the template rationale and field
  descriptions, editable and saved with the alpha. Never auto-submitted.
- My templates: editor (reuse CodeMirror) with live lint, versioning, YAML export; built-ins
  read-only but cloneable.
- template_stats table: per template × dataset category × alpha type × scope: sims, BRAIN check
  pass rate, median train Sharpe, families started. Shown in the library and filled from Phase 6.
Tests: binding to real-shaped fields, eligibility edge cases (grouping fields, pv1 operators),
description draft format, versioning.
```
Verify: bind `analyst_revision_group` to your USA field set; see the rendered alphas, their
Power Pool eligibility, and a description draft.

## Phase 6: Type pipelines on BRAIN (the core research loop)

```
Goal: a separate, configurable pipeline per alpha type that runs REAL BRAIN simulations.
- Pipeline definitions in config/forge/pipelines/*.yaml: regular.yaml, power_pool.yaml,
  atom.yaml, region_agnostic.yaml (+ template for new types). Each declares: lane, alpha type,
  allowed template complexity, generation rules, settings grid, stages, gates, correlation rule
  (self vs power-pool), description requirement, robustness perturbations, budget per run.
- Engine adapter implementing the forge simulator interface on alpha-harness's
  slots/packer/tracker (crash-safe, lane on every child, Governor admission). Planner and Court
  now run on BRAIN through it.
- Stages: generate (template × field set) -> eligibility lint -> cache -> successive-halving
  rounds on BRAIN -> fetch BRAIN checks (/check) for candidates passing train gates -> type-
  specific correlation (self for Regular/ATOM, power-pool for Power Pool) for the top few only
  (rate limits) -> robustness/repair for strong-but-failing -> router tags every alpha with all
  types it fits.
- Pipelines screen: pick a pipeline + field set + templates + budget -> plan preview (sims per
  round, cache hits, expected cost) -> Approve -> live progress over WebSocket.
Tests with a fake engine: budget never exceeded, stage order, checks fetched only for
candidates, power-pool vs self correlation chosen by type, resume after restart.
```
Verify: run a Power Pool pipeline with a budget of 30 on a small field set. Watch the rounds
and the BRAIN checks arrive.

## Phase 7: Families and the Submittable Board

```
Goal: after simulations, group similar alphas and show exactly what is submittable and why.
- Families: cluster recent alphas by PnL correlation (BRAIN pnl recordset; threshold in
  learning.yaml) within each type's comparison set (BRAIN compares Power Pool with Power Pool,
  others with non-Power-Pool of the same region — see brain/platform.py notes).
- Submittable Board, grouped by day -> alpha type -> family: each family's leader with BRAIN
  check status (PASS / FAIL / PENDING per check, QUOTA_CHECKS and IGNORED_CHECKS handled as in
  platform.py), self or power-pool correlation result, train vs test, and the pool's
  before/after stats from BRAIN (`before_and_after`) to rank by what it ADDS.
- "Why not submittable" for every non-ready alpha, with a suggested repair (from court.yaml
  repairs) and a one-click "queue repair" into the validate lane.
- Power Pool leaders show their description draft and its BRAIN format checks.
- A manual submission checklist in ranked order (I submit on BRAIN; no submit API).
Tests: clustering on synthetic PnL, board grouping, check-status interpretation from fixtures.
```
Verify: the Board shows yesterday's and today's families per type, with clear "ready" vs
"why not".

## Phase 8: Research analytics and visualisation (what a desk looks at)

```
Goal: industry-grade analysis of REAL alphas from BRAIN recordsets (pnl, turnover,
yearly-stats) — every chart with a one-line "what this tells you".
- Alpha tear sheet: cumulative PnL with drawdown shading; underwater (drawdown) chart; rolling
  1-year Sharpe and volatility; PnL moving averages and +/-2 sigma bands; monthly returns
  calendar heatmap; yearly stats bars; daily return distribution with normal overlay, QQ plot,
  skew/kurtosis, worst days; turnover over time.
- Statistics: block-bootstrap Sharpe confidence interval, sign-flip permutation test,
  Deflated Sharpe with the real trial count of its template/pipeline, P(Sharpe > 1.25).
- Factor exposure: simulate a small known-factor set ONCE per scope on BRAIN (reversal,
  momentum 12-1, size, low-vol, industry momentum; cached forever; explore lane) and regress
  each alpha's PnL on them: betas, R², residual Sharpe ("what is really new").
- Pool views: correlation heatmap with dendrogram order, PCA of my pool (scree + loadings),
  compare 2–4 alphas side by side.
Tests on synthetic series with known answers (bootstrap, permutation, regression).
```

## Phase 9: Learning engine (it improves by itself)

```
Goal: the tool learns from my history and spends better every week (config/forge/learning.yaml).
- Opportunity scores per template × dataset category × alpha type × scope from template_stats
  with Bayesian shrinkage (few samples -> near the prior).
- Thompson-sampling selection: when I ask a pipeline to "auto-choose", it picks templates and
  field sets by sampling these posteriors, keeping an exploration share; lane bandit
  rebalances daily.
- Surrogate model (scikit-learn HistGradientBoosting): P(passes BRAIN checks) and expected train
  Sharpe from features in learning.yaml; nightly retrain; the planner ranks with it; calibration
  curve + feature importance; strict test: no test-period or post-simulation features.
- Template evolution in the template_research lane: mutate winning templates, test each mutant
  on a panel of datasets on BRAIN, score generalisation; survivors appear in the Studio as
  proposals with their evidence.
- Learning screen: yield per 100 sims over time, posterior means, calibration, best/worst
  templates, discoveries this week.
Tests: bandit on simulated arms, shrinkage, surrogate leakage, mutation validity.
```

## Phase 10: AI research agent with memory

```
Goal: a research partner that remembers my work and corrects me.
- Agent panel on every screen (alpha-harness LLM layer), context-aware (current screen,
  selected alpha/template/field set).
- Memory = the database: hypotheses, pipelines run, failures by reason, lessons, my notes and
  past corrections; a weekly summary is written to lessons automatically.
- Roles: researcher, critic (Hypothesis Court on BRAIN in the validate lane: every objection
  becomes a runnable test), analyst (explains tear sheets/families/factor exposure), tutor.
- Hypothesis Workshop screen: I write an idea -> agent challenges it with evidence from my
  history ("your last 6 sentiment alphas failed HIGH_TURNOVER without decay") -> Court trial ->
  survivors become a pipeline proposal.
- Tools: read everything; propose hypotheses/templates/pipelines as approval cards; spend only
  from the reserve lane after I click Approve; never submit. Show tokens/cost per session.
Tests: permissions (spend without approval refused), memory retrieval, tool schemas offline.
```

## Phase 11: Practice mode and optional real-price lab

```
- Rename the Gym to "Practice" with a clear banner: synthetic market for learning; results
  never compare to BRAIN.
- Optional: a Real-Price Lab using free daily prices for a large-cap US list (document
  survivorship bias and the lack of point-in-time fundamentals on screen). For learning
  price-volume ideas only, never shown next to BRAIN results.
- Learn screen: curriculum, spaced repetition, options primer (Black-Scholes, Black-76,
  Bachelier) for interview prep.
```
