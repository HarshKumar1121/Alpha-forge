# Storage Plan

## The problem

Three things grow without bound on a daily-use BRAIN research loop, and together they were
measured at 56GB on the user's real `~/.alpha-forge/` database:

- `brain_cache` (SQLite) — every BRAIN answer (`alpha:`, `performance:`, `pnl:`, `check:`,
  `correlation:*:`) is cached forever once written, so a year of daily simulation runs never
  loses a byte.
- `alpha_pnl` (DuckDB) — one daily PnL series per Alpha ever simulated, kept even for Alphas
  abandoned on day one and never looked at again.
- `Trial.distributions` (SQLite, via Optuna-backed Gym studies) — what each trial drew its
  categorical choices from; a field searched over a few thousand fields stores them all, on
  every trial, measured at 127 KB a trial (`db/models.py:462`). A study with a few thousand
  trials alone accounts for a large share of the total.

None of this is wrong to cache in the first place — BRAIN rate-limits aggressively and a
correlation sweep that re-asks a question it already has the answer to is both slow and risky.
The problem is that nothing ever expires it.

## The split: (a) and (b)

- **(a) — decisions and the read-side signal.** Pure functions that decide what is safe to
  expire and why, plus the one piece of state the decision needs that doesn't exist anywhere
  else: when an Alpha's PnL was last actually read. No deletes. This is `forge/storage/`.
- **(b) — the job that acts on (a).** A scheduled (or manually triggered) cleanup task that
  calls (a)'s `dry_run_report` first, writes the permanent check-verdict records, then deletes
  exactly the rows (a) named — plus the `Trial.distributions` TTL, which (a) does not cover yet.
  Not started.

Splitting this way means the riskiest part (actually deleting rows from the user's real
database) ships last, and only after the decision logic underneath it has tests and a dry run
the user has reviewed.

## (a) — what's built, in `backend/src/alpha_harness/forge/storage/`

- **`models.py`** — two small SQLite tables on alpha-harness's own `Base`:
  - `StorageAccess` — one row per Alpha ever read, `last_accessed_at`. This is the "idle" clock
    for `alpha_pnl`, which has no per-row metadata of its own in DuckDB.
  - `StorageCheckHistory` — a compact, permanent PASS/FAIL/WARNING record per check, written
    before a `check:` cache entry is allowed to expire, so the verdict survives even once the
    full BRAIN response is gone.
- **`access.py`** — `touch_pnl(db, alpha_ids)`, called from every place that reads an Alpha's
  PnL for a specific Alpha: `api/vault.py`'s single-alpha endpoint, `api/lab_tasks.py`'s bulk
  correlation reads, `api/forge_nuggets.py`. (`api/portfolio.py` is named in the module's own
  docstring as a fourth call site but does **not** call `touch_pnl` yet — not wired, worth
  fixing before part (b) ships, or idle Alphas still open in Portfolio could be miscounted as
  idle.)
- **`cleanup.py`** — the TTL decisions themselves, no deletes:
  - `brain_cache`: a row expires after `CACHE_TTL_DAYS = 30`, *unless* it's a `check:` or
    `correlation:*:` entry for an Alpha that's either on the Submittable Board's widest window
    right now (`BOARD_LOOKBACK_DAYS = 14`, matching `forge_board.Days`'s own range) or was
    itself fetched within `PROTECT_RECENT_DAYS = 7` — BRAIN rate-limits correlation checks
    hourly, so a page that just asked must not be made to ask again a minute later.
  - `alpha_pnl`: an Alpha's series expires once it is both never submitted
    (`date_submitted IS NULL`) and idle past `PNL_IDLE_DAYS = 30`, using `StorageAccess` (falling
    back to `fetched_at` if never read since rollout). A submitted Alpha is never a candidate —
    Portfolio and the correlation checker need it always.
  - `check_history_rows` / `CacheExpiry.preserve` — the permanent-record half: called on a
    `check:` entry right before it's deleted, so part (b) can write `StorageCheckHistory` rows
    first and never lose a verdict.
  - `dry_run_report` — runs the whole decision against the live databases and counts what
    *would* be deleted, deleting nothing. This is what part (b) calls first, and what the user
    reviews before any real cleanup run.
- **`trial.distributions`** — the 14-day retention window (expire a Trial's stored
  `distributions` 14 days after the trial finished, once the study itself is no longer being
  actively replayed) is a **decision, not yet implemented**. `cleanup.py` only covers
  `brain_cache` and `alpha_pnl` today; the Trial TTL is part (b)'s to add, alongside the actual
  delete job.
- **Tests** — `backend/tests/test_forge_storage.py`, 11 cases, offline (CLAUDE.md rule 10: no
  BRAIN calls — SQLite + DuckDB only). All pass in isolation.

## Status

- **Part (a): built and tested, not yet committed.** `forge/storage/` and
  `test_forge_storage.py` are untracked; the three call-site edits (`api/vault.py`,
  `api/lab_tasks.py`, `api/forge_nuggets.py`) are modified-but-uncommitted. `api/portfolio.py`
  still needs its `touch_pnl` call added.
- **Part (b): not started.** No cleanup job, no `Trial.distributions` TTL, no wiring into a
  scheduled task or an admin-triggered endpoint, no actual delete path.
- A separate, unrelated batch of uncommitted work — the Regression Lab
  (`forge/research/regression.py`, `regression_data.py`, `api/forge_regression.py`,
  `api/forge_probe.py`, `api/forge_submit_helper.py`, their tests, and the `main.py` router
  registrations for all three) — sits alongside this in the working tree but does not import or
  touch anything in `forge/storage/`. It belongs in its own commit.
