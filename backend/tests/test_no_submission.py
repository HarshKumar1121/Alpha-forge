"""CLAUDE.md rule 1, enforced: **no code path may submit an alpha.**

Submitting is irreversible and is done by hand on the BRAIN website. The guarantee today is
an absence — ``BrainEndpoints`` simply has no ``submit`` method — and an absence is exactly
the kind of invariant that a well-meaning future commit erases without noticing. So it is
checked here instead of trusted.

Two independent detectors, because either alone has a blind spot:

* a **call-site scan** over the AST, which catches ``client.request("POST", ...)`` against a
  submission path even when the path is an f-string;
* a **literal scan**, which catches a submission path assigned to a name first and sent
  somewhere the AST scan cannot follow.

``/users/self/activities/submissions`` is read-only — it is how many alphas were submitted
per day, which the quarter screen counts — so the detectors key on the path segment
``submit`` exactly, not on the substring.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from alpha_harness.brain.endpoints import BrainEndpoints

if TYPE_CHECKING:
    from pathlib import Path

#: Methods on the client that put a request on the wire.
SENDERS = {"request", "request_retrying", "poll"}

#: ``poll`` is a GET by construction; the others take the method as their first argument.
ALWAYS_GET = {"poll"}


def _render(node: ast.expr) -> str | None:
    """A path literal as text, with interpolations collapsed to ``{}``.

    Returns ``None`` for a path this scan cannot see through — a bare name or a call. Those
    are the literal scan's job.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
            else:
                out.append("{}")
        return "".join(out)
    return None


def is_submission_path(path: str) -> bool:
    """Whether a path addresses alpha submission.

    Keyed on the exact segment so ``/users/self/activities/submissions`` — a read of daily
    submitted-alpha counts — is not mistaken for one.
    """
    segments = path.split("?")[0].strip("/").split("/")
    return "submit" in segments


def submission_call_sites(tree: ast.AST, filename: str) -> list[str]:
    """Every call in ``tree`` that would send a request to a submission path."""
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        sender = node.func.attr
        if sender not in SENDERS or not node.args:
            continue

        if sender in ALWAYS_GET:
            method, path_node = "GET", node.args[0]
        else:
            if len(node.args) < 2:
                continue
            method = _render(node.args[0]) or "?"
            path_node = node.args[1]

        path = _render(path_node)
        if path is not None and is_submission_path(path):
            found.append(f"{filename}:{node.lineno}: {method} {path}")
    return found


def submission_literals(tree: ast.AST, filename: str) -> list[str]:
    """Every string literal in ``tree`` that spells a submission path."""
    return [
        f"{filename}:{node.lineno}: {node.value!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and is_submission_path(node.value)
    ]


def _sources(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


# -- the guarantee ---------------------------------------------------------


def test_no_backend_code_calls_a_submission_endpoint(repo_src: Path) -> None:
    sources = _sources(repo_src)
    assert sources, "found no backend sources to scan — the guard would pass vacuously"

    offenders: list[str] = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(repo_src.parent.parent)
        offenders += submission_call_sites(tree, str(relative))
        offenders += submission_literals(tree, str(relative))

    assert not offenders, (
        "Alpha submission must stay manual (CLAUDE.md rule 1). Found:\n  " + "\n  ".join(offenders)
    )


def test_endpoints_exposes_no_submit_method() -> None:
    """The absence in ``BrainEndpoints`` is the structural half of the guarantee."""
    named = [
        name
        for name in dir(BrainEndpoints)
        if not name.startswith("_") and "submit" in name.lower()
    ]
    assert not named, f"BrainEndpoints grew a submission method: {named}"


# -- the guard's own teeth -------------------------------------------------
#
# A guard that cannot fail is worse than no guard, because it reads like cover. These feed
# the detectors code that *should* trip them.


def test_call_site_scan_catches_a_planted_submission() -> None:
    planted = ast.parse(
        'await self.client.request("POST", f"/alphas/{alpha_id}/submit")',
        filename="planted.py",
    )
    assert submission_call_sites(planted, "planted.py")


def test_call_site_scan_catches_a_plain_string_path() -> None:
    planted = ast.parse(
        'await client.request_retrying("POST", "/alphas/abc123/submit")',
        filename="planted.py",
    )
    assert submission_call_sites(planted, "planted.py")


def test_literal_scan_catches_a_path_held_in_a_name() -> None:
    """The indirection the AST scan cannot follow."""
    planted = ast.parse('SUBMIT = "/alphas/{}/submit"', filename="planted.py")
    assert not submission_call_sites(planted, "planted.py")
    assert submission_literals(planted, "planted.py")


def test_detectors_leave_the_read_only_submissions_endpoint_alone() -> None:
    """``/users/self/activities/submissions`` is a GET of daily counts, not a submission."""
    benign = ast.parse(
        'SUBMISSIONS_PATH = "/users/self/activities/submissions"\n'
        'await client.request_retrying("GET", SUBMISSIONS_PATH)',
        filename="benign.py",
    )
    assert not submission_call_sites(benign, "benign.py")
    assert not submission_literals(benign, "benign.py")
    assert not is_submission_path("/users/self/activities/submissions")
