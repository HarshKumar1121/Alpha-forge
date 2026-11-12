# Osmosis Allocator — design

Status: design only. No code written. Review before any implementation.

Written for: the user (a CS student learning quant research), who will enter the final points on BRAIN by hand.

## 1. What this is, and what it is not

The Osmosis Allocator takes the submitted Alphas we already hold, decides which ones are
eligible for BRAIN's Daily Osmosis Rank, picks a small combination of them per region and
delay, and proposes how to split 100,000 points across that combination.

It produces a **plan** and a **CSV export**. It does not submit anything, does not call BRAIN
for anything except what the existing read paths already do, and does not spend simulations.
The points are typed into BRAIN's site by hand, consistent with hard rule 1.

It does **not** predict what BRAIN will pay. The screen reports combination statistics only
(out-of-sample Sharpe, drawdown, turnover, correlations). No currency, no payment estimate.

## 2. The eligibility gate (the first thing to get right)

Osmosis eligibility is **not** assumed. An Alpha is Osmosis-eligible only when its stored
submission checks contain an `OSMOSIS_ALLOCATION` entry whose result is `PASS`.

Evidence from the code:

- `backend/src/alpha_harness/brain/platform.py:145` lists `OSMOSIS_ALLOCATION` in
  `IGNORED_CHECKS`. Its comment (lines 133-137) says these checks label an Alpha rather
  than gate it, and that BRAIN reports them as `WARNING` on every Alpha it checks, passing or
  failing.
- `frontend/src/screens/alpha/messages.ts:263` maps `IS.OSMOSIS_ALLOCATION.WARNING` to
  "Daily Osmosis Rank not generated." So the WARNING text itself says the rank was *not*
  generated.

**Decision: eligibility requires `PASS`.** The brief said "not failing or pending". That
would let `WARNING` through, and the platform's own comment says `WARNING` is the usual
state for this check. Reading `WARNING` as eligible would mean assuming every submitted
Alpha qualifies, which the brief rules out. Treating `WARNING` as ineligible is the
conservative reading. It is listed as open question Q1 in section 12.

Every Alpha gets one of these statuses, shown with its reason:

| Stored result | Status | Eligible |
|---|---|---|
| `PASS` | eligible | yes |
| `PENDING` | not yet judged: run a Check Submission and wait | no |
| `FAIL`, `ERROR` | refused by this check | no |
| `WARNING` | BRAIN says the rank was not generated | no (pending Q1) |
| check absent | never checked, or checks not stored | no: "run Check Submission" |

## 3. Is the result already stored? (answer from `vault/store.py`)

**Yes, in the data format, with two caveats.**

- `backend/src/alpha_harness/vault/store.py:44-53` (`checks_json`) stores the Alpha's full
  check array with one exception: it drops only `QUOTA_CHECKS`
  (`REGULAR_SUBMISSION`, `D0_SUBMISSION`, defined in `platform.py`). `OSMOSIS_ALLOCATION` is
  kept with its `result`. The vault column is `alpha.checks`.
- `vault/store.py:557-565` (`save_checks`) writes a check array with a partial-column upsert,
  so a `/check` re-run updates `checks` without touching metrics.

**Caveat 1: a submitted Alpha's listing may not carry results.** The comment at
`backend/src/alpha_harness/vault/yields.py:110-113` says "the submitted Alpha's listing
carries only labels." For a submitted Alpha, `checks` may therefore hold names without
results until a Check Submission has been run and saved. `api/alphas.py:80` confirms the
design: checks "are the last Check Submission's when there was one." So the OSMOSIS result
exists in our store only for Alphas that were checked and saved.

**Caveat 2: the existing verdict ignores this check.** `gating_results`
(`vault/yields.py:99-104`) excludes `IGNORED_CHECKS`. The vault's submittable/pending/refused
verdict therefore says nothing about Osmosis. The allocator must **not** reuse `verdict()`
or `gating_results()` for eligibility. Adding `OSMOSIS_ALLOCATION` to a gating set would
change every Alpha's verdict, so we don't do that either.

## 4. Prerequisite changes (before any allocator code)

**P1. A dedicated status function.** A new pure function
`osmosis_status(checks) -> (status, reason)` in the new Forge package. It reads the stored
array and nothing else. No edit to `IGNORED_CHECKS` or `yields.py`.

**P2. Coverage read.** Count, per scope, how many submitted Alphas have an `OSMOSIS_ALLOCATION`
result stored. Show the count on the screen. If coverage is low, the screen says so and
names the fix ("run Check Submission on these N Alphas") instead of building a short plan.

