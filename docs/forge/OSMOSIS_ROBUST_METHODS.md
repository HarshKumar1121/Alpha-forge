# Robust allocation methods for the Osmosis Allocator

Status: approved plan, Part A in progress. Extends `OSMOSIS_ALLOCATOR.md` and the commits
ab39f51, 4016f86, 0fa2688. This is an extension, not a rework. "Part A" and "Part B" refer
only to this work.

Written for: the user (a CS student learning quant research), who reads the screen and types
the points into BRAIN by hand.

## Candidates

Use the scope path's candidate set (`scope.py`): every submitted Alpha with stored PnL.
`OSMOSIS_ALLOCATION` stays display-only. Test on real data as well as synthetic data.

## New methods

Each is long-only, sums to 1, and respects the floor and cap.

1. **resampled_max_sharpe.** Stationary-bootstrap the training rows (block 21 days, reusing
   `stationary_bootstrap_indices`), draw `resample_count` times (YAML, default 200), run
   shrunk max-Sharpe on each draw, and average the weights. Fall back to equal weight if
   fewer than 50% of draws solve.
2. **nco.** Nested clustered optimisation (López de Prado, 2019). Cluster on correlation
   distance `sqrt(0.5 (1 − ρ))`, choosing k by silhouette score. Inside each cluster, use
   min-variance. Across clusters, use min-variance on the cluster-level returns. Expand to
   Alpha weights. Fall back to equal weight if any level fails.
3. **max_diversification.** Maximise `(wᵀσ) / sqrt(wᵀΣw)` on the shrunk covariance, long-only.
   Fall back to equal weight.
4. **bayesian_blend.** Black-Litterman style.
   - Prior: `π = λ · Σ · w_erc`, where `w_erc` are equal-risk-contribution weights,
     reverse-optimised.
   - Views: `P = I`, `Q_i = shrunk Sharpe_i · σ_i / sqrt(252)`.
   - Omega (diagonal): `Ω_ii = τ · σ_i² · (1 + κ · s_i²) / T_i`, where `s_i` is the
     consistency score and `T_i` the days of history.
   - Posterior: `μ_BL = [(τΣ)⁻¹ + Ω⁻¹]⁻¹ [(τΣ)⁻¹ π + Ω⁻¹ Q]`.
   - Weights: proportional to `(λΣ)⁻¹ μ_BL`, then projected and bounded. The formula goes in
     the docstring.
   - `λ`, `τ`, `κ` live in `osmosis.yaml`, are tuned on synthetic data only, and are never
     tuned on the holdout.
   - Fall back to equal weight if the weights are not all positive.
5. **ensemble.** Mean of the bounded weights of all non-fallback methods, **excluding equal
   weight** (the baseline). Fall back to equal weight if there are no inputs.

## Inputs

- **PnL basis.** A setting in `osmosis.yaml`, default `after_cost_5bps`. Use it for a scope
  only when every candidate Alpha in that scope has it; otherwise use gross for the whole
  scope. Show the basis per scope on screen.
- **Consistency score.** The standard deviation of half-year (126-day) Sharpes, requiring at
  least 4 half-years, computed only from each fold's training window (hard rule 6). Neutral
  below that. Shown as a column, and it feeds `bayesian_blend`'s confidence.
- **Max drawdown** of each method's out-of-sample combined PnL: a headline column in the
  comparison.

## Validation

Unchanged rule. Every method, old and new, goes through the two-stage walk-forward: choose on
the earlier folds, then one check on the holdout. A method becomes a scope's default only if
it passes the holdout check against equal weight. Expect more scopes to end on equal weight
with 11 candidates; that is the correct outcome.

## Performance

Cache the walk-forward and the final-plan refit (`scope.py`, around line 274) per scope, keyed
on the stored-data revision. A cold load of 20–25 s across all scopes is acceptable only with
a visible "Calculating..." message showing progress per scope. Warm loads must be fast. Add
timing logs.

## Tests (offline)

- Every method, including fallback paths: sums to 1, respects floor and cap.
- Resampled stability: lower weight variance than plain max-Sharpe across independent
  synthetic datasets from one model. If this fails, report it; do not tune until it passes.
- NCO on two clean synthetic clusters splits risk about 50/50.
- max_diversification beats equal weight on its own diversification ratio.
- Ensemble equals the mean of its inputs and excludes fallbacks and equal weight.
- bayesian_blend returns the prior with no views; views dominate with long histories.
- Consistency and weights use training data only (extend the existing test).
- Cache: a second analysis does not refit (count calls via monkeypatch).
- No simulation endpoint reachable (existing test, unchanged).

## Split

- **Part A (backend, offline):** new methods module, inputs module (PnL basis, consistency),
  settings and YAML, `weights.py` registration, tests.
- **Part B (wiring and screen):** `scope.py` caches, drawdown and basis fields, `api.py`,
  regenerated types, the screen's new columns, tooltips and progress message.

## Decisions taken while building Part A

- **Files.** `forge/osmosis/robust.py` (the five methods), `forge/osmosis/inputs.py` (PnL
  basis, consistency), `settings.py` and `config/forge/osmosis.yaml` (new keys), `weights.py`
  (METHODS registration; the shared solver `solve_raw` now feeds both the originals and the
  robust methods), and two test files. Tests: `backend/tests/test_forge_osmosis_robust.py`.
- **Import order.** `robust.py` imports from `weights.py`; `weights.py` imports `robust.py`
  lazily inside `solve_raw`, to avoid a cycle. Solvers that robust needs were made public
  (`min_variance_solution`, `max_sharpe_solution`, `equal_risk_contribution_solution`).
- **Consistency neutral value is 0.** A neutral score adds no extra uncertainty, since
  `Omega` uses `(1 + kappa * s^2)`. Below `min_half_years` the score is 0, not a penalty.
- **Bayesian blend fallbacks.** Falls back to equal weight if equal-risk-contribution has no
  solution (the prior is undefined), or if any posterior weight is not positive. Projection is
  not applied on top of the positivity rule, because the plan's fallback rule decides.
- **NCO cluster count.** `k` ranges over 2 .. p-1, chosen by silhouette. When the correlation
  structure has no groups, every `k` is degenerate and NCO falls back. Found on synthetic data:
  an exchangeable model gives identical pairwise distances after Ledoit-Wolf shrinkage.
- **Ensemble inputs** are every method except equal weight and the ensemble itself, each bounded
  first; a method that fell back is left out.
- **Hyperparameters are starting values.** `bl_risk_aversion` 2.5, `bl_tau` 0.05, `bl_kappa` 1.0
  are standard Black-Litterman starting points. They have not been tuned on synthetic data yet,
  and that tuning has not been run.
- **Walk-forward cost.** Adding methods grows each fold's work (resampled max-Sharpe runs 200
  fits per fold, and the ensemble recomputes its inputs). In the test fixtures `resample_count`
  is 20, so the existing plumbing tests stay fast; production stays at 200. Part B's cache
  addresses the cold load.
