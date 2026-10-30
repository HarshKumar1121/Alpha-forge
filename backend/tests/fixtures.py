"""Hand-written BRAIN response shapes.

**None of this is captured from a live session.** Every value here was written by hand from
the shapes documented in ``alpha_harness.brain.schemas``: the ids are obvious fakes
(``AB12345``, ``aLpHa001``), the figures are round numbers, and the dates are chosen rather
than observed. That matters for two reasons — a fixture taken from a real account leaks the
account into git, and a fixture that happens to match one account's data stops being a test
of the *shape*.

These exist so the read-only checks in ``scripts/smoke_brain.py`` have offline regression
cover: if BRAIN changes a response shape, the smoke script starts failing against the
platform and these tests tell us which field moved.
"""

from __future__ import annotations

from typing import Any

#: ``GET /authentication``. ``token.expiry`` is seconds remaining, not an absolute time.
AUTH_STATE: dict[str, Any] = {
    "user": {"id": "AB12345"},
    "token": {"expiry": 14400.0},
    "permissions": ["CONSULTANT", "MULTI_SIMULATION"],
}

#: ``GET /authentication`` for an account with no consultant rights, which is the case that
#: must still sign in cleanly while reporting a smaller feature set.
AUTH_STATE_PLAIN: dict[str, Any] = {
    "user": {"id": "CD67890"},
    "token": {"expiry": 3600.0},
    "permissions": [],
}

#: ``GET /operators``. Three categories, because the Template Studio's lint groups by them.
OPERATORS: list[dict[str, Any]] = [
    {
        "name": "rank",
        "category": "Cross Sectional",
        "scope": ["REGULAR", "COMBO"],
        "definition": "rank(x, rate=2)",
        "description": "Ranks the input among all instruments and returns 0..1.",
        "level": "ALL",
    },
    {
        "name": "ts_delta",
        "category": "Time Series",
        "scope": ["REGULAR"],
        "definition": "ts_delta(x, d)",
        "description": "x - ts_delay(x, d)",
        "level": "ALL",
    },
    {
        "name": "group_neutralize",
        "category": "Group",
        "scope": ["REGULAR"],
        "definition": "group_neutralize(x, group)",
        "description": "Demeans x within each group.",
        "level": "ALL",
    },
]

#: ``GET /data-fields`` as a bare array, which is how a whole market answers.
#: camelCase on the wire; ``BulkField`` is a msgspec struct with ``rename="camel"``.
DATA_FIELDS: list[dict[str, Any]] = [
    {
        "id": "anl4_rev_eps_mean",
        "description": "Analyst revision, mean EPS estimate",
        "type": "MATRIX",
        "coverage": 0.82,
        "dateCoverage": 0.61,
        "userCount": 1240,
        "alphaCount": 8800,
        "pyramidMultiplier": 1.5,
        "themes": ["ANALYST"],
        "dateCreated": "2019-04-01",
        "dataset": {"id": "analyst4", "name": "Analyst Estimates"},
        "category": {"id": "analyst", "name": "Analyst"},
        "subcategory": {"id": "analyst_est", "name": "Estimates"},
    },
    {
        "id": "news_sent_score",
        "description": "News sentiment score",
        "type": "MATRIX",
        "coverage": 0.44,
        "dateCoverage": 0.95,
        "userCount": 90,
        "alphaCount": 310,
        "pyramidMultiplier": 2.0,
        "themes": [],
        "dateCreated": "2022-11-15",
        "dataset": {"id": "news12", "name": "News Sentiment"},
        "category": {"id": "news", "name": "News"},
        "subcategory": None,
    },
]

#: The same endpoint when it answers with an envelope instead. Both branches are live code
#: in ``list_data_fields_all``, so both are covered.
DATA_FIELDS_ENVELOPE: dict[str, Any] = {"count": 2, "results": DATA_FIELDS}

#: ``GET /users/self/alphas`` at version 4.0. Two ACTIVE alphas, which is what the smoke
#: script asks for.
ALPHA_LIST: dict[str, Any] = {
    "count": 2,
    "next": None,
    "previous": None,
    "results": [
        {
            "id": "aLpHa001",
            "type": "REGULAR",
            "author": "AB12345",
            "status": "ACTIVE",
            "grade": "GOOD",
            "dateCreated": "2025-01-15T10:30:00-05:00",
            "dateSubmitted": "2025-01-16T09:00:00-05:00",
            "settings": {
                "instrumentType": "EQUITY",
                "region": "USA",
                "universe": "TOP3000",
                "delay": 1,
                "decay": 4,
                "neutralization": "SUBINDUSTRY",
                "truncation": 0.08,
                "testPeriod": "P2Y0M0D",
            },
            "regular": {
                "code": "rank(ts_delta(anl4_rev_eps_mean, 21))",
                "operatorCount": 2,
            },
            "is": {
                "sharpe": 1.62,
                "fitness": 1.04,
                "turnover": 0.18,
                "returns": 0.14,
                "drawdown": 0.09,
                "margin": 0.0012,
                "longCount": 1480,
                "shortCount": 1470,
                "pnl": 2400000.0,
                "bookSize": 20000000.0,
                "startDate": "2015-01-01",
                "checks": [
                    {"name": "LOW_SHARPE", "result": "PASS", "limit": 1.25, "value": 1.62},
                    {"name": "LOW_TURNOVER", "result": "PASS", "limit": 0.01, "value": 0.18},
                    # Carries lists rather than numbers; `Check.numbers` must return None.
                    {
                        "name": "MATCHES_COMPETITION",
                        "result": "PASS",
                        "matched": [],
                        "unmatched": ["IQC2025"],
                    },
                ],
            },
            "os": None,
        },
        {
            "id": "aLpHa002",
            "type": "REGULAR",
            "author": "AB12345",
            "status": "ACTIVE",
            "grade": "AVERAGE",
            "dateCreated": "2025-02-02T11:00:00-05:00",
            "dateSubmitted": None,
            # Nullable on the wire; a null must not fail the page.
            "favorite": None,
            "hidden": None,
            "tags": None,
            "settings": {
                "instrumentType": "EQUITY",
                "region": "USA",
                "universe": "TOP3000",
                "delay": 1,
                "decay": 0,
                "neutralization": "INDUSTRY",
                "truncation": 0.08,
            },
            "regular": {"code": "-ts_sum(returns, 5)", "operatorCount": 2},
            "is": {"sharpe": 1.31, "turnover": 0.44, "checks": []},
        },
    ],
}

#: ``GET /alphas/{id}/recordsets/pnl``: a schema of columns plus positional records.
PNL_RECORDSET: dict[str, Any] = {
    "schema": {
        "name": "pnl",
        "title": "Profit & Loss",
        "properties": [
            {"name": "date", "title": "Date", "type": "date"},
            {"name": "pnl", "title": "PnL", "type": "decimal"},
        ],
    },
    "records": [
        ["2025-01-02", 0.0],
        ["2025-01-03", 12500.0],
        ["2025-01-06", 9800.0],
        ["2025-01-07", 21300.0],
    ],
}

#: A 401 carrying a Persona identity check rather than a credential failure. The client has
#: to tell these apart or a step-up reads as a wrong password.
PERSONA_401_BODY: dict[str, Any] = {"inquiry": "inq_FAKE123"}
