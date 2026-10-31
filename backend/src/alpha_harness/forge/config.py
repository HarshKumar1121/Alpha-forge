"""Where Forge's rules and files live.

Rules are YAML under ``config/forge`` (CLAUDE.md rule 7), read once per process: the planner
consults them inside per-candidate loops, and re-parsing a file each time is pure waste.
Tests that need different rules point ``AH_FORGE_CONFIG_DIR`` elsewhere and clear the
caches with :func:`reload`.

Forge's own files go under the app's data directory, which honours ``AH_DATA_DIR`` — never a
``~/.alpha-harness`` shared with a plain alpha-harness install.
"""

import os
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from ..config import Settings

#: Repo-root ``config/forge``. This file sits one level deeper than ``alpha_harness/config.py``.
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[4] / "config" / "forge"


def config_dir() -> Path:
    configured = os.environ.get("AH_FORGE_CONFIG_DIR")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_CONFIG_DIR


def data_dir() -> Path:
    """Forge's files inside the app's data directory. Not created here."""
    return Settings().data_dir / "forge"


def read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must hold a mapping at the top level")
    return data  # pyright: ignore[reportUnknownVariableType] - YAML is untyped by nature


@cache
def court() -> dict[str, Any]:
    """``court.yaml``: the Court's tests, field archetype thresholds, planner shares, repairs."""
    return read_yaml(config_dir() / "court.yaml")


@cache
def lanes() -> dict[str, Any]:
    return read_yaml(config_dir() / "lanes.yaml")


def reload() -> None:
    """Forget every cached config, so the next read sees the files as they are now."""
    from . import alpha_types
    from .research import templates

    court.cache_clear()
    lanes.cache_clear()
    alpha_types.load_all.cache_clear()
    templates.load_library.cache_clear()
