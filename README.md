# garmin-pulse-mcp

A read-only MCP server for your own Garmin Connect data, built on Garmin's **official
Personal API** (no password, no scraping). It authenticates with a revocable API key you
create in Garmin Connect and uses Garmin's own Python client underneath.

Instead of mirroring the API one endpoint per tool, it exposes a few tools shaped around
questions, and returns compact, unit-labelled JSON so a whole month fits in context.

## Tools

| Tool | What it answers | Calls to Garmin |
| --- | --- | --- |
| `get_recovery(start, end)` | Per day: sleep (h, score, stages, bedtime), overnight HRV + 7-day avg, resting HR, stress, Body Battery, steps; plus range averages. ≤ 92 days, default 7. | daily summaries + sleep + HRV, split into 31-day windows, in parallel |
| `get_day_detail(date)` | Sleep sub-scores and Garmin's insight, HRV readings range, Body Battery events (what charged/drained it) | 4 |
| `list_activities(start, end, activity_type, limit)` | One row per activity: duration, distance, pace/speed, HR, power/NP, TSS/IF, training load, TE, minutes per HR zone | 1 per 45 activities |
| `get_activity(id, include_laps)` | Summary + planned workout; from the FIT file: each leg of a multisport/brick session, every lap, HR-zone minutes | 3–4 |
| `get_training_summary(start, end, group_by)` | Volume per ISO week/month/day split by swim/bike/run/…: sessions, hours, km, elevation, load, TSS, zone minutes (computed locally). Default 8 weeks | 1 per 45 activities |
| `get_body_composition(start, end)` | Weigh-ins (kg, body fat, muscle, water) and range average | 1 |
| `get_nutrition(date)` | Food log totals, goals, meals; hydration and sweat loss | 2 |
| `get_profile()` | Units, time zone, training days, HR zones; optional key-expiry warning | 2 |
| `garmin_api_get(path, params)` | Escape hatch for any other Personal API GET (stress series, all-day HR, SpO2, fitness age…), squeezed and capped at 40k chars | 1 |

All tools are annotated read-only. Units are metric and spelled out in the keys
(`_km`, `_min`, `_bpm`, `_w`, `_ms`, `_kg`); run pace is min/km, swim pace min/100 m.
Dates accept `YYYY-MM-DD`, `today` or `yesterday`; sleep and HRV are filed under the
morning you woke up.

**Not available from the Personal API** (so not here either): training status/readiness,
VO2max trend, race predictor, creating or scheduling workouts, writing anything.

## Setup

### 1. Install uv

[uv](https://docs.astral.sh/uv/) is the only prerequisite — it handles Python and the
package in one shot.

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Create a Garmin Personal API key

Garmin Connect web → Settings → Personal API
(<https://connect.garmin.com/app/settings/personalApiToken>). Pick an expiry (max one
year), copy the key; it's shown once.

### 3. Add to your MCP config

macOS Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/KaiTries/garmin_connect_mcp", "garmin-pulse-mcp"],
      "env": {
        "GARMIN_PERSONAL_API_KEY": "YOUR-KEY-HERE",
        "GARMIN_MCP_TZ": "Europe/Zurich",
        "GARMIN_KEY_EXPIRES": "2026-10-28"
      }
    }
  }
}
```

Claude Code:
```bash
claude mcp add garmin -- uvx --from git+https://github.com/KaiTries/garmin_connect_mcp garmin-pulse-mcp
# then set the key: claude mcp edit garmin  (add GARMIN_PERSONAL_API_KEY to env)
```

`uvx` fetches and caches the package on first run. To update to the latest version later:
```bash
uvx --from git+https://github.com/KaiTries/garmin_connect_mcp --reinstall garmin-pulse-mcp check
```

**Prefer a secrets file?** Store the key at `~/.config/garmin/connect-personal-api-key`
(mode `600`) and omit `GARMIN_PERSONAL_API_KEY` from the config. Useful if multiple tools
share the same key or you'd rather not put credentials in the MCP config file.

```bash
mkdir -p ~/.config/garmin
printf '%s' 'YOUR-KEY' > ~/.config/garmin/connect-personal-api-key
chmod 600 ~/.config/garmin/connect-personal-api-key
```

### Home server (streamable HTTP)

```bash
git clone https://github.com/KaiTries/garmin_connect_mcp
cd garmin_connect_mcp
mkdir -p secrets
printf '%s' 'YOUR-GARMIN-KEY' > secrets/garmin_api_key
openssl rand -hex 32 > secrets/mcp_http_token
chmod 644 secrets/*   # the container runs as uid 10001 and must read them
docker compose up -d --build
```

The server listens on `:8000/mcp`; every request except `/healthz` needs
`Authorization: Bearer <contents of secrets/mcp_http_token>`. It refuses to bind to a
non-loopback address without a token unless you set `GARMIN_MCP_ALLOW_UNAUTHENTICATED=1`
(only do that behind a reverse proxy that authenticates). Behind a proxy, set
`GARMIN_MCP_ALLOWED_HOSTS` to the public host name to enable DNS-rebinding protection.

Claude Code against the server:
```bash
claude mcp add --transport http garmin https://garmin.your.lan/mcp --header "Authorization: Bearer $TOKEN"
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `GARMIN_PERSONAL_API_KEY` / `GARMIN_PERSONAL_API_KEY_FILE` | `~/.config/garmin/connect-personal-api-key` | API key source |
| `GARMIN_KEY_EXPIRES` | – | `YYYY-MM-DD`; `get_profile` warns within 14 days |
| `GARMIN_MCP_TZ` (or `TZ`) | system | Defines "today" |
| `GARMIN_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `GARMIN_MCP_HOST` / `GARMIN_MCP_PORT` | `127.0.0.1` / `8000` | HTTP bind |
| `GARMIN_MCP_HTTP_TOKEN` / `GARMIN_MCP_HTTP_TOKEN_FILE` | – | Bearer token for HTTP |
| `GARMIN_MCP_ALLOWED_HOSTS` | – | Comma-separated Host names for DNS-rebinding protection |
| `GARMIN_MCP_REQUEST_TIMEOUT` | `30` | Seconds per Garmin request |

## How it works

- Key → 24 h bearer token via Garmin's client (cached in `~/.config/garmin/`); a rejected
  token is renewed once, an expired or deleted key produces a message telling you where to
  make a new one.
- Long ranges are split into the windows each endpoint accepts (28 days for stress/steps,
  31 for sleep/daily summaries/Body Battery, 365 for HRV; 45 activities per page).
- FIT files are parsed with `fitparse`; HR-zone minutes integrate per-second records against
  your Garmin zone floors, ignoring gaps over 10 s (pauses).

## Development

```bash
git clone https://github.com/KaiTries/garmin_connect_mcp
cd garmin_connect_mcp
uv sync --frozen
uv run pytest
```

Tests use payloads shaped after Garmin's OpenAPI models, mocked HTTP for the client layer,
a real MCP client session against the server, a synthetic bike→run FIT file, and the HTTP
transport in a subprocess.
