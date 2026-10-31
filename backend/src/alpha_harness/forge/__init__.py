"""Alpha Forge's research layer, ported from alpha-copilot.

Everything here is a library: parsing and linting Fast Expressions, alpha-type rules from
``config/forge``, field profiling, typed-slot templates, the budgeted planner, the Hypothesis
Court, and the Gym — a synthetic market with planted effects for learning for free.

Two seams keep it independent of where simulations actually run and where results are kept:
:mod:`.simulator` (the Gym now, alpha-harness's engine in Phase 6) and :mod:`.store`
(in memory now, the database once the phase that owns the tables lands).
"""
