# Alpha Forge build playbook (VS Code + Claude Code)

Each phase ends with a tool you can use. Do the phases in order and don't skip ahead: later
phases depend on the data the earlier ones collect.

## How to run each phase

1. In VS Code, open the `alpha-forge` folder and open the Claude Code panel. Or open a terminal
   in that folder and run `claude`.
2. Start a fresh conversation for each phase by typing `/clear`.
3. Press Shift+Tab until it shows **plan mode**. Paste the prompt. Read the plan, ask
   questions, then approve it.
4. When it finishes, check it yourself:
   `cd backend && uv run pytest -q` and `cd frontend && pnpm check`. Then open the app and look
   at what changed.
5. Commit: `git add -A && git commit -m "phase N: …"`.
6. Ask: *"Explain what you built in this phase like I'm a CS student learning quant research.
   Then give me 3 questions to check I understood."* This is how the tool teaches you.

If something breaks, paste the error into Claude Code with: *"Find the root cause, fix it, add
a regression test."*

---

## Phase 0: Setup (terminal, once)

```bash
# tools (macOS)
brew install uv pnpm node lefthook git
# put the three things you have in Downloads together:
#   alpha-harness (zip or folder), alpha-copilot (folder), alpha-forge-blueprint.zip
cd ~/Downloads
bash -c "$(unzip -p alpha-forge-blueprint.zip scripts/setup_forge.sh)"
cd ~/quant/alpha-forge && code .
```

The setup script copies alpha-harness to `~/quant/alpha-forge`, adds alpha-copilot under
`reference/`, unpacks this blueprint, installs Python 3.14 and the dependencies, and makes the
first git commit.

Then, in Claude Code (no plan mode needed):

```
Read CLAUDE.md, then docs/forge/ARCHITECTURE.md, then alpha-harness's own README.md and
DESIGN.md. Skim backend/src/alpha_harness and frontend/src, and reference/alpha-copilot
(README + src/alpha_copilot/research). Don't change anything. Tell me in plain language:
(1) how alpha-harness is structured and how to run it in dev, (2) which alpha-copilot
modules map to which Forge modules, (3) the risks you see in the build order. Then list the
exact commands I should run to start backend and frontend in dev.
```

## Phase 1: Run the base and validate BRAIN (0 simulations)

```
Goal: get alpha-harness running locally in dev and prove the BRAIN connection, without
spending simulations.
1. Start backend and frontend in dev using the repo's own task runner (lefthook / uv / pnpm)
   and tell me the URL to open.
2. I will sign in through the app's own sign-in screen. Don't handle my password.
3. Add backend/scripts/smoke_brain.py: read-only checks (session valid, operators, one
   dataset's fields for USA/1/TOP3000, my ACTIVE alphas, one PnL recordset). Print a pass/fail
   table. It must never POST /simulations.
4. Fix any failures and add offline regression tests with the real response shapes (no
   personal data).
Acceptance: app opens, I can sign in, smoke script all PASS, tests green.
```

## Phase 2: Port the alpha-copilot research layer (library only, no UI yet)

```
Goal: bring alpha-copilot's research brain into the backend as a tested library.
Create backend/src/alpha_harness/forge/ and port from reference/alpha-copilot/src/alpha_copilot:
expr.py (parser + operator counting), profiles.py → alpha_types (load config/forge/alpha_types),
research/profiler.py, research/templates.py (upgrade it to the TYPED-SLOT format in
config/forge/templates/*.yaml: field/window/group slots, archetype filters, same_dataset_as,
different_dataset_from, vector aggregate), research/planner.py, research/families.py,
research/court.py (config from config/forge/court.yaml), analytics.py, and local/ (Gym engine,
sim, gym, client).
Rules: follow alpha-harness conventions (Python 3.14, ruff, basedpyright strict, its
db/session patterns). The planner and Court must run against an abstract "simulator" interface
so that the Gym client AND the alpha-harness engine can both back them (write that interface;
an engine-backed adapter comes in Phase 6). Port alpha-copilot's tests and make them pass.
Add a test that loads every template YAML, fills its slots, and asserts it parses.
Acceptance: `uv run pytest -q` green; a short script runs the Court and the planner on the Gym.
```

## Phase 3: Quota Governor (start using the tool daily from here)

