# Selection Workflow — audit and design

Status: **Select built** (PR 1: `POST /api/forge/templates/fit` and its pairing reasons; PR 2:
the `/forge/select` screen, its nav entry, and the `/forge/studio` redirect retarget). Every
file/line reference below was read on 2026-11-12; nothing is assumed from older reports.

Built differences from this spec, so the doc does not claim more than the code does:
- Market bar uses the shared Forge scope (`useForgeScope`), not its own `alpha-forge-select` key,
  and also shows instrument type (`ForgeScopePicker`). Changing the market clears the pool.
- Field table and pool are `FieldPicker` (search, crowding, my pass-rate per field, Load field
  set, Save as set). The dataset and category pass-rate columns (§3.2) and the Load nugget
  button (§3.3) are not built yet.
- Pairing line uses `POST /api/forge/nuggets/suggest` (advisory, §3.3).
- Candidate cards show counts per slot, not dataset names: the fit response carries field ids
  only. Dataset ids are looked up through `catalog.fields` on hand-off, for the variables.
- Nav: Select replaces the Workbench entry. The Workbench route stays until open question 3 (§8)
  is decided.

## 0. Verdict

Build one new screen, **Select** (`/forge/select`, `screens/forge/select/`), and have it hand
off into the existing Template Lab editor instead of replacing it.

Template Lab already wins the core job — scope → fields → template → bind → preview → run → Tasks
— against every Forge screen that tried to do the same thing (§2). What it structurally cannot
do is let you see, before spending a simulation, **how crowded a field is, how often your own
alphas on that field/dataset/category have passed BRAIN's checks, and which templates your
chosen fields can actually fill**. Forge already computes all of that; it is simply not on the
path you take when you pick. Select puts it on that path and then drops you into the
unmodified Template Lab with the template text, scope and slot bindings pre-filled. The run
itself is Template Lab's own call, so Tasks and Results Breakdown work without change.

The four concrete advantages, each tied to data that exists today (verified in §3):

| # | Advantage | Where the data already is |
|---|---|---|
| 1 | Per-field **coverage, crowding, my pass-rate** in the picker | catalog rows carry `coverage`; `POST /api/forge/data/fields/extras` returns `crowding` + `mine` |
| 2 | **Dataset and category pass-rate** beside each field | `GET /api/forge/data/datasets` rows carry `mine` + `category_id` |
| 3 | **Templates filtered to what your fields can fill, with a reason** | `bindings()`/`field_problem()` in `forge/research/templates.py` — needs one thin new endpoint (§4) |
| 4 | **Pairing sanity of the pool before you bind** | `POST /api/forge/nuggets/suggest` → `suggest_pairings()` |

If after reading §1–§2 you disagree that these are worth a screen, the honest fallback is: keep
using Template Lab and remove Workbench. Nothing in this doc is required to keep Forge working.

---

## 1. Audit: what Template Lab does today

Files: `frontend/src/screens/research-labs/template/{index,state,gallery,blocks,variables,editor,api}.tsx|ts`,
`backend/src/alpha_harness/api/template_lab.py`. Route `/labs/template` (`shell/nav.ts:47`).

### 1.1 The workflow, click by click

1. **Market** — `ScopePicker` at the top: Region chips, Delay chips. Universe is **not** here;
   it lives at the bottom under "Simulation Settings → Universe" as a *searched* multi-select
   (`index.tsx:552-566`), so the universe is a search dimension, not a scope choice.
   `useMarketScope(region, delay)` (`index.tsx:86`) resolves the catalog scope. A market that
   is not downloaded shows a warning; `region=agnostic` shows the 4×-cost notice.
2. **Template** — the gallery (`gallery.tsx`) lists built-in templates and your saved ones as
   cards: expression with `$holes`, plus Facts (operator/field budget, hole count, Power
   Pool/Single-Dataset badges). Click → `open(templateId, name, text, variables)`
   (`state.ts:105`). Or type from blank. Code view (CodeMirror with autocomplete, hover docs,
   inline diagnostics) and Blocks view (visual tree) edit the same `text`.
3. **Variables** — `VariablesPanel` lists every `$name` found in the text. A name with no
   definition starts from `defaultOf()` (`variables.tsx:33-50`): a preset list if it matches
   one, otherwise *the fields of whichever dataset/subcategory/category it is named after*.
   `VariableEditor` lets you set it as **Values** (free text list) or **Fields**: dataset
   chips (`DatasetChips`) + Matrix/Vector toggle. The stored shape is
   `{kind:'fields', dataset_ids, filter?: FieldFilterState, vector_operators?}` (`api.ts:10`).
