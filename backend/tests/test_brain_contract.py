"""Offline regression cover for the five read-only checks in ``scripts/smoke_brain.py``.

The smoke script proves the BRAIN connection against the live platform. These tests prove
the *parsing* against hand-written shapes, so when the smoke script fails we can tell a
changed response apart from a broken session — and so a refactor that breaks a shape fails
here, with no network and no simulations.
"""

from __future__ import annotations

import httpx2
import pytest
from conftest import json_response, make_endpoints, route
from fixtures import (
    ALPHA_LIST,
    AUTH_STATE,
    AUTH_STATE_PLAIN,
    DATA_FIELDS,
    DATA_FIELDS_ENVELOPE,
    OPERATORS,
    PERSONA_401_BODY,
    PNL_RECORDSET,
)

from alpha_harness.brain.errors import (
    BrainAuthError,
    BrainRateLimited,
    BrainVerificationRequired,
)
from alpha_harness.brain.filters import AlphaQuery, Filter

# -- check 1: is the session valid? ----------------------------------------


async def test_get_auth_parses_a_live_session() -> None:
    endpoints, _ = make_endpoints(route({("GET", "/authentication"): json_response(AUTH_STATE)}))
    state = await endpoints.get_auth()

    assert state is not None
    assert state.user_id == "AB12345"
    assert "CONSULTANT" in state.permissions
    # Gates batching in the engine; a wrong read here silently drops throughput to 1.
    assert "MULTI_SIMULATION" in state.permissions


async def test_get_auth_reads_an_account_without_consultant_rights() -> None:
    endpoints, _ = make_endpoints(
        route({("GET", "/authentication"): json_response(AUTH_STATE_PLAIN)})
    )
    state = await endpoints.get_auth()

    assert state is not None
    assert state.user_id == "CD67890"
    assert "CONSULTANT" not in state.permissions
    assert "MULTI_SIMULATION" not in state.permissions


@pytest.mark.parametrize("status", [204, 401])
async def test_get_auth_reports_no_session(status: int) -> None:
    """No session is ``None``, not an exception: the UI shows a sign-in screen for it."""
    endpoints, _ = make_endpoints(route({("GET", "/authentication"): httpx2.Response(status)}))
    assert await endpoints.get_auth() is None


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_get_auth_raises_rather_than_claiming_signed_out(status: int) -> None:
    """Throttled or down is not signed out.

    Returning ``None`` here would make the app drop a perfectly good session every time
    BRAIN had a bad minute, and the next call would spend the sign-in lockout budget.
    """
    endpoints, _ = make_endpoints(
        route({("GET", "/authentication"): json_response({"detail": "nope"}, status=status)})
    )
    with pytest.raises(Exception) as caught:
        await endpoints.get_auth()
    assert not isinstance(caught.value, BrainAuthError)


async def test_persona_step_up_is_not_a_credential_failure() -> None:
    """A 401 carrying an inquiry means "verify yourself", not "wrong password"."""
    endpoints, _ = make_endpoints(
        route({("GET", "/authentication"): json_response(PERSONA_401_BODY, status=401)})
    )
    with pytest.raises(BrainVerificationRequired) as caught:
        await endpoints.get_auth()
    assert caught.value.inquiry == "inq_FAKE123"


# -- check 2: operators ----------------------------------------------------


async def test_list_operators_parses() -> None:
    endpoints, _ = make_endpoints(route({("GET", "/operators"): json_response(OPERATORS)}))
    operators = await endpoints.list_operators()

    assert [o.name for o in operators] == ["rank", "ts_delta", "group_neutralize"]
    assert operators[0].category == "Cross Sectional"
    assert operators[1].definition == "ts_delta(x, d)"


# -- check 3: one dataset's fields for USA / 1 / TOP3000 -------------------


async def test_list_data_fields_accepts_a_bare_array() -> None:
    endpoints, recorder = make_endpoints(
        route({("GET", "/data-fields"): json_response(DATA_FIELDS)})
    )
    fields = await endpoints.list_data_fields_all(
        region="USA", delay=1, universe="TOP3000", instrumentType="EQUITY"
    )

    assert [f.id for f in fields] == ["anl4_rev_eps_mean", "news_sent_score"]
    assert fields[0].dataset is not None
    assert fields[0].dataset.id == "analyst4"
    # Crowding and the pyramid multiplier drive the Phase 4 Data Explorer filters.
    assert fields[0].user_count == 1240
    assert fields[1].pyramid_multiplier == 2.0
    # The bulk read is pinned to 3.0; the default 2.0 answers a different shape.
    assert "version=3.0" in recorder.requests[0].headers["accept"]


async def test_list_data_fields_accepts_an_envelope() -> None:
    """The same endpoint answers both ways, and both branches are live code."""
    endpoints, _ = make_endpoints(
        route({("GET", "/data-fields"): json_response(DATA_FIELDS_ENVELOPE)})
    )
    fields = await endpoints.list_data_fields_all(
        region="USA", delay=1, universe="TOP3000", instrumentType="EQUITY"
    )
    assert [f.id for f in fields] == ["anl4_rev_eps_mean", "news_sent_score"]


