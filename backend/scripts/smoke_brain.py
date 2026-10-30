"""Prove the BRAIN connection works, without spending a single simulation.

Run it after signing in through the app's own sign-in screen::

    cd backend && uv run python scripts/smoke_brain.py

It reuses the session cookie the app already sealed in the local vault. It never asks for
a password, never signs in, and never retries a sign-in — CLAUDE.md rule 2. If there is no
valid session it says so and stops.

**Why each check exists.** They are not arbitrary pings; they are the five reads every
later phase is built on, in dependency order:

1. ``session``    — everything else is meaningless without it.
2. ``operators``  — the language. The Template Studio's lint (Phase 5) refuses to pass an
                    operator this account does not have, so the list has to be readable.
3. ``datasets``   — the catalog the Data Explorer (Phase 4) filters.
4. ``fields``     — one dataset's fields in one market. This is the unit a field set is
                    built from, and the Behaviour Lab (Phase 8) profiles.
5. ``alphas``     — your own submitted work, which the families and portfolio screens
                    (Phase 9) measure new candidates against.
6. ``pnl``        — a daily PnL series. Every local metric worth having (Sharpe, drawdown,
                    correlation, factor exposure) is computed from this series rather than
                    from BRAIN's summary figures.

**It cannot spend quota.** Read-only is enforced at the transport, not promised in a
comment: :class:`ReadOnlyTransport` refuses any request that is not a GET or OPTIONS, so a
``POST /simulations`` would raise here rather than cost a simulation.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx2

from alpha_harness.account import AuthService, PlatformMetadata
from alpha_harness.brain.client import BrainClient
from alpha_harness.brain.endpoints import BrainEndpoints
from alpha_harness.brain.filters import AlphaQuery, Filter
from alpha_harness.brain.platform import API_BASE
from alpha_harness.config import Settings
from alpha_harness.db.sqlite import Database
from alpha_harness.sealing import Sealer

PASS = "PASS"  # noqa: S105 - a check outcome, not a password
FAIL = "FAIL"
SKIP = "SKIP"

#: Fields read from the sample dataset. One page is enough to prove the shape parses; the
#: whole market is ~40 MB and the catalog sync is what downloads it.
FIELD_SAMPLE = 50


class ReadOnlyViolation(RuntimeError):
    """A write was attempted by a script that is only allowed to read."""


class ReadOnlyTransport(httpx2.AsyncHTTPTransport):
    """Lets GET and OPTIONS through and refuses everything else.

    This is the structural guarantee behind "spends no simulations". ``POST /simulations``
    is the only way to spend one, and it cannot leave this process.
    """

    ALLOWED = frozenset({"GET", "OPTIONS", "HEAD"})

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.method not in self.ALLOWED:
            raise ReadOnlyViolation(
                f"smoke_brain.py attempted {request.method} {request.url.path}; "
                "this script is read-only and must never write to BRAIN."
            )
        return await super().handle_async_request(request)


@dataclass
class Check:
    """One row of the report."""

    name: str
    status: str = SKIP
    detail: str = ""
    seconds: float = 0.0


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str) -> Check:
        check = Check(name)
        self.checks.append(check)
        return check

    @property
    def failed(self) -> bool:
        return any(c.status == FAIL for c in self.checks)

    def render(self) -> str:
        name_w = max((len(c.name) for c in self.checks), default=6)
        detail_w = max((len(c.detail) for c in self.checks), default=6)
        rule = f"+{'-' * (name_w + 2)}+--------+------+{'-' * (detail_w + 2)}+"

        header = f"| {'CHECK'.ljust(name_w)} | STATUS |   ms | {'DETAIL'.ljust(detail_w)} |"
        rows = [
            f"| {c.name.ljust(name_w)} | {c.status.ljust(6)} | "
            f"{round(c.seconds * 1000):>4} | {c.detail.ljust(detail_w)} |"
            for c in self.checks
        ]
        return "\n".join([rule, header, rule, *rows, rule])


async def _timed(check: Check, work: Any) -> Any:
    """Run one check, recording how long it took and turning a failure into a FAIL row.

    Nothing is allowed to raise out of here: one dead endpoint should still produce a full
    table rather than a stack trace and four unknown rows.
    """
    started = time.monotonic()
    try:
        result = await work
    except Exception as exc:  # noqa: BLE001 - the report is the error channel
        check.status = FAIL
        check.detail = f"{type(exc).__name__}: {exc}"[:120]
        return None
    finally:
        check.seconds = time.monotonic() - started
    check.status = PASS
    return result


async def run(region: str, delay: int, universe: str) -> Report:
    settings = Settings()
    settings.ensure_data_dir()
    report = Report()

    client = BrainClient(API_BASE, transport=ReadOnlyTransport())
    endpoints = BrainEndpoints(client)
    db = Database(settings.sqlite_path)
    await db.create_all()
    sealer = Sealer(settings.key_path)
    metadata = PlatformMetadata(db, endpoints)
    auth = AuthService(db, sealer, endpoints, metadata=metadata)

    try:
        # 1. Session. Restore only — never a sign-in, so a stale cookie costs nothing and
        #    spends none of BRAIN's lockout budget.
        check = report.add("session")
        session = await _timed(check, auth.restore())
        if session is None or not session.authenticated:
            check.status = FAIL
            check.detail = check.detail or "no valid session — sign in through the app first"
            return report
        permissions = ",".join(session.permissions) or "none"
        check.detail = f"user={session.user_id} permissions={permissions}"

        # 2. Operators: the language this account may write alphas in.
        check = report.add("operators")
        operators = await _timed(check, endpoints.list_operators())
        if operators is not None:
            categories = len({o.category for o in operators if o.category})
            check.detail = f"{len(operators)} operators across {categories} categories"
            if not operators:
                check.status = FAIL
                check.detail = "BRAIN returned an empty operator list"

        scope: dict[str, Any] = {
            "region": region,
            "delay": delay,
            "universe": universe,
            "instrumentType": "EQUITY",
        }

        # 3. Datasets for one market.
        check = report.add("datasets")
        datasets = await _timed(check, endpoints.list_data_sets_all(**scope))
        dataset_id = None
        if datasets is not None:
            if datasets:
                # Most fields first: the richest dataset is the most useful smoke target
                # and the one most likely to expose a shape change.
                richest = max(datasets, key=lambda d: d.field_count or 0)
                dataset_id = richest.id
                check.detail = (
                    f"{len(datasets)} datasets in {region}/{delay}/{universe}; "
                    f"richest={dataset_id} ({richest.field_count} fields)"
                )
            else:
                check.status = FAIL
                check.detail = f"no datasets for {region}/{delay}/{universe}"

        # 4. One dataset's fields.
        #
        # A `dataset.id` filter without a `limit` is refused outright (`400 ["Invalid
        # query"]`); the unlimited read is only offered for a whole market, which is ~40 MB
        # and far more than a smoke check should pull. One page proves the shape.
        check = report.add("fields")
        if dataset_id is None:
            check.detail = "no dataset to read fields from"
        else:
            fields = await _timed(
                check,
                endpoints.list_data_fields_all(
                    **scope, **{"dataset.id": dataset_id, "limit": FIELD_SAMPLE}
                ),
            )
            if fields is not None:
                matrix = sum(1 for f in fields if f.type == "MATRIX")
                check.detail = f"{len(fields)} fields sampled from {dataset_id} ({matrix} MATRIX)"
                if not fields:
                    check.status = FAIL
                    check.detail = f"{dataset_id} returned no fields"

        # 5. Your own ACTIVE alphas.
        check = report.add("alphas")
        query = AlphaQuery(limit=5, offset=0, filters=[Filter("status", "=", "ACTIVE")])
        page = await _timed(check, endpoints.list_alphas(query))
        alpha_id = None
        if page is not None:
            results = page.get("results") or []
            if results:
                alpha_id = results[0].get("id")
            check.detail = f"{page.get('count', 0)} ACTIVE; sampled {alpha_id or 'none'}"

        # 6. One PnL series — the input to every local metric worth having.
        check = report.add("pnl")
        if alpha_id is None:
            check.detail = "no ACTIVE alpha to read a recordset from"
        else:
            recordset = await _timed(check, endpoints.get_recordset(str(alpha_id), "pnl"))
            if recordset is not None:
                rows = recordset.rows()
                span = f"{rows[0].get('date')}..{rows[-1].get('date')}" if rows else "empty"
                check.detail = f"{len(rows)} daily rows for {alpha_id} ({span})"
                if not rows:
                    check.status = FAIL
                    check.detail = f"{alpha_id} returned an empty PnL series"
    finally:
        await client.aclose()
        await db.dispose()

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--region", default="USA")
    parser.add_argument("--delay", type=int, default=1)
    parser.add_argument("--universe", default="TOP3000")
    args = parser.parse_args()

    report = asyncio.run(run(args.region, args.delay, args.universe))
    print(report.render())

    if report.failed:
        print("\nFAILED. Nothing above spends simulations, so it is safe to re-run.")
        return 1
    print("\nAll checks passed. The BRAIN connection is good and no simulations were spent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