**P3. A refresh path is a separate, user-triggered decision.** Filling missing results means
calling `/alphas/{id}/check` on BRAIN for each candidate. That endpoint re-runs submission
checks without submitting, which makes it outward-facing. Two things must be confirmed before
it is wired, and **the allocator itself never calls it**:
  - whether `/check` consumes any simulation quota (not confirmed from the code; hard rule 3
    applies if it does);
  - that the user wants the refresh triggered from the screen, one Alpha at a time, under
    BRAIN's rate-limit rules (hard rule 9).

No database schema change is expected: results fit in the existing `alpha.checks` JSON.
That is to be confirmed by reading a few stored rows in implementation.

## 5. Data (read-only, zero simulations)

- Series: `state.alphas.series(ids)` returns each Alpha's daily PnL and turnover,
  `dict[alpha_id -> dict[date, (pnl, turnover)]]`. The same read the portfolio endpoint uses
  (`api/portfolio.py:315`).
- Frame: `tools/portfolio.py:321` `frame(series, meta, ids, bps)` lays the Alphas out day by
  day as aligned arrays. Reuse it; do not write a second alignment.
- Membership: `state.alphas.submitted_members()` (used by `api/portfolio.py:309`).
- Metadata: `state.alphas.by_ids(ids)` for region and delay. Confirm in implementation that
  these two fields are present in its rows.
- **Every read calls `touch_pnl(state.db, ids)`** (`forge/storage/access.py:20`), so the
  storage TTL does not expire PnL the allocator just used. This matches the existing
  `# forge:` pattern in `api/portfolio.py:316`.
- **Zero simulations.** The allocator's module imports no simulation endpoint. A test injects
  a fake engine whose simulate call raises, and asserts it is never reached.

Grouping: by `(region, delay)`. Each scope is planned separately and sums to 100,000 on its
own. Alphas from different scopes are never combined, because they trade different
universes on different calendars.

## 6. Selection: greedy forward, inside each training window

Per scope:

1. Keep only eligible Alphas (section 2). If fewer than **10** remain, the scope is blocked
   and the screen says how many are missing. It does not fall back to a smaller set.
2. Greedy forward selection on the training window. Start empty. At each step add the
   Alpha that most increases the **equal-weight** combination's Sharpe on training data.
   This is the marginal-contribution step.
3. Pairwise correlation cap. A candidate whose daily-PnL correlation with any already-selected
   Alpha exceeds the cap (default 0.7, configurable) is skipped. 0.7 mirrors
   `SELF_CORRELATION_CEILING` in `platform.py`.
4. Stop at `max_selected` (default 10) or when no candidate adds Sharpe.

Selection runs **inside each walk-forward fold** (section 8), on that fold's training window
only. Selecting on the full history and then testing on it would leak the answer, and the
out-of-sample numbers would be inflated.

Selection uses TRAIN data only (hard rule 6). The selection statistic is never computed on a
test window.

## 7. Weighting methods to compare

Each method takes the selected Alphas' training-window PnL and returns weights that sum to 1,
long-only, subject to the per-Alpha cap and floor (section 9).

1. **Equal weight (EW).** `w_i = 1/N`. The baseline everything must beat.
2. **Inverse volatility.** `w_i ∝ 1/σ_i`, with σ_i the training-window standard deviation of
   each Alpha's daily PnL.
3. **Minimum variance, Ledoit–Wolf shrinkage.** Covariance Σ shrunk toward a constant-
   correlation target (Ledoit & Wolf, 2004). Minimise `wᵀΣw` over the simplex, long-only.
4. **Hierarchical risk parity (HRP).** Cluster Alphas on correlation distance
   `d = sqrt(0.5 (1 − ρ))`, reorder into quasi-diagonal form, split weight recursively by
   inverse cluster variance (López de Prado, 2016). Uses no expected returns at all.
5. **Max-Sharpe with shrunk means.** Shrink each Alpha's mean PnL toward the cross-sectional
   mean, use the shrunk Σ from method 3, set `w ∝ Σ⁻¹μ_shrunk`, then project long-only and
   normalise. If no positive solution exists, fall back to EW and log it.

