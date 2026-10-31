# Alpha Forge — instructions for Claude Code

## What this is
A personal daily-use alpha research platform for WorldQuant BRAIN, built by a CS student who is
learning to become a quant researcher. It is a fork of alpha-harness (MIT, © MiracleInvoker)
extended with alpha-copilot's research layer. Design: docs/forge/ARCHITECTURE.md. Build order:
Phases 0–2 of docs/forge/BUILD_PLAYBOOK.md, then docs/forge/BUILD_PLAYBOOK_V2.md (see Status /
roadmap). Two equal goals: find robust, diverse alphas within a 5,000/day simulation budget;
teach the user. Prefer readable code with docstrings that explain the quant reasoning.

## Status / roadmap
- **Done:** Phases 0–2 of BUILD_PLAYBOOK.md (base running, BRAIN validated, research brain
  ported with the Gym Playground, typed-slot templates), then v2's Phase 3 (Quota Governor and
  Today) and Phase 4 (real scopes from `OPTIONS /simulations`, Datasets tab with crowding and
  my history, dataset pages, field sets).
- **Current plan:** docs/forge/BUILD_PLAYBOOK_V2.md is authoritative from Phase 3 on. v1's
  Phases 3–13 are superseded; do not follow them. ARCHITECTURE.md is still the module design,
  but its §7 build order is v1's, and where it treats the Gym as a research stage (Behaviour Lab
  Gym mode, the agent's "run Gym experiments") v2 wins.
- **v2 in brief:** BRAIN is the simulator; scopes come live from `OPTIONS /simulations`, so only
  combinations the account can run are offered. Submittable means BRAIN says so
  (`/alphas/{id}/check`, self / power-pool correlation), never a local guess. The Gym becomes
  Practice mode: learning and free pre-screening only, never presented as a stand-in for BRAIN.
  One pipeline per alpha type (`config/forge/pipelines/*.yaml`). Hard rules below are unchanged.
- **v2 phases:** 3 Quota Governor + Today · 4 Real scope and data · 5 Template Studio v2 ·
  6 Type pipelines on BRAIN · 7 Families and Submittable Board · 8 Research analytics ·
  9 Learning engine · 10 AI agent with memory · 11 Practice mode and Real-Price Lab.
- **Next:** Phase 5 (Template Studio v2: templates bound to field sets, real eligibility).

## Layout
- backend/ — FastAPI, Python 3.14, uv. Package `alpha_harness` (keep the name; Forge code goes
  in `alpha_harness/forge/`). SQLite via SQLAlchemy async; DuckDB/Parquet for analytics.
- frontend/ — React 19, Vite, TypeScript, Tailwind 4, TanStack Router/Query, CodeMirror,
  lightweight-charts (time series), ECharts (added in Phase 4 for heatmaps/PCA/bars), pnpm.
- config/forge/ — lanes.yaml, alpha_types/, templates/, court.yaml, learning.yaml.
- reference/alpha-copilot — read-only source to port from. Never import from it at runtime.
- Follow alpha-harness's own DESIGN.md and tooling (ruff, basedpyright, biome, lefthook).

## Upstream
This repo tracks https://github.com/residual-lab/alpha-harness as remote `upstream`, branch
`main`. Upstream keeps improving the engine, the BRAIN client and the UI shell, and we want
those improvements — so every merge from it should be boring. Two habits keep it that way:

1. **New Forge code lives in Forge directories**, never mixed into upstream files:
   `backend/src/alpha_harness/forge/`, `frontend/src/screens/forge/`, `config/forge/`.
   A file upstream does not have cannot conflict.
2. **When an existing alpha-harness file must change**, keep the edit as small as it can be
   and mark it with a comment `# forge:` (Python) or `// forge:` (TypeScript) saying why.
   A conflict in a marked line is then a two-second decision instead of an archaeology
   problem, and `git grep "forge:"` lists the whole divergence from upstream.

Prefer a seam over an edit: a new module, a registered route, a config entry. Reach for a
change to an upstream file only when there is no seam, and say so in the marker.

## Commands
- Backend tests: `cd backend && uv run pytest -q`
- Frontend checks: `cd frontend && pnpm check`
- Dev: use the repo's task runner (`lefthook run dev`) or the commands Phase 0 identified.
- The user runs `lefthook run dev` and checks the UI in the browser. Do not start dev servers or
  headless browsers; verify with tests and curl against the running server instead.

## Hard rules
1. No code path may submit an alpha. Submission is manual on the BRAIN website.
2. Never retry a failed BRAIN sign-in automatically; never store or log passwords.
3. Every simulation goes through the Quota Governor with a lane. Never exceed the daily limit
   minus the safety margin. Cache hits cost zero and must be reused.
4. Never spend BRAIN simulations in tests; use the Gym client or a fake engine.
5. The AI agent may read and propose; it spends only from the reserve lane after the user
   clicks Approve; it never submits.
6. Selection and ranking use TRAIN metrics. Test-period metrics are shown once, labelled.
7. Platform rules live in YAML (alpha types, lanes, templates, thresholds), not in Python.
8. Secrets (cookies, keys, DBs) stay out of git and out of logs.
9. Respect BRAIN rate limits (Retry-After, 429). No tight polling.
10. Every feature ships with offline tests. Run them and show the result before saying done.

## UI conventions
Every number a user sees has a tooltip or caption explaining it in plain language; every chart
has a one-line "what this tells you". Empty states teach what to do next.
