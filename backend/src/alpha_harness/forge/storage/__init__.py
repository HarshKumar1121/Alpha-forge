"""Forge-owned storage housekeeping: a last-accessed tracker and cache/PnL TTL logic.

Exists so ``~/.alpha-forge/harness.db`` and ``catalog.duckdb`` do not repeat the old
alpha-harness install's growth (56 GB from permanent daily data). Nothing here deletes a row:
this package only decides what *would* be expired (:mod:`.cleanup`) and records that a row was
looked at (:mod:`.access`). The actual delete-and-compact job reads these decisions; it does not
duplicate them.
"""
