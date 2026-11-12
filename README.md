# Alpha Forge

Alpha Forge extends alpha-harness (https://github.com/residual-lab/alpha-harness, MIT) with a research layer for WorldQuant BRAIN.

## Credits and license

alpha-harness is the upstream project, © MiracleInvoker, released under the MIT license. Its original README is kept at [docs/UPSTREAM_README.md](docs/UPSTREAM_README.md). The engine, the BRAIN client and the UI shell come from upstream. This fork's license is in [LICENSE](LICENSE), which keeps the upstream copyright notice.

## What this adds over upstream

- **Today** (`/forge/today`): the day's simulation plan, with planned and spent quota per lane. It is driven by the Quota Governor described below.
- **Analytics** (`/forge/analytics`): a local tear sheet built from daily PnL. It shows Sharpe after costs, Sharpe by year, a deflated Sharpe that corrects for how many variants were tried, a bootstrap confidence interval for Sharpe, and a permutation test. It is computed from PnL you already have.
- **Results Breakdown** (`/forge/breakdown/:taskId`): pass rates for a finished batch, grouped by field, dataset and setting. It also estimates the cost of repairing near-miss alphas and orders the submit candidates by correlation.
- **Select** (`/forge/select`): pairs each template with the fields you can use, and shows how well each template fits your field pool.
- **Submittable Board** (`/forge/board`): groups alphas into families and picks a leader per family by TRAIN Sharpe. It shows whether BRAIN reports each candidate as submittable, and for those that are not, why, with suggested repairs. Submittability comes from BRAIN's own check results, not a local guess.
- **Submit Helper** (`/forge/submit-helper`): orders the alphas you have chosen to submit by correlation, so overlap is visible before you submit. It does not submit anything.

## Design notes

- **No auto-submit.** No code path submits an alpha. Submission is manual on the BRAIN website.
- **Quota governor.** Every simulation is assigned to a lane defined in [config/forge/lanes.yaml](config/forge/lanes.yaml) and recorded in a ledger. The defaults are a daily limit of 5,000, a safety margin of 150, and a pacing cap of 650 per hour. If BRAIN's own limit is lower, BRAIN's figure wins. Cache hits cost no quota.
- **Train-only selection.** Ranking and selection use TRAIN-period metrics. Test-period metrics are shown once, labelled as test.
- **No real BRAIN calls in tests.** The test harness replaces the network socket so no test can reach BRAIN, and a fake engine simulates outcomes.
- **Storage TTL cache.** Cached BRAIN answers expire after 30 days (`CACHE_TTL_DAYS` in [backend/src/alpha_harness/forge/storage/cleanup.py](backend/src/alpha_harness/forge/storage/cleanup.py)), and the cleanup logic removes only rows past that limit.
- **PnL correlation is not BRAIN's production correlation.** The correlations this app computes from your PnL series are estimates. BRAIN's production correlation is the figure that matters for submission, so wherever BRAIN has reported one, that is the figure shown.

## Connecting to BRAIN

Enter your BRAIN email and password in the app's sign-in form. The backend signs in to BRAIN, caches the session cookies on this machine so a restart doesn't sign in again, and stores your password encrypted (AES-256-GCM, with a key file on this machine). With a stored password, the app signs in again by itself when the session expires. A refused login, or Sign out, stops that. If BRAIN asks for an identity check, the app opens BRAIN's page in a separate window. Passwords and cookies stay in the local database and are never written to the repo or the logs. The encryption protects against casual disclosure, not against someone who already has your user account.

## Requirements

- [uv](https://docs.astral.sh/uv/) (installs Python 3.14 for you)
- Node.js and [pnpm](https://pnpm.io/) (the repo pins pnpm 12.6.0 via `packageManager`)
- A WorldQuant BRAIN account
- [lefthook](https://github.com/evilmartians/lefthook), optional, for the `lefthook run dev` task runner

## Setup

```bash
git clone https://github.com/HarshKumar1121/alpha-forge.git
cd alpha-forge

(cd backend && uv python install 3.14 && uv sync)
(cd frontend && pnpm install)
```

## Run

With lefthook installed:

```bash
lefthook run dev
```

This starts the backend on port 8000 (uvicorn with `--reload`) and the frontend dev server (Vite). Open the URL Vite prints, usually http://localhost:5173.

Without lefthook, in two terminals:

```bash
cd backend && uv run uvicorn alpha_harness.main:app --reload --port 8000
cd frontend && pnpm dev
```

## Test and check

```bash
cd backend && uv run pytest -q     # backend tests
cd frontend && pnpm check          # biome, TypeScript and vitest
lefthook run check                 # all backend and frontend checks
```

## Configuration

- `AH_DATA_DIR`: the folder for the local database. It defaults to `~/.alpha-harness`. Set it in the environment, or in a `.env` file at the repo root.
- [config/forge/](config/forge/): lanes, alpha types, templates, court and learning settings. Platform rules live here as YAML, not in Python.

## Project layout

- `backend/src/alpha_harness/`: the FastAPI app. Forge code is in `forge/`.
- `frontend/src/screens/forge/`: the Forge screens.
- `config/forge/`: YAML rules and settings.
- `docs/forge/`: architecture and build notes.

## Screenshots

<!-- Screenshots to be added: Today, Analytics, Results Breakdown, Select, Submittable Board, Submit Helper. -->
