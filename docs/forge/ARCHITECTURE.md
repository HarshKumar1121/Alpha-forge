# Alpha Forge — architecture

Alpha Forge is a personal, daily-use alpha research platform for WorldQuant BRAIN. It is a fork
of **alpha-harness** (MIT; its BRAIN engine, UI shell, expression editor and charts) extended with
the research layer of **alpha-copilot** (profiler, templates, planner, Court, Gym, tutor) and new
industry-style systems: a quota governor, a dataset behaviour lab, a template studio with
template research, a learning engine and an embedded AI research agent.

## 1. Principles

1. **The budget is the design.** 5,000 simulations a day is the scarce resource. Every component
   either saves simulations (cache, lint, surrogate ranking, redundancy pruning, early stopping)
   or spends them where the evidence says they pay (lanes, bandit, successive halving).
2. **Ideas before expressions.** Templates carry an economic rationale; hypotheses carry a
   mechanism; the Court attacks ideas before they become alphas.
3. **Judge on train, look at test once.** Selection never uses the held-out period.
4. **Humans submit.** No submit code path exists. The tool prepares, ranks and explains.
5. **Everything is config.** Alpha-type rules, lanes, templates and thresholds live in YAML so
   new BRAIN types or competitions need no code.
6. **It teaches.** Every screen explains what a number means; the agent corrects you.

## 2. What we reuse, what we add

| Area | From alpha-harness (keep) | From alpha-copilot (port) | New |
|---|---|---|---|
| BRAIN API | client, endpoints, auth/ALTCHA, rate-limit learning | — | quota ledger hooks |
| Execution | slots, packer, tracker (crash-safe ids), reconcile | — | lane-aware queue, pacing |
| Storage | SQLite (SQLAlchemy async) + DuckDB for analytics | journal tables | lanes, templates, field sets, families, lessons |
| Metrics | vault metrics, yields, PnL backfill | analytics (DSR, collision rule) | Monte Carlo, PCA, factor exposure |
| Research | labs (search/TPE, GA, template, power pool, super) | profiler, templates, planner, families, Court | Template Studio, Forge builder, Behaviour Lab |
| Learning | Optuna | — | bandit allocator, surrogate model, template evolution |
| UI | React shell, router, CodeMirror editor, lightweight-charts, cmdk | — | ECharts (heatmaps, PCA, radar), new screens |
| AI | multi-provider LLM layer, chat | mentor/tutor/researcher/critic prompts + tools | agent panel with tool permissions |
| Learning to be a QR | — | Gym, curriculum, spaced repetition | in-app Learn screen |

## 3. System map

```
            ┌─────────────────────────── UI (React, localhost) ───────────────────────────┐
            │ Today · Data Explorer · Behaviour Lab · Template Studio · Forge · Runs ·     │
            │ Alpha Lab · Families & Portfolio · Court · Journal · Learn · Agent panel     │
            └───────────────▲──────────────────────────────────────────────▲─────────────┘
                            │ REST + WebSocket (live progress)             │
┌───────────────────────────┴──────────────── FastAPI backend ─────────────┴──────────────┐
│  Catalog & Field Sets ──► Behaviour Lab ──► Template Studio ──► Forge (experiment plans) │
│                                                             │                            │
│                          Learning Engine ◄──────────────────┤ ranks, prunes, allocates   │
│                    (bandit · surrogate · template evolution)│                            │
│                                                             ▼                            │
│  Quota Governor (lanes, ledger, pacing) ──► Execution Engine (alpha-harness slots/packer)│
│                                                             │                            │
│  Router (every sim → every alpha type) ◄────────────────────┘                            │
│        │                                                                                 │
│        ▼                                                                                 │
│  Evaluation: gates · robustness · Court · Monte Carlo · DSR · factor exposure             │
│        │                                                                                 │
│        ▼                                                                                 │
│  Alpha Book: daily grouping · families · portfolio marginal Sharpe · submission queue     │
│        │                                                                                 │
│        └──► Journal & Lessons ──► AI agent (researcher · critic · analyst · tutor)        │
└──────────────────────────────────────────────────────────────────────────────────────────┘
   SQLite (state, journal, cache)   DuckDB/Parquet (PnL, analytics)   YAML config (rules, lanes, templates)
```

## 4. A day with the tool

1. **Morning (Today screen).** Overnight results grouped by lane and type; quota plan for today
   (bandit-adjusted); new families; decay alerts; lessons; reviews due.
2. **Explore (15 min).** Data Explorer: filter datasets (region, category, coverage, crowding,
   pyramid multiplier) → save a *field set*. Behaviour Lab profiles it (explore lane).
3. **Forge (15 min).** Pick field set × templates × target types → see estimated sims, cache
   hits and predicted yield → approve. The engine runs it through the day under pacing.
4. **Validate.** Strong candidates go to the Court and robustness (validate lane) automatically.
5. **Evening (Alpha Lab).** Read tear sheets, compare families, ask the agent why things failed,
   queue submissions (you submit on BRAIN), record lessons, close hypotheses.

## 5. Modules

