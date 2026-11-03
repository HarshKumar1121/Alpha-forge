"""Nugget pairing suggestions: same-dataset and cross-dataset statistic pairs are offered,
near-duplicate measures and economically-incoherent cross-dataset pairs are rejected — all
advisory, pure functions, no DB (CLAUDE.md rule 4: free, offline)."""

from alpha_harness.forge.nuggets.suggest import suggest_pairings
from alpha_harness.forge.research.field_profile import profile_field


def _field(field_id: str, dataset_id: str, description: str, **over: object) -> object:
    row = {
        "field_id": field_id,
        "dataset_id": dataset_id,
        "dataset_name": dataset_id,
        "description": description,
        "field_type": "MATRIX",
        "coverage": 0.8,
        "date_coverage": 0.8,
        "category_name": None,
        "subcategory_name": None,
    }
    row.update(over)
    return profile_field(row)


def test_same_dataset_pair_is_valid() -> None:
    a = _field("anl4_eps_mean", "analyst4", "Mean analyst EPS estimate")
    b = _field("anl4_rev_mean", "analyst4", "Mean analyst revenue estimate")
    suggestions = suggest_pairings([a, b])
    assert len(suggestions) == 1
    assert suggestions[0].valid
    assert "analyst4" in suggestions[0].reason


def test_cross_dataset_estimate_actual_pair_is_valid() -> None:
    estimate = _field("anl4_eps_estimate", "analyst4", "Consensus EPS estimate")
    actual = _field("fnd1_eps_actual", "fundamentals1", "Reported actual EPS")
    suggestions = suggest_pairings([estimate, actual])
    assert len(suggestions) == 1
    assert suggestions[0].valid
    assert "statistic pair" in suggestions[0].reason


def test_duplicate_signal_pair_is_rejected() -> None:
    mean = _field("anl4_eps_mean", "analyst4", "Mean analyst EPS estimate")
    avg = _field("anl4_eps_avg", "analyst4", "Average analyst EPS estimate")
    suggestions = suggest_pairings([mean, avg])
    assert len(suggestions) == 1
    assert suggestions[0].valid is False
    assert "same measure" in suggestions[0].reason


def test_cross_dataset_nonsense_pair_is_rejected() -> None:
    eps = _field("anl4_eps_mean", "analyst4", "Mean analyst EPS estimate")
    sentiment = _field("news_buzz", "news1", "Social media sentiment score")
    suggestions = suggest_pairings([eps, sentiment])
    assert len(suggestions) == 1
    assert suggestions[0].valid is False
    assert "different datasets" in suggestions[0].reason


def test_suggests_every_pair_among_three_or_more_fields() -> None:
    a = _field("anl4_eps_mean", "analyst4", "Mean analyst EPS estimate")
    b = _field("anl4_rev_mean", "analyst4", "Mean analyst revenue estimate")
    c = _field("news_buzz", "news1", "Social media sentiment score")
    suggestions = suggest_pairings([a, b, c])
    pairs = {frozenset((s.field_a, s.field_b)) for s in suggestions}
    assert len(suggestions) == 3
    assert frozenset((a.field_id, b.field_id)) in pairs
    assert frozenset((a.field_id, c.field_id)) in pairs
    assert frozenset((b.field_id, c.field_id)) in pairs
