"""Entry point: `garmin-pulse-mcp` (stdio by default) or `garmin-pulse-mcp check`."""

from __future__ import annotations

import logging
import sys

from .config import Settings
from .server import build_server


def check(settings: Settings) -> int:
    """Verify the key and print a one-line summary of what the API returns."""
    from .garmin import GarminError, GarminSource

    source = GarminSource(settings)
    try:
        profile = source.settings() or {}
        today = settings.today()
        acts = source.activities(today.replace(day=1), today, limit=5)
    except GarminError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(
        f"OK: signed in as {profile.get('displayName', '?')}, units {profile.get('measurementSystem', '?')}, "
        f"{len(acts)} recent activities visible."
    )
    return 0


def main() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    settings = Settings.from_env()
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        raise SystemExit(check(settings))
    mcp = build_server(settings)
    if settings.transport == "stdio":
        mcp.run("stdio")
        return

    import uvicorn

    from .http_app import build_http_app

    app = build_http_app(mcp, host=settings.host, token=settings.http_token, allowed_hosts=settings.allowed_hosts)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )


if __name__ == "__main__":
    main()
