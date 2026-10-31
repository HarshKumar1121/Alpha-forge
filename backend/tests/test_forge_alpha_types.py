"""Alpha types from YAML: inheritance, offline lint, gates, score tiers, config locations.

The inheritance, lint and gate tests are ported from alpha-copilot ``tests/test_core.py``.
"""

from typing import TYPE_CHECKING

import pytest

from alpha_harness.forge import config
from alpha_harness.forge.alpha_types import gate, lint, load, load_all, score_tier

if TYPE_CHECKING:
    from pathlib import Path


def test_types_inherit() -> None:
    types = load_all()
    assert {"regular", "power_pool", "atom", "arc", "region_agnostic", "super_alpha"} <= set(types)
    assert "my_new_type" not in types  # _new_type_template.yaml is a template, skipped
    pp = types["power_pool"]
    assert pp.lint_rules["max_operators"] == 8
    assert pp.settings["universe"] == "TOP3000"  # inherited from regular
    assert types["region_agnostic"].settings["region"] == "ALL"
    assert types["region_agnostic"].sim_type == "REGION_AGNOSTIC"


def test_lint_power_pool_and_atom() -> None:
    cat = {
        "close": {"dataset": "pv1"},
        "eps": {"dataset": "fundamental6"},
        "rev": {"dataset": "fundamental6"},
        "sent": {"dataset": "news1", "type": "VECTOR"},
    }
    long_expr = "rank(ts_mean(ts_delta(ts_zscore(eps, 21), 5), 5) * rank(rev) - rank(close) + 1)"
    assert not lint(long_expr, load("power_pool"), catalog=cat).ok
    atom = load("atom")
    assert not lint("rank(eps / close)", atom, catalog=cat).ok  # two datasets
    assert lint("rank(eps / rev)", atom, catalog=cat).ok
    assert any("VECTOR" in e for e in lint("rank(sent)", load("regular"), catalog=cat).errors)
    assert lint("rank(vec_avg(sent))", load("regular"), catalog=cat).ok
    assert lint("rank(ts_mean(eps, 37))", load("regular")).warnings  # a mined-looking window


def test_lint_reports_syntax_instead_of_raising() -> None:
    res = lint("rank(close", load("regular"))
    assert not res.ok
    assert res.errors[0].startswith("Syntax")


def test_gate_and_tiers() -> None:
    alpha = {
        "is": {"sharpe": 1.7, "fitness": 1.1, "turnover": 0.2, "checks": []},
        "train": {"sharpe": 1.8},
        "test": {"sharpe": 1.2},
    }
    assert gate(alpha, load("regular")).passed
    assert score_tier(alpha, load("arc")) == 1
    alpha["is"]["checks"] = [{"name": "LOW_SUB_UNIVERSE_SHARPE", "result": "FAIL"}]
    assert not gate(alpha, load("regular")).passed


def test_train_only_type_never_reads_the_test_period() -> None:
    """CLAUDE.md rule 6: selection uses train metrics. A collapse in the test block must not
    move a train-only gate."""
    alpha = {
        "is": {"sharpe": 1.7, "fitness": 1.1, "turnover": 0.2, "checks": []},
        "train": {"sharpe": 1.8},
        "test": {"sharpe": -3.0},
    }
    regular = load("regular")
    assert not gate(alpha, regular).passed
    assert gate(alpha, regular.without_test_gates()).passed


def test_inheritance_loop_is_named(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("name: a\nextends: b\n")
    (tmp_path / "b.yaml").write_text("name: b\nextends: a\n")
    with pytest.raises(ValueError, match="loop"):
        load_all(tmp_path)


def test_forge_data_lives_under_the_app_data_dir() -> None:
    """Forge's files share the AH_DATA_DIR-aware data directory, never a hard-coded home path."""
    from alpha_harness.config import Settings

    assert config.data_dir() == Settings().data_dir / "forge"


def test_config_dir_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "court.yaml").write_text("court: {min_train_sharpe: 9}\n")
    monkeypatch.setenv("AH_FORGE_CONFIG_DIR", str(tmp_path))
    config.reload()
    try:
        assert config.court()["court"]["min_train_sharpe"] == 9
    finally:
        monkeypatch.delenv("AH_FORGE_CONFIG_DIR")
        config.reload()
    assert config.court()["court"]["min_train_sharpe"] != 9