4. **More Filters** — navigates away to the full Data Explorer (`index.tsx:221`,
   `navigate({to:'/data'})`); there you tick datasets and optionally individual fields; Done
   returns via `useDatasetPick.take('/labs/template')` and writes the variable with
   `dataset_ids` and `filter` (which can include explicit `field_ids`, `data/state.ts:113`)
   (`index.tsx:188-211`).
5. **Preview** — debounced `POST /api/template-lab/preview` → `_plan()`
   (`template_lab.py:540`): per-variable field counts from `field_pool()`
   (`labs/search.py`), Search Space size, Sample Alphas, per-location problems. Free.
6. **Settings** — Universe/Neutralization/Investability (searched), Decay, Truncation,
   Pasteurization, NaN handling, Test period; Cores and Simulations.
7. **Add to Tasks** — `POST /api/template-lab/tasks` (`template_lab.py:755-784`): server
   re-runs `_plan`, refuses on any problem, then `add_study(sampler=TEMPLATE_SAMPLER, …)`.
   The result is a numeric `Study` id, listed by `GET /api/lab-tasks`, shown in Tasks, and
   `tasks/results.tsx:266` links it to `/forge/breakdown/$taskId`.

### 1.2 What it does well

- The editor: grammar-checked Code + Blocks views in sync, live problems at the exact
  location, free server-side preview of the real search space. Nothing in Forge matches this.
- `$name` defaults from dataset names are a genuinely fast way to write a sweep.
- One button to Tasks; the Study is a resumable Optuna sweep with pause/stop/edit.
- Saved templates with dirty-state and name-collision handling.

### 1.3 What is clunky or missing

| Gap | Where it bites |
|---|---|
| **No field intelligence.** No coverage, crowding, or your pass-rate anywhere in the picker; `DatasetChips` shows dataset names only. | You choose a dataset blind, then learn from the preview only *how many* fields matched. |
| **Field choice is by dataset, not by field.** Picking individual fields means leaving the screen for the Explorer (`/data`) and coming back. | Context switch every time you want "these 4 fields", which is the normal case for a deliberate run. |
| **Template list is flat.** Gallery is not filtered by the market or by any fields you have in mind; a template that needs a Vector field in a Matrix-only dataset only fails at preview. | You discover the mismatch after choosing, not before. |
| **Universe is buried** under Simulation Settings as a multi-select. | A first-time run often has no universe ticked and `_plan` reports nothing until you scroll down. |
| **One template at a time.** The draft store holds a single `text`. | Comparing two templates on the same fields means two full passes. |
| **No history.** Nothing tells you this template, or fields from this dataset, have passed/failed for you before. | The platform has that data (`forge/data/usage.py`), the screen does not show it. |

---

## 2. Why Workbench, Template Studio and the old Pipelines screen went unused

Read in full: `screens/forge/workbench/{index,api,expression-editor}`, `forge/workbench/*`,
`screens/forge/templates/*`, `screens/forge/studio/*`, `forge/studio/*`, `screens/forge/pipelines/*`,
`forge/pipelines/{orchestrator,brain_adapter}.py`, `api/forge_templates.py`, `api/forge_studio.py`.

### Workbench (`/forge/workbench`, `router.tsx:234`, `nav.ts:152`)

1. **Two field selections that do not talk.** The "Field tray" at the top and the embedded
   Template-Lab panel's own field source below are separate state; ticking tray fields never
   narrows the slots below. You pick twice or wonder why the first pick did nothing.
2. **Still blind.** The tray shows no coverage/crowding/pass-rate, although the same fields
   get all three one screen over in the Datasets tab.
3. **Template `Select` is flat**, filtered by scope + category only, `name (complexity)`,
   never by the fields in the tray, no fit reason.
4. **Three field sources** (tray / field set / nugget) with no guidance on which to use.
5. **Its own run path**: runs are `forge_workbench_run_item` rows projected into Tasks by
   `screens/forge/tasks-bridge.ts` as rows with a string `taskId` (`workbench/api.ts:26`), no
   Run/Pause/Stop, and **no numeric Study id — `get_breakdown(task_id: int)`
   (`forge_breakdown.py:147-149`) cannot open them**, although Tasks still renders the button.