### 5.1 Quota Governor
- Config: `config/forge/lanes.yaml`. Lanes have share and bounds; `daily_limit` 5000 minus
  `safety_margin`.
- **Ledger** table: one row per simulation (lane, plan id, time, alpha id, cached flag). Cache
  hits cost nothing and are recorded as such.
- Admission control: a plan is accepted into a lane only if the lane's remaining quota covers it;
  otherwise it is queued for tomorrow or trimmed (lowest predicted value first).
- Pacing: hourly cap; quiet hours; a lane can't starve others (fair-share across lanes).
- Daily reset from BRAIN's reset time; reads remaining quota from BRAIN headers when available
  and trusts the lower of BRAIN's number and the ledger's.
- Allocation: `fixed` or `bandit` (5.9a).

### 5.2 Catalog, Data Explorer and Field Sets
- Sync datasets and fields (alpha-harness catalog) per region/delay/universe.
- Filters: region, delay, universe, dataset category, field type (MATRIX/VECTOR/GROUP), coverage,
  date coverage, `userCount`/`alphaCount` (**crowding score**), pyramid multiplier, your own alpha
  count there, archetype (once profiled), text search over descriptions.
- **Field sets**: named, saved selections ("analyst revisions USA", "uncrowded news EUR") with
  notes. Field sets are the unit the Forge consumes. Manual pick + filter-based (dynamic) sets.
- Dataset page: field table, coverage histogram, crowding vs pyramid scatter, your history
  there (sims, passes, families).

### 5.3 Behaviour Lab (understand data before writing alphas)
BRAIN does not expose raw field values, so behaviour is learned from **probe alphas** + metadata:
- Field probes (3 sims, cached forever): level `rank(B)`, change `rank(ts_delta(B,21))`, own
  history `ts_rank(B,252)` with `B = ts_backfill(field,63)`. Output: archetype (slow /
  medium / persistent / fast), best angle, sign, turnover, coverage (long+short counts).
- **Dataset fingerprint**: probe a stratified sample of k fields (k chosen by budget) →
  archetype mix, average strength, sign consistency.
- **Redundancy map**: correlation matrix of probe PnLs → hierarchical clustering → keep one
  representative per cluster. Typically removes 30–60% of fields before any template run.
- **PCA** of probe PnLs: how many independent ideas does this dataset really contain?
- Charts: probe cumulative PnL overlay, angle bars, archetype radar, correlation heatmap with
  dendrogram, PCA scree and loadings.
- Gym mode: same screens on the synthetic market with raw-data statistics (update frequency,
  rank persistence, size correlation) for learning.

### 5.4 Template Studio (and template research)
- Library: `config/forge/templates/*.yaml` (built-in) + user templates (stored in DB, exportable
  to YAML). Each template: id, name, category, complexity, **typed slots**, expression,
  rationale, author, version.
- Slot types: `field` (type MATRIX/VECTOR, archetype filter, `same_dataset_as`,
  `different_dataset_from`, vector `aggregate`), `window` (economic values only), `group`.
- Editor (CodeMirror from alpha-harness) with live lint: syntax, operators on your account,
  operator/field counts, **which alpha types it fits** (computed from alpha_types YAML), lookback
  sanity, nesting depth.
- Template statistics: yield by dataset category, archetype and alpha type; median train Sharpe;
  share of families started; correlation of its alphas with the pool.
- **Template research** (template_research lane): mutate templates (swap operator within a
  family, add a group wrapper, add a conditioning `trade_when`, change smoothing), test on a
  *panel of datasets*, and score **generalisation** (works across many datasets) rather than one
  lucky fit. Promising mutants become proposed templates for you to review and name.

### 5.5 Forge (experiment builder)
Input: field set(s) × templates × settings grid × target alpha types × lane × budget.
1. Expand slots (respecting archetype and dataset constraints) → candidate list.
2. Lint every candidate for every target type; drop failures (free).
3. Cache lookup (expression + settings hash) — paid-for results are reused (free).
4. Redundancy pruning via the Behaviour Lab clusters (free).
5. **Surrogate ranking** (5.9b): order by predicted value; keep an exploration slice (random 15%).
6. Successive halving: round 1 at default windows → keep top third (train Sharpe) → neighbours
   and targeted repairs → robustness on distinct finalists.
7. Submit to the Governor as a plan; the UI shows estimated sims per round and expected cost.

### 5.6 Execution engine and Router
- alpha-harness engine unchanged in spirit: 8 slots × up to 10 children, packing by shared keys,
  crash-safe tracking, reconcile on restart. Add: lane id on every child; Governor admission.
- **Router**: every finished alpha is evaluated against every alpha type YAML (Regular, Power
  Pool, ATOM, ARC tiers, …) and tagged with all it qualifies for. One sim, all types.

### 5.7 Evaluation
- Gates per alpha type (BRAIN checks are final; local gates pre-filter).
- Robustness: settings perturbation; sub-universe (breadth-adjusted); neighbour windows.
- **Hypothesis Court** (from alpha-copilot): direction, stability, sub-universe, known factor,
  horizon, neutralization, decay, critic contrasts → SURVIVES / WOUNDED / REJECTED.