```
Goal: the 5,000/day budget becomes a first-class system.
Implement from ARCHITECTURE.md §5.1 and config/forge/lanes.yaml:
- quota_ledger and lane_state tables; every simulation the engine starts is recorded with a lane
  (default lane 'reserve' for manual sims from existing screens).
- Admission control before work enters the engine; pacing (max_per_hour, quiet hours); daily
  reset at the configured local time; read BRAIN's remaining quota from response headers if
  present and trust the lower number.
- Fixed allocation now (bandit comes in Phase 11) with unused quota flowing to busy lanes.
- API: GET /forge/quota (today's plan vs spent per lane), POST /forge/quota/plan (dry-run a
  plan's cost).
- UI: a "Today" screen as the new home. Quota gauge (spent/5000), per-lane bars (planned vs
  spent), hourly spend sparkline, and a warning when the safety margin is near.
Tests: admission, pacing, reset, cache hits cost 0, never exceeding the limit under concurrency.
```

## Phase 4: Data Explorer and field sets

```
Goal: find the right data by hand and save it.
- Extend the alpha-harness catalog screen into "Data Explorer": filters for region, delay,
  universe, dataset category, field type, coverage range, crowding (userCount/alphaCount),
  pyramid multiplier, my own alpha count per dataset, text search; sortable, virtualised table.
- A crowding score (documented formula) and an "under-explored but usable" quick filter.
- Field sets: create from selection or from saved filters (dynamic), name + notes, list/edit/
  delete; API under /forge/fieldsets.
- Dataset page: field table, coverage histogram, crowding vs pyramid-multiplier scatter
  (ECharts — add it as a dependency), my history in this dataset.
Every chart gets a one-line caption explaining what it tells a researcher.
Tests for filters, crowding score and field-set CRUD.
```

## Phase 5: Template Studio

```
Goal: browse, write and judge templates.
- Load built-in templates from config/forge/templates/*.yaml; user templates stored in DB with
  versioning and YAML export.
- Studio screen: library list (filter by category, complexity, alpha-type fit), template
  detail with rationale, and an editor using alpha-harness's CodeMirror setup with live lint:
  syntax, slots detected, operators unknown to my account, operator/field counts, nesting depth,
  odd lookbacks, and computed badges for which alpha types it fits (from alpha_types YAML).
- "Try it" panel: fill the slots with a field from a field set → show the rendered
  expressions → run on the Gym (free) and show the local tear sheet.
- template_stats table (filled from Phase 6 onward): yield by dataset category, archetype,
  alpha type; median train Sharpe; families started.
Tests for slot parsing, constraint checks, type-fit computation and versioning.
```

## Phase 6: Forge and Router (the main research loop)

```
Goal: field sets × templates × settings → smart, budgeted experiments; every result tagged
with every type it fits.
- Engine adapter implementing the Phase 2 simulator interface on alpha-harness's
  slots/packer/tracker, with a lane id on every child.
- Forge screen: choose field set(s), templates (or "auto-match by archetype"), settings grid,
  target alpha types, lane, budget. Show the plan: candidates → after lint → after cache →
  after pruning → rounds (successive halving) with sims per round. Approve → plan runs under
  the Governor.
- Planner rounds as in ARCHITECTURE.md §5.5 (surrogate ranking is a stub that uses
  template_stats until Phase 11).
- Router: after every finished alpha, evaluate it against every alpha_types YAML and store the
  tags (Regular / Power Pool / ATOM / ARC tier …) in alpha_tags.
- Runs screen: live plan progress over WebSocket (rounds, spent, passes).
Tests with a fake engine: budget never exceeded, cache reuse, router tags, plan resume after
restart.
```

## Phase 7: Alpha Lab, tear sheets and the daily book

```
Goal: understand every alpha and see each day's work grouped.
- Alpha Lab table: filter by day, lane, plan, template, dataset, type tag, family, gate status.
- Tear sheet page: cumulative PnL with drawdown shading, rolling 1-year Sharpe, yearly Sharpe
  bars, turnover, checks table, train vs test (shown once, labelled), type tags, template +
  rationale, field behaviour, similar alphas. Use lightweight-charts for time series and ECharts
  for bars.
- Daily book on the Today screen: yesterday's and today's results grouped
  day → lane → type → family, with counts and the best leader per group.
- Notes and labels per alpha; a manual submission checklist (no submit API).
```

## Phase 8: Behaviour Lab