6. The comparable part (template + fields → run) sits below Market, Regression and Tray
   panels — three scrolls before you reach what Template Lab shows first.

Net: more concepts and clicks than Template Lab, less editor, and a run that lands in Tasks
as a second-class row. That is why it lost.

### Template Studio (`/api/forge/studio/*`, `screens/forge/studio/*`)

It is not a screen. It is the preview/description/my-templates CRUD API used by the Template
Library's "Bind to field set" sheet; `router.tsx:172` redirects `/forge/studio` to Workbench.
It never runs anything — its only exit is "Send to Pipelines". There was nothing to use.

### Old Pipelines (`/forge/pipelines`, `router.tsx:191`)

1. **Pipeline first, fields last.** You must pick a pipeline config (which fixes the alpha
   type and allowed template complexities) before you see a single field or template.
2. **Template ticks are filtered by complexity only**, not by whether your fields can fill
   them; a ticked template can plan to 0 candidates with no explanation (`/compatible` exists
   at `forge_templates.py:320` but this screen never calls it).
3. **Batch ceremony** (plan → lint → approve → background run) is right for a nightly factory,
   wrong for "run this one idea now".
4. Runs are `forge_pipeline_run` rows: same string-id projection as Workbench, same no-Breakdown.

### Common root cause

All three re-implemented selection *and* running in parallel to Template Lab, and all three
made the run a Forge-side record instead of a lab Study. The user ends up with a worse editor,
a worse Tasks row, and none of the field intelligence that would have justified the detour.

---

## 3. The Select screen

Route `/forge/select`. Nav entry under the Forge section (`shell/nav.ts`, replacing the
Workbench entry at `nav.ts:152`). Layout is one column, top to bottom, no tabs.

### 3.1 Market bar

Three chip groups: **Region**, **Delay**, **Universe**. Values come from the same scope data
`forge/data/scope.py` builds from `OPTIONS /simulations`, so only legal combinations are
offered. Default: the last market used on this screen (persisted in `localStorage` key
`alpha-forge-select`), else `DEFAULT_SCOPE` (`lib/scope.ts`). Universe is one value here
(it is the catalog scope the numbers below are read in); Template Lab's multi-universe
search stays available after hand-off and starts pre-ticked with this one (§3.5).

Right side of the bar: a quota line read from `GET /api/forge/quota` (`forge_quota.py:156`):
"Today: 1 240 of 4 850 used · reserve lane 310 left". Tooltip: "Template Lab runs are charged
to the reserve lane (lanes.yaml `default_lane`)."

Changing any chip clears the candidate pool (§3.3) and the template panel (§3.4).

### 3.2 Field picker

A search box and a results table, identical in query to the Data Explorer's Fields tab:
`queries.fields(scope, FieldFilter)` via the existing `POST /api/catalog/fields`
(`api/catalog.py:354`), sorted by
`alpha_count` desc by default, 100 per page. Filter chips above the table: **Dataset**
(multi, `DatasetChips` from `screens/data/dataset-chips`), **Category**, **Type**
(Matrix / Vector), **Coverage ≥** (0.5 / 0.8 / 0.95 presets → `coverage_min`),
**Region exclusive** toggle. These are the `FieldFilter` fields the catalog already honours
(`catalog/queries.py:95-130`); no new query.

Columns, one row per field. Every cell has a tooltip; the exact source is the contract:

| Column | Shown as | Source |
|---|---|---|
| ☐ | add to pool | — |
| Field | `anl4_eps_mean` + description, Type badge | catalog row `field_id`, `description`, `field_type` |
| Dataset | `analyst4` · category name | catalog row `dataset_id`, `category_name` |
| Coverage | `87%` bar | catalog row `coverage` (same cell as `data/fields.tsx:173`) |
| Users / Alphas | `412 · 3 108` | catalog row `user_count`, `alpha_count` |
| Crowding | 0–1 bar, `0.62`, tooltip "ranked against every field in USA·TOP3000·D1" | `POST /api/forge/data/fields/extras` (`forge_data.py:340`) → `FieldExtra.crowding`, computed by `crowding_scores()` (`forge/data/crowding.py:72`) over the whole scope |
| My pass-rate (field) | `3/5 passed` or `—` when `checked == 0`; tooltip "alphas of mine reading this field that BRAIN has fully checked" | same call → `FieldExtra.mine` (`MyHistory`: `sims, alphas, checked, passed, pass_rate`), from `load_usage()` → `Usage.fields[field_id]` (`forge/data/usage.py:66-68`) |
| My pass-rate (dataset) | `9/14` | `GET /api/forge/data/datasets` (scope) → `ForgeDataset.mine` for `dataset_id`; one call per market, cached by TanStack Query |
| My pass-rate (category) | `21/40` | client-side sum of `mine.checked` / `mine.passed` over all `ForgeDataset` rows sharing `category_id`; same response, no extra call |
| Pyramid | `×1.6` or `—` | `ForgeDataset.account_multiplier` (falls back to `pyramid_multiplier`) |

