"""The Osmosis Allocator's numbers, read from ``config/forge/osmosis.yaml``.

Every planning value lives in YAML (CLAUDE.md rule 7), so you can tune the caps and windows
once you have seen real output. The defaults are the starting values from
``docs/forge/OSMOSIS_ALLOCATOR.md``. Unknown keys are an error, not a silent default: a typo in
``cap`` must not quietly plan with 100% caps.
"""

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any

from ..config import config_dir, read_yaml

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Every knob the allocator reads. Fractions are of the total points, not of PnL."""

    #: Eligible Alphas a scope needs before a plan is made. Below this, the scope is blocked.
    min_eligible: int = 10
    #: The most Alphas one combination may hold. Greedy selection stops here.
    max_selected: int = 10
    #: Largest daily-PnL correlation allowed between two selected Alphas (absolute value).
    #: 0.7 mirrors ``SELF_CORRELATION_CEILING`` in ``brain/platform.py``.
    corr_cap: float = 0.7
    #: Extra Sharpe a candidate must add, beyond the best so far, to be kept once the
    #: combination has reached its minimum size.
    min_sharpe_gain: float = 0.0
    #: Training window, in trading days (504 is about two years).
    train_days: int = 504
    #: Out-of-sample window scored after each training window, in trading days.
    test_days: int = 126
    #: How far the window rolls forward between folds, in trading days.
    step: int = 126
    #: Fewest scored folds a scope needs before a plan is made. Fewer is too little evidence.
    min_folds: int = 4
    #: The most recent scored folds, held out of method selection and used only for the CI check.
    holdout_folds: int = 2
    #: Largest share of the total any one Alpha may hold (0.25 = 25,000 of 100,000 points).
    cap: float = 0.25
    #: Smallest share any selected Alpha must hold (0.01 = 1,000 of 100,000 points).
    floor: float = 0.01
    #: The points each scope must sum to exactly.
    total_points: int = 100_000
    #: Resamples for the paired bootstrap that decides whether a method beats equal weight.
    bootstrap_samples: int = 2000
    #: Mean length, in days, of a stationary-bootstrap block. Blocks keep the autocorrelation
    #: of daily PnL intact, which a plain resample would destroy.
    bootstrap_block: float = 21.0
    #: How far the Max-Sharpe method pulls each Alpha's mean toward the group mean (0 = not at
    #: all, 1 = every Alpha gets the same mean).
    mean_shrink: float = 0.5
    #: Seed for the bootstrap, so the same data always gives the same verdict.
    seed: int = 7
    #: Trading days per year, for annualising Sharpe ratios.
    periods_per_year: int = 252


def from_mapping(raw: dict[str, Any]) -> Settings:
    """Build and validate :class:`Settings` from a parsed YAML mapping."""
    known = {f.name for f in fields(Settings)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"osmosis.yaml has unknown keys: {unknown}")
    settings = Settings(**raw)
    _validate(settings)
    return settings


def load_settings(path: Path | None = None) -> Settings:
    """Read ``config/forge/osmosis.yaml`` (or ``path``) into validated settings."""
    source = path if path is not None else config_dir() / "osmosis.yaml"
    if not source.exists():
        raise FileNotFoundError(f"missing allocator settings: {source}")
    return from_mapping(read_yaml(source))


def _validate(s: Settings) -> None:
    if not 0 < s.floor < s.cap <= 1:
        raise ValueError(f"need 0 < floor < cap <= 1, got floor={s.floor}, cap={s.cap}")
    if not 0 < s.corr_cap <= 1:
        raise ValueError(f"corr_cap must be in (0, 1], got {s.corr_cap}")
    if s.max_selected < 2 or s.min_eligible < 2:
        raise ValueError("max_selected and min_eligible must each be at least 2")
    if s.train_days < 2 or s.test_days < 1 or s.step < 1:
        raise ValueError("train_days >= 2, test_days >= 1 and step >= 1 are required")
    if s.holdout_folds < 1 or s.min_folds < s.holdout_folds + 2:
        raise ValueError(
            "holdout_folds must be >= 1, and min_folds must be at least holdout_folds + 2 "
            f"so the selection folds are not empty; got min_folds={s.min_folds}, "
            f"holdout_folds={s.holdout_folds}"
        )
    if not 0 <= s.mean_shrink <= 1:
        raise ValueError(f"mean_shrink must be in [0, 1], got {s.mean_shrink}")
    if s.bootstrap_samples < 100 or s.bootstrap_block < 1:
        raise ValueError("bootstrap_samples must be >= 100 and bootstrap_block >= 1")
    if s.total_points <= 0 or s.periods_per_year <= 0:
        raise ValueError("total_points and periods_per_year must be positive")
