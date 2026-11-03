"""Type pipelines: configurable, per-alpha-type research loops that spend real BRAIN quota.

- :mod:`.brain_adapter` — ``BrainSimulator``, the :class:`~..simulator.Simulator` Protocol
  implemented over alpha-harness's own engine (slots, packer, tracker), so the Researcher and
  the Hypothesis Court can run against BRAIN exactly as they run against the Gym.
- :mod:`.fake_engine` — a scriptable, in-memory ``Simulator`` test double for stage/budget
  tests that must never spend real quota (CLAUDE.md rule 4).
"""
