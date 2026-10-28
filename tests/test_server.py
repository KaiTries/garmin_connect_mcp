import json
import os
import socket
import subprocess
import sys
import time
from datetime import date

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from garmin_connect_mcp.config import Settings
from garmin_connect_mcp.garmin import GarminError
from garmin_connect_mcp.server import build_server
from tests import fixtures as f
from tests.conftest import build_brick_fit

TODAY = date(2026, 10, 28)


class FrozenSettings(Settings):
    def today(self) -> date:
        return TODAY


class FakeGarmin:
    def __init__(self):
        self.calls = []

    def daily_summaries(self, lo, hi):
        self.calls.append(("daily", lo, hi))
        return f.DAILY_SUMMARIES["data"]

    def sleep_summaries(self, lo, hi):
        return f.SLEEP_SUMMARIES

    def hrv_summaries(self, lo, hi):
        return f.HRV_SUMMARIES["hrvSummaries"]

    def activities(self, lo, hi, activity_type=None, limit=20):
        self.calls.append(("activities", lo, hi, activity_type, limit))
        return [f.RUN, f.SWIM, f.RIDE][:limit]

    def activity(self, activity_id):
        if activity_id == 404:
            raise GarminError("Garmin has nothing at /activity-service/activity/404 (HTTP 404). Check the ID or date.")
        return f.ACTIVITY_DETAIL

    def activity_workouts(self, activity_id):
        return [{"workoutName": "Brick 30/10", "sport": "MULTISPORT", "workoutPhrase": "30' Z2 bike, 10' tempo run"}]

    def activity_fit(self, activity_id):
        return build_brick_fit()

    def heart_rate_zones(self):
        return f.HR_ZONES

    def settings(self):
        return {"displayName": "kai", "measurementSystem": "metric", "timeZone": "Europe/Zurich"}

    def weight_range(self, lo, hi):
        return f.WEIGHT_RANGE

    def get(self, path, params=None):
        self.calls.append(("get", path, params))
        return {"x": None, "y": [1, 2]}


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def call(server, name, args=None):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        result = await client.call_tool(name, args or {})
    text = result.content[0].text
    return result.isError, (text if result.isError else json.loads(text))


@pytest.mark.anyio
async def test_tools_are_listed_read_only():
    server = build_server(FrozenSettings(), FakeGarmin())
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        tools = (await client.list_tools()).tools
    assert len(tools) == 9
    assert all(t.annotations.readOnlyHint and not t.annotations.destructiveHint for t in tools)


@pytest.mark.anyio
async def test_recovery_defaults_to_last_seven_days():
    fake = FakeGarmin()
    is_error, out = await call(build_server(FrozenSettings(), fake), "get_recovery")
    assert not is_error
    assert out["from"] == "2026-10-22" and out["to"] == "2026-10-28"
    assert out["days"][1]["sleep_score"] == 81
    assert ("daily", date(2026, 10, 22), TODAY) in fake.calls


@pytest.mark.anyio
async def test_recovery_rejects_bad_ranges():
    server = build_server(FrozenSettings(), FakeGarmin())
    is_error, text = await call(server, "get_recovery", {"start_date": "2026-01-01", "end_date": "today"})
    assert is_error and "at most 92" in text
    is_error, text = await call(server, "get_recovery", {"start_date": "27.10.2026"})
    assert is_error and "Invalid date" in text


@pytest.mark.anyio
async def test_activity_detail_includes_brick_legs_and_zones():
    is_error, out = await call(build_server(FrozenSettings(), FakeGarmin()), "get_activity", {"activity_id": 7})
    assert not is_error
    assert out["planned_workout"][0]["name"] == "Brick 30/10"
    assert [s["sport"] for s in out["sessions"]] == ["cycling", "running"]
    assert len(out["laps"]) == 3
    assert out["hr_zone_min_from_records"] == [20.0, 0.0, 10.0, 10.0, 0.0]


@pytest.mark.anyio
async def test_garmin_errors_become_tool_errors():
    is_error, text = await call(build_server(FrozenSettings(), FakeGarmin()), "get_activity", {"activity_id": 404})
    assert is_error and "HTTP 404" in text


@pytest.mark.anyio
async def test_training_summary_default_window_starts_on_monday():
    fake = FakeGarmin()
    is_error, out = await call(build_server(FrozenSettings(), fake), "get_training_summary")
    assert not is_error
    assert out["from"] == "2026-09-07"  # Monday, 8 ISO weeks including the current one
    assert {p["period"] for p in out["periods"]} == {"2026-W43", "2026-W44"}


@pytest.mark.anyio
async def test_raw_get_guards_paths():
    fake = FakeGarmin()
    server = build_server(FrozenSettings(), fake)
    is_error, text = await call(server, "garmin_api_get", {"path": "/hrv-service/../personalapitoken-service"})
    assert is_error
    is_error, text = await call(server, "garmin_api_get", {"path": "https://evil.example/x"})
    assert is_error
    is_error, out = await call(server, "garmin_api_get", {"path": "/fitnessage-service/fitnessage/v2/2026-10-27"})
    assert not is_error and out == {"y": [1, 2]}


@pytest.mark.anyio
async def test_profile_warns_about_expiring_key():
    settings = FrozenSettings(key_expires=date(2026, 11, 5))
    is_error, out = await call(build_server(settings, FakeGarmin()), "get_profile")
    assert out["api_key_warning"] == "The Garmin Personal API key expires in 8 days."
    assert out["heart_rate_zones"][0]["zone_floors_bpm"] == [110, 130, 145, 160, 172]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(env_extra):
    env = {**os.environ, "GARMIN_MCP_TRANSPORT": "streamable-http", **env_extra}
    return subprocess.Popen([sys.executable, "-m", "garmin_connect_mcp"], env=env, stderr=subprocess.PIPE)


def test_http_requires_bearer_token():
    port = _free_port()
    proc = _start({"GARMIN_MCP_PORT": str(port), "GARMIN_MCP_HTTP_TOKEN": "s3cret"})
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                if httpx.get(base + "/healthz").status_code == 200:
                    break
            except httpx.ConnectError:
                time.sleep(0.1)
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
        }
        headers = {"Accept": "application/json, text/event-stream"}
        assert httpx.post(base + "/mcp", json=init, headers=headers).status_code == 401
        bad = httpx.post(base + "/mcp", json=init, headers={**headers, "Authorization": "Bearer nope"})
        assert bad.status_code == 401
        ok = httpx.post(base + "/mcp", json=init, headers={**headers, "Authorization": "Bearer s3cret"})
        assert ok.status_code == 200 and "garmin" in ok.text
    finally:
        proc.terminate()
        proc.wait(5)


def test_http_refuses_public_bind_without_token():
    proc = _start({"GARMIN_MCP_HOST": "0.0.0.0", "GARMIN_MCP_PORT": str(_free_port())})
    _, err = proc.communicate(timeout=20)
    assert proc.returncode != 0 and b"Refusing to serve" in err
