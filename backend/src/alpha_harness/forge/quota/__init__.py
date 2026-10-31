"""The Quota Governor: how the day's BRAIN simulations are shared out, paced and recorded.

- :mod:`.config` — ``lanes.yaml``, validated, and which lane pays for a task.
- :mod:`.models` — the ``quota_ledger`` and ``lane_state`` tables.
- :mod:`.ledger` — writing spends and cache hits, and the day's sums.
- :mod:`.governor` — the arithmetic: budget, linear release, admission and pacing.
"""