- **Monte Carlo**: block-bootstrap Sharpe confidence interval; permutation (sign-flip /
  circular-shift) null; probability of Sharpe > threshold; drawdown distribution.
- Deflated Sharpe with honest trial counts per hypothesis/template.
- **Factor exposure**: regress alpha PnL on known-factor alpha PnLs (reversal, momentum, size,
  low-vol, industry momentum) → betas, R², residual Sharpe ("what's really new").

### 5.8 Alpha Book: daily grouping, families, portfolio
- Every alpha carries: date, lane, plan, template, field set, dataset(s), type tags, family.
- **Daily book**: the Today and Alpha Lab screens group by day → lane → type → family.
- **Families**: leader clustering on PnL correlation (threshold 0.6), recomputed nightly; checked
  against your submitted pool (self-correlation risk).
- **Portfolio**: incremental Sharpe of each family leader vs your submitted pool; greedy
  submission order; correlation heatmap with clustering; PCA of the pool.
- Submission queue: a checklist you work through on the BRAIN website (no API submit).
- Decay monitor: live vs backtest Sharpe for submitted alphas, by template and lane.

### 5.9 Learning engine (the tool improves from your own history)
a. **Lane bandit** — Thompson sampling on each lane's reward (robust alpha that starts a new
   family per sim), within YAML bounds, rebalanced daily on a 14-day window.
b. **Surrogate model** — gradient boosting (scikit-learn HistGradientBoosting; LightGBM optional)
   predicting P(passes gates) and expected train Sharpe *before* simulating. Features: template
   id/category/complexity, operator count, dataset category, field archetype, coverage, crowding,
   pyramid multiplier, settings, region. Retrained nightly; calibration plot on the Learning
   screen; the Forge keeps a random exploration slice so it can't collapse onto its own beliefs.
c. **Template evolution** — genetic search over template ASTs with cross-dataset
   generalisation fitness; mutants are proposals, never auto-promoted.
d. **Lesson mining** — weekly statistics over failures ("HIGH_TURNOVER in 61% of sentiment
   alphas without decay") turned into lessons the agent can cite.

### 5.10 AI research agent
One agent, four roles, shared tools, strict permissions:
- **Researcher** — hypothesis → field behaviour → templates → budget → plan.
- **Critic** — the Court's devil's advocate; objections must be testable.
- **Analyst** — reads tear sheets, families, factor exposures; explains in plain language.
- **Tutor** — curriculum, quizzes, spaced repetition; corrects misconceptions.
Tools: search catalog, field/dataset profiles, template library and stats, lint, Forge
estimate, run Gym experiments, read alphas/tear sheets/families/ledger, journal and lessons,
propose hypothesis/template/plan. **Permissions**: read freely; propose freely; spend only from
the `reserve` lane and only after you click Approve on the proposal card; never submit.
Provider: alpha-harness's multi-provider LLM layer (Anthropic, OpenAI-compatible).

### 5.11 Visualisation catalogue
- Time series (lightweight-charts): cumulative PnL with drawdown shading, rolling 1-year Sharpe,
  turnover, live vs backtest overlay.
- ECharts: correlation heatmaps with dendrogram order, PCA scree/loadings, yearly Sharpe bars,
  quintile-style bars (Gym), archetype radar, crowding vs multiplier scatter, lane quota gauges,
  funnel per lane, bootstrap Sharpe histogram, surrogate calibration curve.
- Every chart has a one-line "what this tells you" caption (learning goal).

### 5.12 Data model (new tables; alpha-harness tables stay)
`quota_ledger`, `lane_state`, `field_sets`, `field_profiles`, `dataset_fingerprints`,
`templates`, `template_stats`, `plans`, `plan_items`, `alpha_tags` (type tags from the router),
`families`, `family_members`, `hypotheses`, `court_cases`, `lessons`, `surrogate_models`,
`learning_progress`. PnL lives in DuckDB/Parquet keyed by alpha id.

### 5.13 Config
```
config/forge/lanes.yaml            quota governor
config/forge/alpha_types/*.yaml    rules per alpha type / competition (extends:)
config/forge/templates/*.yaml      template library (typed slots + rationale)
config/forge/court.yaml            court tests, thresholds, known-factor library
config/forge/learning.yaml         bandit, surrogate and evolution settings
```

## 6. Non-negotiables
No submit code. No automatic retry of failed sign-in. Respect Retry-After / 429. Stay inside
the daily limit with margin. Secrets never committed. Test-period metrics are not used to choose.
Every new module has offline tests (mocked BRAIN, Gym client).

## 7. Build order (see BUILD_PLAYBOOK.md)
0 setup → 1 run base → 2 port research layer → 3 Governor → 4 Explorer + field sets →
5 Template Studio → 6 Forge + Router → 7 Alpha Lab + tear sheets → 8 Behaviour Lab →
9 Families & Portfolio → 10 Court + Monte Carlo + factor exposure → 11 Learning engine →
12 Agent → 13 Learn screen and polish.
