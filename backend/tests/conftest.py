"""Test harness: a BRAIN client that cannot reach the network.

Every test drives a real :class:`BrainClient` over an ``httpx2.MockTransport``, so the
parsing, pacing and error-mapping code under test is the same code the application runs —
only the socket is replaced. CLAUDE.md rule 4: no test may spend a BRAIN simulation.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx2
import pytest

from alpha_harness.brain.client import BrainClient
from alpha_harness.brain.endpoints import BrainEndpoints

#: Not the real host. A test that somehow escaped the mock transport would fail to resolve
#: rather than quietly reach BRAIN.
TEST_BASE = "https://brain.invalid"

Handler = Callable[[httpx2.Request], httpx2.Response]


class Recorder:
    """The requests a test made, so it can assert on what went over the wire."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    @property
    def calls(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.requests]


def json_response(
    body: Any,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx2.Response:
    """A JSON response built the way BRAIN sends one."""
    return httpx2.Response(
        status,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json", **(headers or {})},
    )


def make_client(handler: Handler) -> tuple[BrainClient, Recorder]:
    """A client whose every request is answered by ``handler``."""
    recorder = Recorder()

    def recording(request: httpx2.Request) -> httpx2.Response:
        recorder.requests.append(request)
        return handler(request)

    client = BrainClient(TEST_BASE, transport=httpx2.MockTransport(recording))
    return client, recorder


def make_endpoints(handler: Handler) -> tuple[BrainEndpoints, Recorder]:
    client, recorder = make_client(handler)
    return BrainEndpoints(client), recorder


def route(table: dict[tuple[str, str], httpx2.Response | Handler]) -> Handler:
    """Dispatch on ``(method, path)``, failing loudly on anything unrouted.

    An unexpected request is a test failure rather than a 404: a code path that quietly
    calls one more endpoint than the test expected is exactly what we want to hear about.
    """

    def handler(request: httpx2.Request) -> httpx2.Response:
        key = (request.method, request.url.path)
        found = table.get(key)
        if found is None:
            raise AssertionError(f"unrouted request: {key[0]} {key[1]}")
        return found(request) if callable(found) else found

    return handler


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def repo_src() -> Iterator[Any]:
    """The backend package root, for the tests that read source rather than run it."""
    from pathlib import Path

    yield Path(__file__).resolve().parents[1] / "src" / "alpha_harness"