**Why equal weight is a hard baseline.** Expected returns are estimated from a few years of
daily PnL, and that estimate is very noisy. Take an Alpha with a true annual Sharpe of 1.0,
observed over two years. The standard error of its estimated annual Sharpe is roughly
`sqrt((1 + SR²/2) / years)`, about **0.87** here. The estimate is 1.0 ± 0.87. Optimisers
that maximise on those estimates mostly fit noise, and then lose when the noise is not
there out of sample. DeMiguel, Garlappi & Uppal (2009) found that 1/N was hard to beat out
of sample for exactly this reason. Methods 1-4 avoid expected returns entirely. Method 5 keeps
them, but shrinks them hard. The comparison exists to test whether any of this estimation
buys anything for our data.

## 8. Validation: walk-forward

Rolling folds over the stored history, per scope:

- `train_days` (default 504, about two years), `test_days` (default 126, about six months),
  `step` (default 126). Each fold: select (section 6), fit each method on the training
  window, then **score each method on the following test window**, with weights held fixed
  across that window.
- Concatenate every fold's out-of-sample daily returns into one OOS series per method.
- Report, per method:
  - **OOS combo Sharpe** (annualised, √252, on the concatenated OOS series);
  - **max drawdown** on cumulative combo PnL (in the series' own unit);
  - **allocation turnover**: the sum of absolute weight changes between consecutive fold
    allocations, averaged over folds;
  - the fraction of folds where the method beat EW.
- Correlation between selected Alphas is shown alongside, for context.

**A method wins only if it beats equal weight out of sample.** Concretely: the difference
in OOS Sharpe (method minus EW) must have a 95% paired stationary-bootstrap confidence
interval whose lower bound is above zero. Otherwise **EW is the plan**, and the screen says
so in one line ("No method beat equal weight out of sample; using equal weight.").

Ties and near-ties go to EW. It is the simplest method, and it has the fewest ways to fail
on data we have not seen.

**The final plan** refits the winning method (or EW) on the most recent `train_days`, using
all the data up to the present. That is the allocation the user sees and exports.

**Low power is stated, not hidden.** With few Alphas and few folds, no method will show a
significant win. The screen reports the fold count and the bootstrap interval, so a "no win"
result reads as "not enough evidence", not as a verdict.

## 9. Output: integer points, sums to exactly 100,000

Per scope, the plan is an integer allocation whose points sum to **exactly 100,000**.

- Caps and floors are applied to the weights before rounding: each Alpha's weight lies
  between `floor` and `cap` (both fractions of the total, defaults 1% and 25%, configurable
  in YAML). Excess is redistributed to unconstrained Alphas until the constraints hold.
- Infeasible settings (for example `N × floor > 100,000` or `N × cap < 100,000`) raise an
  error that names the values. They do not silently break the cap.
- Rounding uses **largest remainder**: take `floor(w × 100,000)` for each Alpha, then give
  the remaining points one at a time to the Alphas with the largest fractional remainders.
  The sum is exactly 100,000 by construction.
- After rounding, a check asserts `sum == 100,000` and `floor ≤ points ≤ cap` for each
  Alpha. The check is a test and a runtime assertion.

## 10. Output screen and CSV

**Screen** (one entry under Tools, section 11): per scope, a table with Alpha ID, region,
delay, points, weight, and the check status. Beside it, the combination's OOS figures for the
chosen method and for EW, with a one-line caption on each number, per the UI conventions.

**CSV export**: one row per (scope, Alpha): `scope, alpha_id, region, delay, points,
weight, method, osmosis_status`. The points column is what the user types into BRAIN. The
export header carries the generation date and the data's last date.

The CSV does not include a payment estimate, a rank, or any figure BRAIN has not reported.

## 11. Where it lives (no new nav section)

New Forge code, in Forge directories only:

- `backend/src/alpha_harness/forge/osmosis/`: `eligibility.py` (P1), `selection.py`,
  `weights.py` (five methods), `walkforward.py`, `points.py` (rounding and caps), `plan.py`
  (CSV), `api.py` (router).
- `config/forge/osmosis.yaml`: `min_eligible`, `max_selected`, `corr_cap`, `train_days`,
  `test_days`, `step`, `cap`, `floor`, `bootstrap_samples`. Platform numbers stay in YAML
  (hard rule 7).
- `frontend/src/screens/forge/osmosis/`: the screen and its API client.

Upstream files touched (each marked `# forge:` or `// forge:`):

- `backend/src/alpha_harness/main.py`: one `include_router` line, next to the existing Forge
  routers at lines 211-218. A seam, not an edit to logic.
- `frontend/src/screens/tools/index.tsx`: one entry in the Tools list, marked `// forge:`.

## 12. Open questions for you

- **Q1. Is `WARNING` ineligible?** This design says yes (section 2). Confirm, or tell me what
  a `WARNING` on OSMOSIS means on BRAIN's site so I can encode it.
- **Q2. Does `/check` consume simulation quota?** Not settled from the code. It decides
  whether a refresh path is allowed under hard rule 3 (section 4, P3).
- **Q3. Train/test boundary.** The code does not define one for this purpose. The default
  here is a rolling `train_days` window, not a fixed date. Say if you want a fixed split.
- **Q4. Default caps.** Proposed: 1% floor, 25% cap, `max_selected` 10, correlation cap 0.7.
  These are my picks, not BRAIN's rules. Confirm them.
- **Q5. Scipy.** Min-variance with long-only constraints and HRP are easiest with a QP
  solver. `backend/pyproject.toml` has numpy and pandas but not scipy. Adding it needs your
  OK; otherwise the solver is written in numpy (more code, same results).

## 13. Scope and splitting (per CLAUDE.md, "more than ~15 files")

Counting new files, two upstream edits and the tests, this is about 20 files. CLAUDE.md asks
for a split. Proposed:

- **Part A (backend, offline):** `eligibility.py`, `selection.py`, `weights.py`,
  `walkforward.py`, `points.py`, `plan.py`, the config YAML, and tests. Built and tested with
  synthetic PnL and a fake engine, no API.
- **Part B (API and UI):** `api.py`, the `main.py` seam, the screen, the Tools entry, and
  API tests.

Part A can be reviewed on its own, and is where the statistics live. Nothing in Part B
changes any number.

## 14. Tests (offline, run before "done")

- Eligibility: each status row of the section 2 table, including `WARNING` and a missing
  check. Assert that only `PASS` is eligible.
- Gate uses the new status, not `verdict()`: an Alpha with OSMOSIS `FAIL` and all other checks
  `PASS` is not eligible.
- No simulation: the fake engine's simulate call raises; every allocator path must complete.
- `touch_pnl` is called on every ID read.
- Selection: the correlation cap is respected; blocked when fewer than 10 are eligible.
- Points: sum is exactly 100,000 for many random weight vectors; caps and floors hold;
  infeasible settings raise.
- Walk-forward: on synthetic data with a known winner, the winner is picked; on pure noise,
  EW is the plan.
- Honesty: the output contains no payment or rank field.

## 15. Sources

- Ledoit, O. & Wolf, M. (2004). A well-conditioned estimator for large-dimensional covariance
  matrices.
- DeMiguel, V., Garlappi, L. & Uppal, R. (2009). Optimal versus naive diversification: how
  inefficient is the 1/N portfolio strategy? *Review of Financial Studies*.
- López de Prado, M. (2016). Building diversified portfolios that outperform out of sample.
  *Journal of Portfolio Management*.

Source-code references are to this repository at `master`, as of this document.

## 16. Part A as built (implementation notes)

Decisions made while building Part A that the design above did not spell out:

- **Files:** `forge/osmosis/` holds `settings.py` (added: one dataclass that validates
  `config/forge/osmosis.yaml` and rejects unknown keys), `eligibility.py`, `selection.py`,
  `weights.py`, `walkforward.py`, `points.py`, `plan.py`, and `__init__.py`. Config is
  `config/forge/osmosis.yaml`. Tests are `backend/tests/test_forge_osmosis.py`.
- **Dependency:** `scipy>=1.18.1` added to `backend/pyproject.toml` (Q5). Ledoit-Wolf is
  implemented in numpy; scipy is used for the optimisers and HRP's clustering.
- **Caps are applied inside every method**, so the walk-forward scores exactly the weights the
  plan would use. The cap also forces a minimum combination size of `ceil(1 / cap)`, so a 25%
  cap needs at least four Alphas. Greedy selection keeps adding past zero marginal gain until
  that minimum is met; a scope with fewer usable Alphas is reported as blocked.
- **Fold membership** requires PnL on every day of a fold's training and test windows. It
  checks availability only, never performance, so a fold's choices do not depend on how it did.
- **Walk-forward input** is the eligible Alphas' PnL. Overlapping test windows (step < test
  days) are de-duplicated by date, so no day is scored twice.
- **Final plan** refits the winning method on the most recent `train_days`, using only Alphas
  complete over that window, then rounds with largest remainder.
- **Pure core:** Part A takes a PnL table and a status map as plain inputs. It does not read
  the vault, touch PnL storage, or call BRAIN. Those reads (and `touch_pnl`) belong to Part B.
- **Tests** enforce, on the source: no module in the package imports a BRAIN endpoint, client
  or simulator, and the selection never sees its own test window.
