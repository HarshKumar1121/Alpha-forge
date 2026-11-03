"""Field profile: category tagging, cadence and duplicate detection — all static, all
deterministic (CLAUDE.md: no LLM, no simulation; see ``forge/research/field_profile.py``).
"""

from alpha_harness.forge.research.field_profile import (
    categorize,
    duplicate_reason,
    profile_field,
)


def _row(**over: object) -> dict[str, object]:
    base = {
        "field_id": "anl4_eps_mean",
        "dataset_id": "analyst4",
        "dataset_name": "Analyst Estimates",
        "description": "Mean analyst estimate of earnings per share",
        "field_type": "MATRIX",
        "coverage": 0.9,
        "date_coverage": 0.95,
        "category_name": "analyst",
        "subcategory_name": None,
    }
    base.update(over)
    return base


# --- category tagging, on real-shaped catalog descriptions ---------------------------------


def test_quality_profitability_from_eps_description() -> None:
    p = profile_field(_row(description="Mean analyst estimate of earnings per share"))
    assert p.category == "quality_profitability"


def test_momentum_from_price_trend_description() -> None:
    p = profile_field(_row(field_id="px_mom_21", description="21-day price momentum rank"))
    assert p.category == "momentum"


def test_sentiment_from_news_description() -> None:
    p = profile_field(
        _row(field_id="news_buzz", description="Social media buzz and sentiment score")
    )
    assert p.category == "sentiment"


def test_risk_from_volatility_description() -> None:
    p = profile_field(
        _row(field_id="beta_63", description="63-day rolling beta against the market")
    )
    assert p.category == "risk"


def test_fundamental_ratio_from_book_to_price() -> None:
    p = profile_field(
        _row(field_id="btop", dataset_id="pv1", description="Book value to price ratio")
    )
    assert p.category == "fundamental_ratio"


def test_model_score_from_composite_description() -> None:
    p = profile_field(
        _row(field_id="ml_alpha_1", description="Composite score from a machine learning model")
    )
    assert p.category == "model_score"


def test_other_when_nothing_matches() -> None:
    assert categorize("A field with no recognisable economic keyword in it", None) == "other"


def test_dataset_description_is_a_fallback() -> None:
    """A bare, uninformative field description still tags correctly from the dataset's own."""
    p = profile_field(
        _row(
            field_id="x1",
            description="Field X1",
            dataset_name="Analyst sentiment and news flow",
        )
    )
    assert p.category == "sentiment"


# --- type / aggregation / cadence -----------------------------------------------------------


def test_vector_field_needs_aggregation() -> None:
    p = profile_field(_row(field_type="VECTOR"))
    assert p.needs_aggregation is True


def test_matrix_field_does_not_need_aggregation() -> None:
    p = profile_field(_row(field_type="MATRIX"))
    assert p.needs_aggregation is False


def test_high_date_coverage_reads_as_daily() -> None:
    p = profile_field(_row(date_coverage=0.97))
    assert p.cadence == "daily"


def test_mid_date_coverage_reads_as_quarterly() -> None:
    p = profile_field(_row(date_coverage=0.3))
    assert p.cadence == "quarterly"


def test_low_date_coverage_reads_as_sparse() -> None:
    p = profile_field(_row(date_coverage=0.05))
    assert p.cadence == "sparse"


def test_missing_date_coverage_is_unknown_not_sparse() -> None:
    p = profile_field(_row(date_coverage=None))
    assert p.cadence == "unknown"


# --- near-duplicate detection ----------------------------------------------------------------


def test_mean_and_avg_of_same_field_are_duplicates() -> None:
    a = profile_field(_row(field_id="anl4_eps_mean", description="Mean analyst EPS estimate"))
    b = profile_field(_row(field_id="anl4_eps_avg", description="Average analyst EPS estimate"))
    reason = duplicate_reason(a, b)
    assert reason is not None
    assert "same measure" in reason


def test_mean_and_std_of_same_field_are_a_valid_pair_not_a_duplicate() -> None:
    a = profile_field(_row(field_id="anl4_eps_mean", description="Mean analyst EPS estimate"))
    b = profile_field(
        _row(field_id="anl4_eps_std", description="Std deviation of analyst EPS estimate")
    )
    assert duplicate_reason(a, b) is None


def test_near_identical_description_same_dataset_is_a_duplicate() -> None:
    a = profile_field(
        _row(
            field_id="rev_growth_1",
            description="Year over year revenue growth percentage reported for the company",
            dataset_id="fundamentals1",
        )
    )
    b = profile_field(
        _row(
            field_id="rev_growth_2",
            description="Year over year revenue growth percentage reported for the firm",
            dataset_id="fundamentals1",
        )
    )
    assert duplicate_reason(a, b) is not None


def test_unrelated_fields_in_different_datasets_are_not_duplicates() -> None:
    a = profile_field(_row(field_id="anl4_eps_mean", dataset_id="analyst4"))
    b = profile_field(
        _row(
            field_id="close",
            dataset_id="pv1",
            description="Close price",
            field_type="MATRIX",
            category_name="pv",
        )
    )
    assert duplicate_reason(a, b) is None