`fields/extras` accepts ≤500 ids (`FieldExtrasRequest`), called once per table page with
that page's ids — the same pattern the Data Explorer uses. `—` is rendered for `None`,
never `0`. Rule 6: `mine` counts only BRAIN-checked alphas (`MyHistory.checked`), so no test-period
metric is involved.

No probe data on this table: the probe API only runs a probe (`POST /api/forge/probe/field`),
there is no list-by-field read to show, and running one spends simulations. Probes stay on
their own screen.

### 3.3 Candidate pool

A sticky strip under the table: chips `anl4_eps_mean ×`, count, **Clear**, and two loaders:
**Load field set…** (`fieldSets.list()` for this market, `screens/forge/fieldsets`) and
**Load nugget…** (`nuggets.list()` in `screens/forge/nuggets/api.ts`). Loading adds that set's
field ids to the pool; rows for ids not on the current table page are fetched with
`fields_by_id` (`catalog/queries.py:381`, returns `coverage`, `dataset_id`, `field_type`) so
every chip can show the same tooltip numbers as a table row.

**Hard cap: 50 fields.** The 51st add is refused with "Select is for a deliberate pick; for
whole-dataset sweeps bind a dataset in Template Lab." The cap matches the one real limit on
this path: `POST /api/forge/nuggets/suggest` refuses more than 50 ids (`forge_nuggets.py:97`),
and the pairing check is the only step that scales with the pool (O(n²) over at most 1,225
pairs). It is not a cost of §3.4's fit check: `bindings()` walks `permutations(pool, slots)`,
but built-in templates have at most two field slots, so a 50-field pool is about 2,450
tuples. User templates have no slot cap, so `/fit` declines (with a note) any walk above
`FIT_WALK_BUDGET` rather than running it.

**Pairing check**, shown as one line under the chips once the pool has ≥2 fields:
`POST /api/forge/nuggets/suggest` with `ProfileRequest{scope: {instrument_type, region, delay,
universe}, field_ids}` (`forge_nuggets.py:95`, max 50 ids — the 40 cap fits), backed by
`suggest_pairings()` (`forge/nuggets/suggest.py:35`, pure, O(n²)). Output: "All pairs look sound", or
"⚠ `anl4_eps_mean` and `anl4_eps_mean_1q` look like the same measure twice" with the
suggestion's `reason`. Advisory only — nothing is blocked.

### 3.4 Template picker — "Templates your pool can fill"

Empty state until the pool has one field: "Add fields above; templates are ranked by what
those fields can fill." Then:

**Data: one new endpoint, `POST /api/forge/templates/fit`** (§4). It reuses exactly what
`/compatible` does today (`forge_templates.py:320-349`: transient `FieldSet` → `resolve()` →
`field_infos()`), then for every template from `merged(user_rows)`
(`forge/studio/user_templates.py:130` — built-ins plus your own as `Template` objects;
`by-scope` reads only `load_library()`, so `/fit` must not copy that):

```
fits: bool                         # bindings(t, fields, require_archetype=False) non-empty
note: str|null                     # set only when the walk was declined (FIT_WALK_BUDGET); fits=false then
pairingProblem: str|null           # pairing_problem(): why same_dataset_as slots cannot pair, from candidates alone
slots: [{ name, kind: field|window|group, type, archetype,
          candidates: [field_id…],  # fields where field_problem(slot, f) is None
          problem: str|null }]      # first field_problem() text when candidates == []
```

`field_problem()` (`templates.py:488-505`) already returns the human reason: *"field X is
MATRIX; this slot needs a VECTOR field"* or *"field X behaves unknown; this slot takes
level, change fields"*. With `require_archetype=False` an unprofiled BRAIN field (archetype
`None`) passes the archetype rule, so in practice only the Matrix/Vector rule bites — same as
`/compatible` today. `bindings()` runs only when every field slot has ≥1 candidate, so the
permutation walk is skipped for the common failing case.

