"""Settings read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _env_list(name: str) -> list[str]:
    raw = _env(name, "") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # Garmin
    api_key_file: Path | None = None  # None -> Garmin client's discovery (env var, then ~/.config/garmin/...)
    key_expires: date | None = None  # optional reminder; Garmin doesn't expose key expiry via the API
    timezone: str | None = None  # IANA name used for "today"; None -> system local time
    request_timeout: float = 30.0

    # MCP transport
    transport: str = "stdio"  # stdio | streamable-http
    host: str = "127.0.0.1"
    port: int = 8000
    http_token: str | None = field(default=None, repr=False)
    allowed_hosts: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> Settings:
        key_file = _env("GARMIN_PERSONAL_API_KEY_FILE")
        expires = _env("GARMIN_KEY_EXPIRES")
        token = _env("GARMIN_MCP_HTTP_TOKEN")
        token_file = _env("GARMIN_MCP_HTTP_TOKEN_FILE")
        if token is None and token_file:
            token = Path(token_file).read_text(encoding="utf-8").strip() or None
        transport = (_env("GARMIN_MCP_TRANSPORT", "stdio") or "stdio").lower()
        if transport not in ("stdio", "streamable-http"):
            raise SystemExit(f"GARMIN_MCP_TRANSPORT must be 'stdio' or 'streamable-http', got {transport!r}")
        return cls(
            api_key_file=Path(key_file).expanduser() if key_file else None,
            key_expires=date.fromisoformat(expires) if expires else None,
            timezone=_env("GARMIN_MCP_TZ") or _env("TZ"),
            request_timeout=float(_env("GARMIN_MCP_REQUEST_TIMEOUT", "30") or 30),
            transport=transport,
            host=_env("GARMIN_MCP_HOST", "127.0.0.1") or "127.0.0.1",
            port=int(_env("GARMIN_MCP_PORT", "8000") or 8000),
            http_token=token,
            allowed_hosts=_env_list("GARMIN_MCP_ALLOWED_HOSTS"),
        )

    def today(self) -> date:
        if self.timezone:
            try:
                return datetime.now(ZoneInfo(self.timezone)).date()
            except Exception:  # unknown zone name -> fall back to local time
                pass
        return datetime.now().astimezone().date()
