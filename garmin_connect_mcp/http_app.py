"""Streamable-HTTP app with a shared-secret bearer gate, for running on a home server."""

from __future__ import annotations

import hmac
import ipaddress
import os
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

OPEN_PATHS = frozenset({"/healthz"})


def is_loopback(host: str) -> bool:
    host = host.strip().strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class BearerGate:
    """ASGI wrapper: every HTTP request except /healthz must send Authorization: Bearer <token>."""

    def __init__(self, app, token: str) -> None:
        self._app = app
        self._token = token.encode()

    def _authorized(self, scope) -> bool:
        for name, value in scope.get("headers") or ():
            if name.lower() == b"authorization":
                scheme, _, credential = value.strip().partition(b" ")
                return scheme.lower() == b"bearer" and hmac.compare_digest(credential.strip(), self._token)
        return False

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") not in OPEN_PATHS and not self._authorized(scope):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
            return
        await self._app(scope, receive, send)


class HealthCheck:
    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/healthz":
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"ok"})
            return
        await self._app(scope, receive, send)


def build_http_app(mcp: FastMCP, *, host: str, token: str | None, allowed_hosts: list[str]):
    if allowed_hosts:
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[f"https://{h}" for h in allowed_hosts] + [f"http://{h}" for h in allowed_hosts],
        )
    if token is None and not is_loopback(host):
        if os.environ.get("GARMIN_MCP_ALLOW_UNAUTHENTICATED") != "1":
            raise SystemExit(
                f"Refusing to serve Garmin health data on {host} without GARMIN_MCP_HTTP_TOKEN. "
                "Set a token, bind to 127.0.0.1, or set GARMIN_MCP_ALLOW_UNAUTHENTICATED=1 "
                "if an authenticating reverse proxy sits in front."
            )
        print("warning: serving without a bearer token; make sure a proxy authenticates requests", file=sys.stderr)
    app = mcp.streamable_http_app()
    if token:
        app = BearerGate(app, token)
    return HealthCheck(app)