**Card**, one per template, same visual shape as Template Lab's gallery card (expression with
`$holes`, Facts line), plus:

- **Fit line** (always present). Four distinct states, keyed in this order: `note` first,
  then `fits`, then `problem`, then `pairingProblem`. They must look different: a declined walk is not the same
  claim as "this template doesn't fit your fields", and must never render as if it were.
  - **State A — fits=true** (`note` null): green check, "Fits". Line: *"Fills all 2 field
    slots from your pool — `$est` ← 3 fields (analyst4), `$act` ← 2 fields (fundamental6)."*
    Counts are `len(slot.candidates)`, dataset names from the candidates' `dataset_id`.
  - **State B — fits=false, problem reason** (`note` null, some slot has `candidates: []`):
    red cross, "Doesn't fit yet". Line: *"`$vol` has no match — field anl4_eps_mean is
    MATRIX; this slot needs a VECTOR field."* (the failing slot's `problem`). This is a
    claim about the pool: the fields do not have what the template needs.
  - **State C — fits=false, note set** (walk declined, `FIT_WALK_BUDGET`; every slot has
    candidates): neutral grey "Not checked", not red and no cross. Line is the `note` text,
    verbatim: *"N field slots over M fields is too many combinations to check here; use
    fewer fields or fewer slots."* No count of candidates is shown as a match, and the card
    is not sorted into "Doesn't fit yet" — it sits in its own group "Not checked (n)"
    below the fitting cards and above "Doesn't fit yet". Its footer button stays enabled
    only if the user selects it anyway; the hand-off must not imply it was verified.
  - **State D — fits=false, every slot has candidates, the walk ran, no joint binding**
    (`note` null). Amber icon, neutral rather than red, since no single field is wrong on
    its own. Label: **"Needs matching fields"**. The line is `pairingProblem` verbatim (from
    `pairing_problem()`, `forge/research/templates.py`) when it is set, one of two messages:
    - *"X and Y must come from the same dataset — none of your candidate pairs for these two
      slots share one"* — every candidate records its dataset, and none match.
    - *"X and Y must come from the same dataset, but your fields don't record which dataset
      they come from, so the match can't be confirmed"* — some candidate has no dataset id,
      so the match cannot be confirmed. This is kept separate so the card never claims the
      datasets differ when they are simply unknown.
    - `pairingProblem` also covers **hint-based statistic pairing**, not only
      `same_dataset_as`. When a slot pair's hints name opposite halves of a known statistic
      pair (mean/std, estimate/actual, high/low, numerator/denominator), no candidate pair
      may disagree on the statistic or the name stem. Example, `analyst_dispersion` with
      `["anl4_eps_std", "anl4_afv4_eps_high"]` (both analyst4): the line is *"none of your
      fields has one of these words in its id (average/avg/mean), as slot Y needs"*. Y is
      the mean slot and neither field names a mean. A stem mismatch (pool `[anl4_eps_std, anl4_rev_mean]`) reads *"no average/avg/mean
      field and deviation/dispersion/sd/std/stdev field in your pool share a name stem (slots
      Y/X pair fields by what they measure, not just dataset)"*.
    - When `pairingProblem` is null, the line is the generic stopgap: *"Each slot has
      candidates, but no combination of your fields satisfies the template's dataset and
      pairing rules together."* This now only happens when no same-dataset or hint pair is
      blocked yet the walk still finds no joint binding. The known case is a pool with fewer
      distinct fields than slots, which is a deferred v1 gap (CLAUDE.md, Still deferred).
- **Badges**: `Built-in` / `Mine`, `dataset_category` (from `ScopeTemplate.dataset_category`),
  complexity, `angle`.
- **Rationale** collapsed under "why this shape" (`Template.rationale`).

Ordering: fitting templates first (State A), by number of candidate fields across slots desc;
then a collapsed divider **"Not checked (n)"** for State C; then a collapsed divider
**"Needs matching fields (n)"** for State D; then a collapsed divider **"Doesn't fit yet (n)"**
for State B with its problem line. Four groups, in that order. Nothing
is hidden — the Pipelines failure (silent zero candidates) is made visible.

Filters above the cards: **Category** chips (from `GET /api/forge/templates/by-scope`'s
`dataset_categories`), **Complexity**, **Show only mine**.

