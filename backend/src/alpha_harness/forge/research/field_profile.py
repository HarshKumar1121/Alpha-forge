"""Static field profile: what a field looks like, read straight off the catalog.

This never simulates (contrast :mod:`.profiler`, which spends 3 BRAIN probes to learn a
field's *behaviour*). Everything here comes from ``data_field``/``data_set`` metadata that a
sync already pulled down, so it is free and instant: type, whether it needs ``vec_avg``/
``vec_sum`` before a template can use it, instrument/date coverage, an update-cadence guess,
and an economic category tag read off its description by keyword, deterministically — no LLM,
so the same field always tags the same way and a test can pin it down.

This is the first rung of the Data Nugget ladder (community BRAIN-researcher practice: read
what a field *is* before you spend a simulation on it, and never combine two fields that are
secretly measuring the same thing).
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

VECTOR = "VECTOR"
MATRIX = "MATRIX"
GROUP = "GROUP"

Cadence = Literal["daily", "quarterly", "sparse", "unknown"]
Category = Literal[
    "quality_profitability",
    "momentum",
    "sentiment",
    "risk",
    "fundamental_ratio",
    "model_score",
    "other",
]

#: Keyword rules, most specific/first-matching wins. Plain substrings on the lower-cased
#: description (dataset description is tried too, as a fallback, since a field's own
#: description is sometimes just its name restated).
_CATEGORY_KEYWORDS: tuple[tuple[Category, tuple[str, ...]], ...] = (
    (
        "sentiment",
        (
            "sentiment",
            "news",
            "buzz",
            "social media",
            "analyst rating",
            "recommendation",
            "tone",
        ),
    ),
    (
        "model_score",
        (
            "model score",
            "ml score",
            "machine learning",
            "predicted probability",
            "composite score",
            "factor score",
            "alpha signal",
            "ensemble",
        ),
    ),
    (
        "risk",
        (
            "volatility",
            "beta",
            "risk",
            "default probability",
            "credit rating",
            "short interest",
            "drawdown",
            "value at risk",
        ),
    ),
    (
        "fundamental_ratio",
        (
            "ratio",
            "yield",
            "multiple",
            "p/e",
            "price to",
            "book to price",
            "ebitda",
            "leverage",
            "debt to",
            "payout",
        ),
    ),
    (
        "momentum",
        (
            "momentum",
            "return",
            "price change",
            "trend",
            "moving average",
            "rsi",
            "reversal",
        ),
    ),
    (
        "quality_profitability",
        (
            "quality",
            "profitability",
            "margin",
            "earnings",
            "eps",
            "revenue",
            "roe",
            "roa",
            "cash flow",
            "growth",
        ),
    ),
)


def categorize(description: str | None, dataset_description: str | None = None) -> Category:
    """The first keyword rule that matches ``description``, else the dataset's, else ``other``.

    Deterministic by construction: same text in, same category out, every time. The ordering
    in :data:`_CATEGORY_KEYWORDS` is the tie-break when a description could read as more than
    one thing (e.g. "momentum-adjusted earnings growth" hits both ``momentum`` and
    ``quality_profitability`` — momentum is checked first because it is the more specific claim
    here).
    """
    for text in (description, dataset_description):
        if not text:
            continue
        lowered = text.lower()
        for category, keywords in _CATEGORY_KEYWORDS:
            if any(k in lowered for k in keywords):
                return category
    return "other"


def cadence(date_coverage: float | None) -> Cadence:
    """A cadence guess from BRAIN's "Date Coverage" (share of the history with a value).

    A field written every trading day covers nearly all of history; one written once a
    quarter covers roughly a quarter of it (carried-forward values aside, BRAIN measures the
    days a *new* value landed). This is a guess from one number, not a measurement of the
    actual update calendar — :mod:`.profiler`'s turnover probe is the real test, and costs a
    simulation. Thresholds are deliberately coarse so a profile is a hint, not a verdict.
    """
    if date_coverage is None:
        return "unknown"
    if date_coverage >= 0.85:
        return "daily"
    if date_coverage >= 0.15:
        return "quarterly"
    return "sparse"


_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str | None) -> frozenset[str]:
    return frozenset(_WORD.findall((text or "").lower()))


@dataclass(frozen=True, slots=True)
class FieldProfile:
    """What a Data Nugget needs to know about one candidate field, read without simulating."""

    field_id: str
    dataset_id: str | None
    dataset_name: str | None
    description: str | None
    field_type: str
    coverage: float | None
    date_coverage: float | None
    category_name: str | None
    subcategory_name: str | None

    @property
    def needs_aggregation(self) -> bool:
        """Whether a template must reduce this field with ``vec_avg``/``vec_sum`` first."""
        return self.field_type == VECTOR

    @property
    def cadence(self) -> Cadence:
        return cadence(self.date_coverage)

    @property
    def category(self) -> Category:
        return categorize(self.description, self.dataset_name)

    @property
    def description_tokens(self) -> frozenset[str]:
        return _tokens(self.description)


def profile_field(row: dict[str, Any]) -> FieldProfile:
    """Build a :class:`FieldProfile` from one row of ``CatalogQueries.fields_by_id`` (or
    anything carrying the same keys: ``field_id``, ``dataset_id``, ``dataset_name``,
    ``description``, ``field_type``, ``coverage``, ``date_coverage``, ``category_name``,
    ``subcategory_name``)."""
    return FieldProfile(
        field_id=str(row["field_id"]),
        dataset_id=row.get("dataset_id"),
        dataset_name=row.get("dataset_name"),
        description=row.get("description"),
        field_type=row.get("field_type") or MATRIX,
        coverage=row.get("coverage"),
        date_coverage=row.get("date_coverage"),
        category_name=row.get("category_name"),
        subcategory_name=row.get("subcategory_name"),
    )


def duplicate_reason(a: FieldProfile, b: FieldProfile) -> str | None:
    """Why ``a`` and ``b`` look like two measures of the same underlying signal, or ``None``.

    Two checks, cheapest/most certain first:

    1. **Same statistic, different spelling.** ``eps_mean`` and ``eps_avg`` both name the
       "mean" side of the ``mean/std`` pair the template engine already knows about
       (:data:`..research.templates._SEMANTIC_PAIRS`) and share every other token in their id
       — they are the same number under two names, not two numbers worth combining.
    2. **Same category, same dataset, near-identical description.** A fallback for fields
       whose ids give no hint: if they come from the same dataset, carry the same category,
       and their description words overlap almost entirely, they are very likely redescribing
       one field (e.g. a renamed or region-local duplicate of it).
    """
    # Local import: keeps field_profile usable without pulling in the template-slot engine,
    # and reuses its one definition of "these words name the same statistic" rather than a
    # second copy that could drift from it.
    from .templates import semantic_side_of, semantic_stem

    if a.field_id == b.field_id:
        return f"{a.field_id} is the same field."

    side_a, side_b = semantic_side_of(a.field_id), semantic_side_of(b.field_id)
    same_side = side_a is not None and side_a == side_b
    same_stem = semantic_stem(a.field_id) == semantic_stem(b.field_id)
    if same_side and same_stem and side_a is not None:
        label = "/".join(sorted(side_a))
        return (
            f"{a.field_id} and {b.field_id} both look like the {label} of the same measure "
            "(same name once the statistic word is stripped out) — not two different signals."
        )

    if (
        a.dataset_id
        and a.dataset_id == b.dataset_id
        and a.category == b.category
        and a.description_tokens
        and b.description_tokens
    ):
        overlap = a.description_tokens & b.description_tokens
        union = a.description_tokens | b.description_tokens
        jaccard = len(overlap) / len(union) if union else 0.0
        if jaccard >= 0.8:
            return (
                f"{a.field_id} and {b.field_id} are both {a.dataset_id} / {a.category} fields "
                "with near-identical descriptions — likely the same measure twice."
            )
    return None
