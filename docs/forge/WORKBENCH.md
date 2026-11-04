# /forge/workbench

One screen to go from "a market and some fields" to "simulations running and tagged in the
existing Tasks/Simulation Matrix/Alphas screens", without re-deriving anything the platform
already knows: scopes from BRAIN's own `OPTIONS /simulations`, field search from the Data
Explorer, slot-filling from Template Studio's nugget-binding, dispatch from the orchestrator and
Quota Governor. New code is thin plumbing between those; it never reimplements them.

## Part A — market bar, data panel, field tray

- **Market bar**: `ForgeScopePicker` + `useForgeScope()` (`screens/forge/scope/picker.tsx`),
  the same shared `'forge'` scope every other Forge screen uses — backed by `GET /api/forge/scopes`
  (BRAIN's `OPTIONS /simulations`, never a hardcoded list).
- **Data panel**: `FieldPicker` (`screens/forge/data/field-picker.tsx`) — reused as-is. It already
  gives "existing field set" vs. "search the catalog" (`/api/catalog/fields`, the Data Explorer's
  own search+filter) and a selection list with remove.
- **Field tray, persisted per scope**: a new tiny table, `forge_workbench_tray`
  (`forge/workbench/models.py`), one row per `(instrument_type, region, delay, universe)` holding
  `field_ids`. `GET/PUT /api/forge/workbench/tray` (`api/forge_workbench.py`) round-trips it. The
  screen loads the tray for the current scope on mount/scope-change and saves it (debounced) as
  the user ticks/un-ticks fields in the `FieldPicker`. This is deliberately separate from named
  Field Sets: the tray is the scratch selection for *this* session's work in *this* market, not a
  thing you'd browse in the Field Sets screen.

## Part B — expression editor and template drawer

- **Expression editor**: CodeMirror, autocomplete from two sources: BRAIN's real operators
  (already resolved somewhere for Template Studio/lint — reuse that operator list) and the
  field ids currently in the tray. Typing `ts_` or a tray field id completes; nothing is invented
  (no fake operators).
- **Template drawer**: `TemplatePicker` (`screens/forge/templates/template-picker.tsx`) plus slot
  filling two ways:
  1. **From the tray** — manual: assign tray fields to a template's typed slots (field/window/group),
     same shape Workshop already does by hand.
  2. **From a bound nugget** — reuse the Prompt-1 binding fix: `PreviewRequest{nugget_id}` in
     `forge_studio.py`, which resolves `Nugget.field_ids` through `resolved_treatment()` instead
     of the template's default `clean()`. The drawer offers "Fill from nugget" when a nugget
     exists for the current scope, loads `POST /api/forge/studio/preview` with `nugget_id` set.
- **Preview**: same `/api/forge/studio/preview` endpoint Template Studio uses — `blockers`/`notes`
  on the response are "real problems" (lint/eligibility against `config/forge/alpha_types/*.yaml`);
  the drawer only flags those, never a client-side guess.

## Part C — run and results

- **Dispatch**: candidates (one `RenderedAlpha` per filled template × field × region selected) go
  through the existing `PipelineRun`/`BatchEngine` + `QuotaGovernor` — the same path Pipelines
  uses, not a parallel one. Each run is tagged with a `task` of `"forge.workbench"` (the existing
  `SimulationRecord.task` column — no schema change), so it shows up in Tasks, the Simulation
  Matrix and the Alphas pool exactly like any other task's output; Workbench's own results table
  is just `/api/forge/*` reads filtered to that task, grouped client-side by template/field/region.
- **Tests run against `FakeSimulator`** (`forge/pipelines/fake_engine.py`), matching the orchestrator
  tests' pattern (`backend/tests/test_forge_pipelines_orchestrator.py`) — no real BRAIN call, ever,
  in a test.

## Route

`frontend/src/screens/forge/workbench/index.tsx`, route `/forge/workbench`, nav entry under
"Forge". Hard rules apply unchanged: nothing here submits an alpha; quota only via the Governor;
BRAIN scopes/operators only from the account's own schema.