No per-template "your pass-rate with this template" in v1: `template_stats` is not populated
(CLAUDE.md "Still deferred"), and `forge_advisor.preflight`'s `similar` list is empty for the
same reason. When that table is filled, the card gets one more line from
`TemplateStats(template_id, scope)` — the model already exists.

Selecting a card is a radio (one template per hand-off). The footer button enables.

### 3.5 Hand-off into Template Lab

Footer: **"Open in Template Lab →"**, with a one-line estimate to its left:
"Search space: 6 fields × 3 windows = 18 expressions" (product of `len(candidates)` per field
slot × `len(values)` per window/group slot, computed client-side from the `/fit` response).

Click does, in this order, with **no change to `state.ts`** — every write is the store's own
public API:

1. If `useTemplateLab.getState().dirty` is true: confirm dialog "Template Lab has unsaved
   changes to '<name>'. Replace them?" Cancel aborts.
2. `useTemplateLab.getState().open(null, template.name, template.expr, variables)`
   (`state.ts:105`) where `variables` is built per slot:
   - field slot → `{ kind:'fields', dataset_ids: unique dataset_id of its candidates,
     filter: { field_ids: candidates, field_types: slot.type ? [slot.type] : [] },
     vector_operators: slot.type === 'VECTOR' ? [slot.aggregate] : [] }`
     (`FieldSlot.aggregate` always holds a vector operator, default `vec_avg`,
     `templates.py:101-107`).
     This is byte-for-byte the shape the Data Explorer pick already writes
     (`index.tsx:198-209`, `data/state.ts:113`); `_plan` → `field_pool(narrow=filter)` →
     `queries.fields` honours `field_ids` (`catalog/queries.py:175`), so the preview searches
     exactly the chosen fields, not the whole dataset.
   - window/group slot → `{ kind:'values', values: slot.values.join(', ') }`.
3. `useTemplateLab.setState({ region, delay, universes: [universe], picking: null, view: 'code', dirty: true })`.
4. `navigate({ to: '/labs/template' })`.

The store is `persist`ed (`alpha-harness-template-lab`, version 4) and `followOtherTabs`, so
the draft survives reload and is visible in another tab. From here the user is in the
unchanged Template Lab: preview shows the search space, Sample Alphas, settings, Cores &
Simulations, Add to Tasks.

### 3.6 Run → Tasks → Results Breakdown

Nothing new. "Add to Tasks" is `POST /api/template-lab/tasks` → `add_study()` → numeric Study
id → `GET /api/lab-tasks` → Tasks row (a real lab task with Run/Pause/Stop/Edit) →
`tasks/results.tsx:266` "View results breakdown" → `/forge/breakdown/$taskId` →
`GET /api/forge/breakdown/{taskId}`. The Study is never projected through `tasks-bridge.ts`.

- **Lane**: the engine task name is Template Lab's own; `config/forge/lanes.yaml` has
  `task_lanes: {}` and `default_lane: reserve`, so the run is charged to **reserve**, exactly
  like every Template Lab run today. `QuotaGovernor.pace()` (`governor.py:341`) gates dispatch.
  If you would rather these runs draw from `forge_regular`, that is one YAML line in
  `task_lanes` (rule 7) — an open question in §8, not part of this screen.
- **Cache hits**: identical requests dedup on the engine's request hash before `pace()` and
  are recorded via `ledger.record_cache_hit()` at cost 0; Select never talks to BRAIN.
- **Rule 1**: no submit anywhere on the path.

---

## 4. Work required

### Backend (one endpoint, one router file)

`POST /api/forge/templates/fit` in `api/forge_templates.py`, next to `/compatible`.
Body: `{instrument_type, region, delay, universe, field_ids: list[str] (1–50)}`.
Response: `FitTemplates{ templates: [FitTemplate{ id, name, source: builtin|user, category,
dataset_category, complexity, angle, rationale, expr, fits, note, slots: [FitSlot{ name, kind,
type, archetype, aggregate, values, candidates, problem }] }], missing, errors }]`. `missing`
lists picked ids the catalog lacks for this market; `errors` lists your templates that no longer
parse; `note` is set only when `fits` is False because the walk was declined.
Implementation: `forge/research/templates.py` (`slot_fit`, `fits_pool`, `FIT_WALK_BUDGET`) and
`POST /api/forge/templates/fit` in `api/forge_templates.py`, sharing the catalog resolve with
`/compatible`. Built-ins and your own templates (`merged()`) are both returned. Tests:
`tests/test_forge_templates_fit.py`, offline, on the fake catalog (rules 4, 10).

