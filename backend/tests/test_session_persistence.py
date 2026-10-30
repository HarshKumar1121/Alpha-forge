"""A sign-in must survive a restart — including one BRAIN paused for an identity check.

The bug this covers: BRAIN can answer a sign-in with ``401`` plus a Persona *inquiry*,
meaning "the password is fine, now prove who you are". That path left nothing on disk.

    login(email, password)  -> 401 + inquiry   -> authenticated=False, so no credential
    verify(inquiry)         -> 200 + user      -> _save_cookies(...)
    _save_cookies           -> no credential   -> returns early, jar dropped

``BrainSessionRow.credential_id`` is a non-nullable foreign key, so with no credential row
there is nowhere to hang a cookie jar. The session then existed only in the live process and
died with it: every restart, and ``scripts/smoke_brain.py``, saw a signed-out account.

These tests drive the real :class:`AuthService` against a mock transport, then build a second
service on the same database — which is what a restart is.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import pytest_asyncio
from conftest import json_response
from fixtures import AUTH_STATE, OPERATORS

from alpha_harness.account import AuthService, PlatformMetadata
from alpha_harness.brain.client import BrainClient
from alpha_harness.brain.endpoints import BrainEndpoints
from alpha_harness.db.sqlite import Database

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

EMAIL = "fake.user@example.invalid"
PASSWORD = "not-a-real-password"  # noqa: S105 - fixture value, never a live credential

#: The 401 that means "prove who you are", as opposed to "wrong password".
PERSONA_401 = {"inquiry": "inq_FAKE123"}

#: Sign-in solves an ALTCHA proof-of-work first, and the real solver runs in these tests —
#: so the challenge has to be genuinely solvable. A tiny answer keeps the search instant.
ALTCHA_SALT = "s"
ALTCHA_ANSWER = 7


def altcha_challenge() -> dict[str, Any]:
    digest = hashlib.sha256(f"{ALTCHA_SALT}{ALTCHA_ANSWER}".encode()).hexdigest()
    return {
        "algorithm": "SHA-256",
        "challenge": digest,
        "salt": ALTCHA_SALT,
        "signature": "fake-signature",
        "maxnumber": 100,
    }


@pytest_asyncio.fixture
async def db(tmp_path: Any) -> AsyncIterator[Database]:
    """A real SQLite database, thrown away with the test's tmp_path."""
    database = Database.for_path(tmp_path / "harness.db")
    await database.create_all()
    yield database
    await database.dispose()


def make_service(db: Database, handler: Any, sealer: Any) -> AuthService:
    """An ``AuthService`` wired exactly as ``AppState`` wires it, minus the network."""
    client = BrainClient(
        "https://brain.invalid", timeout=5.0, transport=httpx.MockTransport(handler)
    )
    endpoints = BrainEndpoints(client)
    return AuthService(db, sealer, endpoints, metadata=PlatformMetadata(db, endpoints))


@pytest.fixture
def sealer(tmp_path: Any) -> Any:
    from alpha_harness.sealing import Sealer

    return Sealer.from_path(tmp_path / "key")


#: The cookie BRAIN sets on the step-up response and honours afterwards.
SESSION_COOKIE = "t=fake-session-cookie"


def step_up_then_accept() -> Any:
    """BRAIN refusing the sign-in for an identity check, then accepting the verification.

    ``GET /authentication`` answers off the *cookie the request carries*, not off a flag
    held in this closure. That is the whole point: a restart gets a brand-new client whose
    only way to be recognised is the jar that was persisted and reloaded. Keying on a flag
    would let the test pass with nothing saved at all.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        carries_session = SESSION_COOKIE in request.headers.get("cookie", "")

        if path == "/captcha":
            return json_response(altcha_challenge())
        if path == "/authentication" and request.method == "POST":
            # The step-up: a 401 carrying an inquiry, and the session cookie set alongside it.
            return httpx.Response(
                401,
                json=PERSONA_401,
                headers={"set-cookie": f"{SESSION_COOKIE}; Path=/"},
            )
        if path == "/authentication/persona" and request.method == "POST":
            return json_response(AUTH_STATE)
        if path == "/authentication" and request.method == "GET":
            return json_response(AUTH_STATE) if carries_session else httpx.Response(401)
        if path == "/operators":
            return json_response(OPERATORS)
        if path.startswith("/users/"):
            return json_response({"id": "AB12345", "firstName": "Fake", "lastName": "User"})
        return json_response({}, status=404)

    return handler


async def test_identity_check_signin_survives_a_restart(db: Database, sealer: Any) -> None:
    """The whole sequence: refused for a check, verified, then restarted."""
    service = make_service(db, step_up_then_accept(), sealer)

    # 1. Sign in. BRAIN pauses it for an identity check.
    paused = await service.login(EMAIL, PASSWORD)
    assert paused.authenticated is False
    assert paused.inquiry == "inq_FAKE123"

    # 2. The person completes the check in their browser; the app closes the inquiry.
    verified = await service.verify("inq_FAKE123")
    assert verified.authenticated is True
    assert verified.user_id == "AB12345"

    # 3. Restart: a brand-new service over the same database and a fresh cookie jar.
    #    This is the assertion the bug failed — restore() used to find nothing at all.
    restarted = make_service(db, step_up_then_accept(), sealer)
    restored = await restarted.restore()

    assert restored.authenticated is True, (
        "a sign-in completed through BRAIN's identity check did not survive a restart"
    )
    assert restored.user_id == "AB12345"
    assert restored.restored_from_cache is True


async def test_the_paused_signin_stores_a_credential_to_hang_cookies_on(
    db: Database, sealer: Any
) -> None:
    """``brain_session.credential_id`` is NOT NULL, so the credential must exist first."""
    service = make_service(db, step_up_then_accept(), sealer)
    await service.login(EMAIL, PASSWORD)

    stored = await service.get_credential()
    assert stored is not None
    assert stored[0] == EMAIL
    # Sealed at rest, and it round-trips.
    assert stored[1] == PASSWORD


async def test_a_wrong_password_is_still_never_stored(db: Database, sealer: Any) -> None:
    """The guarantee the fix must not weaken.

    A mistyped password comes back as a plain 401 with a ``detail`` and no inquiry. Storing
    it would make silent re-login retry a credential BRAIN rejects, spending the lockout
    budget on every attempt.
    """

    def refuses(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/captcha":
            return json_response(altcha_challenge())
        if request.url.path == "/authentication":
            return json_response({"detail": "Incorrect authentication credentials."}, status=401)
        return json_response({}, status=404)

    service = make_service(db, refuses, sealer)
    refused = await service.login(EMAIL, "wrong-password")

    assert refused.authenticated is False
    assert refused.inquiry is None
    assert await service.get_credential() is None, "a rejected password must not be stored"
