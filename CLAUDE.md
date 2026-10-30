# Alpha Forge — instructions for Claude Code

## What this is
A personal daily-use alpha research platform for WorldQuant BRAIN, built by a CS student who is
learning to become a quant researcher. It is a fork of alpha-harness (MIT, © MiracleInvoker)
extended with alpha-copilot's research layer. Design: docs/forge/ARCHITECTURE.md. Build order:
docs/forge/BUILD_PLAYBOOK.md. Two equal goals: find robust, diverse alphas within a
5,000/day simulation budget; teach the user. Prefer readable code with docstrings that explain
the quant reasoning.

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