```
Goal: understand data behaviour before spending template sims.
- Field profile (3 probes, explore lane, cached forever) with archetype, angle bars, probe PnL
  overlay and plain-language notes.
- Dataset fingerprint: stratified sample of k fields chosen from a budget slider; archetype mix
  chart; sign consistency.
- Redundancy map: probe-PnL correlation heatmap with hierarchical-clustering order, and a
  "keep one per cluster" action that writes a pruned field set.
- PCA of probe PnLs: scree plot and the number of components explaining 80% of variance.
- Gym mode toggle showing raw-data statistics for learning.
Tests: clustering, PCA on synthetic data, budget respected.
```

## Phase 9: Families and portfolio

```
Goal: never submit near-duplicates; know what each alpha adds.
- Nightly family clustering (learning.yaml thresholds) over recent passing alphas; collisions
  with my submitted pool flagged.
- Families screen: family cards (leader, members, template mix), correlation heatmap with
  dendrogram, PCA of the pool.
- Portfolio: incremental Sharpe of each family leader vs my submitted pool; greedy submission
  order; the submission checklist pre-filled in that order.
- Decay monitor for submitted alphas: live vs backtest Sharpe by template and lane.
```

## Phase 10: Court, Monte Carlo and factor exposure

```
Goal: industry-grade validation.
- Court screen: write a hypothesis (title, mechanism, sign, horizon, field set) → trial in the
  validate lane → case file UI (each test PASS/FAIL/SKIP with severity, numbers and the
  objection) → verdict → "promote survivors" creates a Forge plan.
- Monte Carlo in forge/analytics: block bootstrap Sharpe CI, sign-flip / circular-shift
  permutation null, P(Sharpe > x), drawdown distribution; charts (histograms with the actual
  value marked).
- Factor exposure: build known-factor alphas once (cached), regress each alpha's PnL on them
  → betas, R², residual Sharpe; show it on the tear sheet as "what's really new".
Tests on synthetic PnL with known answers.
```

## Phase 11: Learning engine

```
Goal: the tool learns from my history and spends better each week.
- Lane bandit (Thompson sampling) per learning.yaml: reward = robust alpha starting a new
  family, per sim; bounded by lane bounds; daily rebalance; posterior chart on Today.
- Surrogate model (scikit-learn HistGradientBoosting) predicting pass probability and train
  Sharpe from the features in learning.yaml; nightly retrain; calibration curve and feature
  importance on a Learning screen; the Forge ranks with it and keeps a 15% exploration slice.
- Template evolution in the template_research lane: mutations from learning.yaml, tested on a
  panel of datasets, generalisation fitness, results shown as proposed templates in the Studio.
- Weekly lesson mining over failures → lessons table.
Tests: bandit on simulated arms, surrogate leakage check (no test-period features), evolution
respects operator limits.
```

## Phase 12: AI research agent

```
Goal: a research partner embedded in every screen.
- Agent panel (right side, toggle with a hotkey) using alpha-harness's LLM provider layer.
- Roles: researcher, critic, analyst, tutor — prompts ported from reference/alpha-copilot
  agent/prompts.py and adapted to Forge's tools.
- Tools: catalog search, field/dataset profiles, template library + stats, lint, Forge estimate,
  Gym runs, read alphas/tear sheets/families/ledger, journal and lessons, propose hypothesis /
  template / plan (shown as approval cards).
- Permissions: read and propose freely; spend only from the reserve lane after I click Approve;
  never submit. Show tokens and cost per session.
- Context-aware: the agent knows which screen and which alpha/template I'm looking at.
Tests: tool permissions (a spend without approval is refused), tool schemas, offline tool calls.
```

## Phase 13: Learn screen and polish

```
- Learn screen: port the curriculum, spaced repetition and Gym from alpha-copilot; add an
  options primer (Black-Scholes, Black-76, Bachelier implementations with interactive charts)
  as interview preparation.
- Global command palette (cmdk) actions for every screen; keyboard shortcuts; empty states that
  teach; dark/light themes.
- Weekly report: what the bandit learned, best templates, dataset discoveries, lessons.
- Optional: package as a desktop app with alpha-harness's Tauri setup.
```

---

## One-off prompts you'll use often

- `BRAIN changed <rule>. Update config/forge/alpha_types and tests, and explain the change.`
- `Add a template for <idea> with typed slots and a rationale; lint it for every alpha type;
  try it on the Gym; show me the tear sheet.`
- `Today's plan spent its budget with few passes. Analyse the ledger and alpha tags and tell me
  where the simulations went and what to change in lanes.yaml.`
- `Review this week's commits as a senior engineer and as a senior quant. What's risky?`
- `Walk me through <file> line by line and quiz me.`
