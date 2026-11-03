"""Pairing suggestions: given 2+ candidate fields, which pairs are worth writing a nugget
rationale for, and which the engine can already tell are wrong — advisory only.

Reuses the template engine's same-dataset/semantic-pairing fix (``forge/research/templates``,
``bindings``/``semantic_problem``) rather than a second copy of "these words name the same
statistic": a pairing suggestion here and a template's ``same_dataset_as`` slot binding should
never disagree about what counts as a mean/std or estimate/actual pair.

The user still writes the rationale and the falsifiable claim by hand — this only says
whether the pairing is structurally sane, never why it should work.
"""

from dataclasses import dataclass
from itertools import combinations

from ..research.field_profile import FieldProfile, duplicate_reason
from ..research.templates import is_semantic_pair, semantic_side_of


@dataclass(frozen=True, slots=True)
class PairingSuggestion:
    field_a: str
    field_b: str
    valid: bool
    reason: str


def _semantic_pair(a: FieldProfile, b: FieldProfile) -> bool:
    """Whether the two field ids name opposite halves of a known statistic pair (mean/std,
    estimate/actual, high/low, numerator/denominator) — the same vocabulary
    ``templates.semantic_problem`` checks a template slot's hint against."""
    return is_semantic_pair(semantic_side_of(a.field_id), semantic_side_of(b.field_id))


def suggest_pairings(fields: list[FieldProfile]) -> list[PairingSuggestion]:
    """Every pair among ``fields``, each marked valid or not, with the reason either way.

    A pair is valid when it is either from the same dataset (BRAIN groups related measures
    into one dataset, so that alone is a real signal) or recognisably a named statistic pair
    across datasets (e.g. an analyst estimate against a reported actual). Anything else is
    flagged as needing a human judgement call before it becomes a nugget, and a pair that looks
    like the same measure twice is rejected outright.
    """
    out: list[PairingSuggestion] = []
    for a, b in combinations(fields, 2):
        dup = duplicate_reason(a, b)
        if dup is not None:
            out.append(PairingSuggestion(a.field_id, b.field_id, valid=False, reason=dup))
            continue
        same_dataset = bool(a.dataset_id) and a.dataset_id == b.dataset_id
        if same_dataset:
            out.append(
                PairingSuggestion(
                    a.field_id,
                    b.field_id,
                    valid=True,
                    reason=f"Both fields are in dataset {a.dataset_id!r}.",
                )
            )
            continue
        if _semantic_pair(a, b):
            out.append(
                PairingSuggestion(
                    a.field_id,
                    b.field_id,
                    valid=True,
                    reason=(
                        "Field ids name opposite halves of a known statistic pair "
                        "(e.g. estimate/actual, mean/std) across datasets."
                    ),
                )
            )
            continue
        out.append(
            PairingSuggestion(
                a.field_id,
                b.field_id,
                valid=False,
                reason=(
                    f"{a.field_id} ({a.dataset_id or 'unknown dataset'}) and {b.field_id} "
                    f"({b.dataset_id or 'unknown dataset'}) are from different datasets with no "
                    "recognised statistic relation between them — write a rationale only if you "
                    "can state a specific economic reason the two should move together."
                ),
            )
        )
    return out
