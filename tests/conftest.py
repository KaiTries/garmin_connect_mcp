from __future__ import annotations

import base64
import json
import time

import pytest

BASE = "https://personal.connectapi.garmin.com"
TOKEN_URL = BASE + "/personalapitoken-service/personal-api/personal-api-key/token"


def fake_jwt(exp_offset: int = 24 * 3600, tag: str = "a") -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part({'exp': int(time.time()) + exp_offset, 'tag': tag})}.sig"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep the Garmin client's key and token cache files out of the real home directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "test-key")
    monkeypatch.delenv("GARMIN_PERSONAL_API_BASE_URL", raising=False)
    return tmp_path


def build_brick_fit() -> bytes:
    """A bike->run brick: 2 sessions, 3 laps, HR records every second."""
    from fit_tool.fit_file_builder import FitFileBuilder
    from fit_tool.profile.messages.file_id_message import FileIdMessage
    from fit_tool.profile.messages.lap_message import LapMessage
    from fit_tool.profile.messages.record_message import RecordMessage
    from fit_tool.profile.messages.session_message import SessionMessage
    from fit_tool.profile.profile_type import FileType, Manufacturer, Sport

    t0 = 1_761_550_000_000  # ms
    builder = FitFileBuilder(auto_define=True)
    fid = FileIdMessage()
    fid.type = FileType.ACTIVITY
    fid.manufacturer = Manufacturer.GARMIN.value
    fid.product = 1
    fid.time_created = t0
    fid.serial_number = 1
    builder.add(fid)

    # 30 min bike: HR 125 for 20 min then 150 for 10; 10 min run at HR 165
    for s in range(40 * 60):
        r = RecordMessage()
        r.timestamp = t0 + s * 1000
        r.heart_rate = 125 if s < 20 * 60 else (150 if s < 30 * 60 else 165)
        builder.add(r)

    def lap(start_s, dur_s, dist_m, hr, sport, power=None, speed=None):
        m = LapMessage()
        m.timestamp = t0 + (start_s + dur_s) * 1000
        m.start_time = t0 + start_s * 1000
        m.total_timer_time = dur_s
        m.total_elapsed_time = dur_s
        m.total_distance = dist_m
        m.avg_heart_rate = hr
        m.sport = sport
        if power:
            m.avg_power = power
        if speed:
            m.avg_speed = speed
        builder.add(m)

    lap(0, 1200, 10_000, 125, Sport.CYCLING, power=180, speed=8.33)
    lap(1200, 600, 5_500, 150, Sport.CYCLING, power=240, speed=9.17)
    lap(1800, 600, 2_000, 165, Sport.RUNNING, speed=3.333)

    def session(start_s, dur_s, dist_m, sport, np=None, tss=None):
        m = SessionMessage()
        m.timestamp = t0 + (start_s + dur_s) * 1000
        m.start_time = t0 + start_s * 1000
        m.total_timer_time = dur_s
        m.total_elapsed_time = dur_s
        m.total_distance = dist_m
        m.sport = sport
        m.avg_speed = dist_m / dur_s
        if np:
            m.normalized_power = np
        if tss:
            m.training_stress_score = tss
        builder.add(m)

    session(0, 1800, 15_500, Sport.CYCLING, np=205, tss=38.5)
    session(1800, 600, 2_000, Sport.RUNNING)
    return builder.build().to_bytes()
