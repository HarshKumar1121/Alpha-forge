"""The Railway hosting seam: the host allow-list and the access-password gate.

Offline: a tiny Starlette app stands in for the real one, and nothing reaches BRAIN.
"""

import base64
from typing import TYPE_CHECKING

import httpx2
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from alpha_harness.forge.hosting import BasicAccessGate, allowed_hosts

if TYPE_CHECKING:
    import pytest


def _app(password: str | None) -> BasicAccessGate:
    async def home(_request: object) -> PlainTextResponse:
        return PlainTextResponse("inside")

    return BasicAccessGate(Starlette(routes=[Route("/", home)]), password=password)


async def _get(app: BasicAccessGate, headers: dict[str, str] | None = None) -> httpx2.Response:
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/", headers=headers or {})


def _basic(password: str, username: str = "anyone") -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def test_no_password_leaves_the_app_open() -> None:
    response = await _get(_app(None))
    assert response.status_code == 200


async def test_a_request_without_credentials_is_asked_to_sign_in() -> None:
    response = await _get(_app("s3cret"))
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic ")
    assert "s3cret" not in response.text


async def test_the_wrong_password_is_refused() -> None:
    response = await _get(_app("s3cret"), _basic("guess"))
    assert response.status_code == 401


async def test_the_right_password_passes_whatever_the_username() -> None:
    response = await _get(_app("s3cret"), _basic("s3cret", username="someone-else"))
    assert response.status_code == 200
    assert response.text == "inside"


async def test_malformed_credentials_are_refused_not_crashed() -> None:
    for header in ("Basic !!!not-base64", "Basic", "Bearer s3cret", "Basic " + "x" * 3):
        response = await _get(_app("s3cret"), {"Authorization": header})
        assert response.status_code == 401, header


async def test_a_password_with_no_colon_is_refused() -> None:
    token = base64.b64encode(b"s3cret").decode()
    response = await _get(_app("s3cret"), {"Authorization": f"Basic {token}"})
    assert response.status_code == 401


def test_allowed_hosts_keeps_the_local_addresses_and_adds_the_configured_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AH_ALLOWED_HOSTS", raising=False)
    assert allowed_hosts() == ["localhost", "127.0.0.1"]

    monkeypatch.setenv("AH_ALLOWED_HOSTS", " alpha-forge.up.railway.app , ,forge.example.com ")
    assert allowed_hosts() == [
        "localhost",
        "127.0.0.1",
        "alpha-forge.up.railway.app",
        "forge.example.com",
    ]


async def test_the_app_factory_puts_the_gate_in_front_of_every_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alpha_harness.main import create_app

    monkeypatch.setenv("AH_ACCESS_PASSWORD", "s3cret")
    monkeypatch.setenv("AH_ALLOWED_HOSTS", "alpha-forge.up.railway.app")
    app = create_app()
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as client:
        # Refused before any route runs, so no app state is needed for this check.
        response = await client.get("/api/health")
    assert response.status_code == 401