Nothing else. Coverage, crowding, pass-rates (field/dataset/category), pairing, quota, by-scope
categories, field sets, nuggets, Template Lab tasks, Results Breakdown: all existing routes,
unchanged.

### Frontend

- `screens/forge/select/{index.tsx, api.ts, field-table.tsx, pool.tsx, template-cards.tsx}`.
- Route `/forge/select` in `router.tsx`; nav entry replacing Workbench's (`nav.ts:152`).
- Retarget the `/forge/studio` redirect (`router.tsx:172`) to `/forge/select` carrying
  `t` (template id) and `fs` (field set id), so an old `/forge/studio` link opens Select with
  that pool and template preselected. (Template Library's "Bind to field set" is a preview
  sheet on the page, not a link, so it is unchanged.)
- Tests: `src/test/routes.test.tsx` route entry, component tests with `api-mock.ts`.

Touches ≈ 10 files.

---

## 5. Worked example (what a session looks like)

1. Open **Select**. Market bar: USA · D1 · TOP3000 (remembered). Quota: "reserve 310 left".
2. Type `eps`; tick Category `Analyst`. Table shows 38 fields. Sort by **My pass-rate
   (dataset)** desc — `analyst4` reads `9/14`, `analyst11` reads `0/6`.
3. Tick `anl4_eps_mean`, `anl4_eps_std`, `anl4_eps_high` (coverage 91/91/88 %, crowding
   0.71/0.44/0.39, field pass-rate 2/3, —, 1/1). Clear the search; tick Category
   `Fundamental`; add `fnd6_eps` (coverage 97 %, crowding 0.88, 4/9).
4. Pool strip: 4 chips. Pairing: "⚠ `anl4_eps_mean` and `fnd6_eps` look like an
   estimate/actual pair across datasets — that is a recognised pairing." (valid, so informational).
5. Templates: 5 fit. Top card *"Estimate surprise, decayed"*: `$est` ← 3 fields (analyst4),
   `$act` ← 1 field (fundamental6), `$win` 3 values. Fit line reads as above. "Doesn't fit yet
   (6)" lists e.g. *"`$flow` needs a VECTOR field"*.
6. Footer: "Search space: 3 × 1 × 3 = 9 expressions". Click **Open in Template Lab →**.
7. Template Lab opens with the expression, `$est`/`$act` bound to exactly those fields,
   `$win = 21, 63, 126`, USA/D1, TOP3000 ticked. Preview: 9 combinations. Set Simulations =
   9, Add to Tasks.
8. Tasks shows the Study; after it runs, "View results breakdown" opens as for any lab task.

---

## 6. Overlap and deprecation

- **Workbench** — remove once Select ships: every reason it was abandoned (§2) is either fixed
  by Select or made moot by handing off to the real editor. Move `RegressionLabPanel` into the
  Regression Lab screen it shares a name with; keep the `forge_workbench_run_item` table and
  its `tasks-bridge.ts` projection until old runs are no longer wanted, then drop both.
- **Template Studio API** — unchanged; still used by Template Library's bind sheet.
- **Template Library** (`/forge/templates`) — unchanged; it is the read-only reference.
  Its "Bind to field set" is an in-page preview sheet and stays as it is; Select is reached
  from the nav, and old `/forge/studio` links forward to it.
- **Old Pipelines** — unchanged by this doc; it is the batch factory, a different job.
- **Template Lab** — unchanged, and now the only place a template is edited or run from a pick.

---

## 7. Non-goals for v1

- Multi-template batch from one pool (needs Template Lab to hold several drafts — a change to
  the editor, not to this screen).
- Per-template historical pass-rate (blocked on populating `template_stats`).
- Any new crowding or pass-rate math; any change to Quota Governor, engine, or Breakdown.
- Running from Select directly. Deliberately: a second run path is exactly what sank Workbench.

---

## 8. Open questions for approval

1. Lane for Select-originated runs: stay on `reserve` (today's Template Lab behaviour) or add
   a `task_lanes` entry pointing them at `forge_regular`?
2. Pool cap — resolved: 50, matching the pairing endpoint's limit (§3.3). Not from `learning.yaml`.
3. Remove Workbench in the same PR as Select, or one PR later?
