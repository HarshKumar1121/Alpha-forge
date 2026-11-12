"""How Alpha Forge is reached when it runs on a server (Railway) rather than on a laptop.

Two settings, both read from the environment so nothing is hard-coded:

- ``AH_ALLOWED_HOSTS``: the host names the app answers to, comma-separated. Starlette's
  ``TrustedHostMiddleware`` refuses any other ``Host`` header with a 400, so the public
  Railway domain has to be listed here. ``localhost`` and ``127.0.0.1`` are always allowed.
- ``AH_ACCESS_PASSWORD``: when set, every request must carry HTTP Basic credentials whose
  password matches. This is the only lock on a public URL. The username is ignored, so the
  browser's sign-in prompt accepts anything in that field. Unset means open, as on a laptop.

The password is never logged and never echoed back in a response.
"""

import base64
import binascii
import os
import secrets
from typing import TYPE_CHECKING

from starlette.responses import PlainTextResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

#: Always allowed: the laptop's own addresses, which the desktop app uses.
DEFAULT_HOSTS = ("localhost", "127.0.0.1")

HOSTS_VARIABLE = "AH_ALLOWED_HOSTS"
# The name of an environment variable, not a password: it is read from the environment.
PASSWORD_VARIABLE = "AH_ACCESS_PASSWORD"  # noqa: S105


def allowed_hosts() -> list[str]:
    """The local addresses plus any extra hosts named in ``AH_ALLOWED_HOSTS``."""
    extra = [host.strip() for host in os.environ.get(HOSTS_VARIABLE, "").split(",")]
    return [*DEFAULT_HOSTS, *(host for host in extra if host)]


def access_password() -> str | None:
    """The gate's password from the environment; an empty value counts as unset."""
    return os.environ.get(PASSWORD_VARIABLE) or None


class BasicAccessGate:
    """Pure ASGI middleware that asks for the access password on every request.

    Pure ASGI rather than ``@app.middleware("http")`` because it also has to refuse a
    WebSocket handshake, which the HTTP middleware never sees.
    """

    def __init__(self, app: ASGIApp, password: str | None) -> None:
        self.app = app
        self._expected = password.encode("utf-8") if password else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._expected is None or scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if self._signed_in(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # Closing before accept refuses the handshake; the browser never gets a socket.
            await send({"type": "websocket.close", "code": 1008})
            return
        response = PlainTextResponse(
            "Sign in to Alpha Forge.",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Alpha Forge", charset="UTF-8"'},
        )
        await response(scope, receive, send)

    def _signed_in(self, scope: Scope) -> bool:
        """Whether the Basic credentials carry the right password, compared in constant time."""
        header = dict(scope.get("headers", [])).get(b"authorization", b"")
        if not header.lower().startswith(b"basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:].strip(), validate=True)
        except binascii.Error, ValueError:
            return False
        _, separator, password = decoded.partition(b":")
        if not separator or self._expected is None:
            return False
        return secrets.compare_digest(password, self._expected)