async def test_data_fields_sends_every_scope_parameter() -> None:
    """All four are required; omitting one answers for a scope nobody asked about."""
    endpoints, recorder = make_endpoints(
        route({("GET", "/data-fields"): json_response(DATA_FIELDS)})
    )
    await endpoints.list_data_fields_all(
        region="USA", delay=1, universe="TOP3000", instrumentType="EQUITY"
    )
    sent = recorder.requests[0].url.params
    assert sent["region"] == "USA"
    assert sent["delay"] == "1"
    assert sent["universe"] == "TOP3000"
    assert sent["instrumentType"] == "EQUITY"


# -- check 4: my ACTIVE alphas ---------------------------------------------


async def test_list_alphas_parses_the_envelope() -> None:
    endpoints, recorder = make_endpoints(
        route({("GET", "/users/self/alphas"): json_response(ALPHA_LIST)})
    )
    query = AlphaQuery(limit=5, offset=0, filters=[Filter("status", "=", "ACTIVE")])
    page = await endpoints.list_alphas(query)

    assert page["count"] == 2
    assert page["limit"] == 5
    assert [a["id"] for a in page["results"]] == ["aLpHa001", "aLpHa002"]
    assert "version=4.0" in recorder.requests[0].headers["accept"]


async def test_alpha_filter_operator_survives_url_encoding() -> None:
    """The comparison operator is part of the parameter name, not the value.

    A dict-to-querystring helper encodes ``>=`` into the value and the server then matches
    nothing — which looks like "no alphas", not like a bug. Hence the DSL, and hence this.
    """
    query = AlphaQuery(limit=50, filters=[Filter("is.sharpe", ">=", 1.58)])
    assert "is.sharpe>=1.58" in query.path()
    assert "%3E%3D" not in query.path()


async def test_alpha_list_tolerates_nulls_the_codec_allows() -> None:
    """``tags``, ``favorite`` and ``hidden`` are nullable; a null must not fail the page."""
    from alpha_harness.brain.schemas import Alpha

    alpha = Alpha.model_validate(ALPHA_LIST["results"][1])
    assert alpha.tags == []
    assert alpha.favorite is None
    assert alpha.expression == "-ts_sum(returns, 5)"


async def test_check_with_lists_instead_of_numbers_does_not_crash() -> None:
    """``MATCHES_COMPETITION`` carries arrays where other checks carry figures.

    One unknown check shape used to abort the whole listing page it arrived on.
    """
    from alpha_harness.brain.schemas import Alpha

    alpha = Alpha.model_validate(ALPHA_LIST["results"][0])
    assert alpha.in_sample is not None
    by_name = {c.name: c for c in alpha.in_sample.checks}
    assert by_name["LOW_SHARPE"].numbers == (1.62, 1.25)
    assert by_name["MATCHES_COMPETITION"].numbers is None


# -- check 5: one PnL recordset --------------------------------------------


async def test_recordset_zips_columns_against_records() -> None:
    """The wire format is a schema plus positional arrays; you must zip them yourself."""
    endpoints, _ = make_endpoints(
        route({("GET", "/alphas/aLpHa001/recordsets/pnl"): json_response(PNL_RECORDSET)})
    )
    recordset = await endpoints.get_recordset("aLpHa001", "pnl")
    rows = recordset.rows()

    assert len(rows) == 4
    assert rows[0] == {"date": "2025-01-02", "pnl": 0.0}
    assert rows[3]["pnl"] == 21300.0


async def test_recordset_waits_out_retry_after() -> None:
    """A recordset is an asynchronous job: ``Retry-After`` means "not ready yet".

    Its *presence* is the signal, not the status code — a 200 carrying the header is still
    a pending job, and a client that reads the body anyway gets an empty series.
    """
    answers = iter(
        [
            json_response({}, headers={"retry-after": "0"}),
            json_response({}, headers={"retry-after": "0"}),
            json_response(PNL_RECORDSET),
        ]
    )
    endpoints, recorder = make_endpoints(
        route({("GET", "/alphas/aLpHa001/recordsets/pnl"): lambda _: next(answers)})
    )
    recordset = await endpoints.get_recordset("aLpHa001", "pnl")

    assert len(recordset.rows()) == 4
    assert len(recorder.requests) == 3


# -- client behaviours the five checks depend on ---------------------------


async def test_429_becomes_a_typed_rate_limit_error_carrying_the_wait() -> None:
    """CLAUDE.md rule 9: respect Retry-After and 429."""
    endpoints, _ = make_endpoints(
        route(
            {
                ("GET", "/operators"): json_response(
                    {"detail": "too many requests"},
                    status=429,
                    headers={"retry-after": "7"},
                )
            }
        )
    )
    with pytest.raises(BrainRateLimited) as caught:
        # The plain request path: `request_retrying` would sleep out the 7 seconds.
        await endpoints.client.request("GET", "/operators")
    assert caught.value.retry_after == 7.0


async def test_daily_simulation_quota_headers_are_read() -> None:
    """The Quota Governor (Phase 3) trusts the lower of BRAIN's number and its own ledger,
    so it has to be able to read BRAIN's number at all."""
    endpoints, _ = make_endpoints(
        route(
            {
                ("GET", "/operators"): json_response(
                    OPERATORS,
                    headers={
                        "x-ratelimit-limit": "5000",
                        "x-ratelimit-remaining": "4821",
                        "x-ratelimit-reset": "3600",
                    },
                )
            }
        )
    )
    response = await endpoints.client.request("GET", "/operators")

    assert response.rate_limit is not None
    assert response.rate_limit.limit == 5000
    assert response.rate_limit.remaining == 4821
