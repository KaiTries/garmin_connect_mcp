"""Summarise an activity's FIT file: sessions (one per leg in multisport), laps, HR zone time."""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

import fitparse

from .transform import clean, minutes, pace, rnd, sport_family

MAX_LAPS = 80
MAX_RECORD_GAP_S = 10  # ignore pauses when integrating time-in-zone


def _speed(values: dict) -> Any:
    return values.get("enhanced_avg_speed") or values.get("avg_speed")


def _session(v: dict) -> dict:
    family = sport_family(str(v.get("sport") or ""))
    row = {
        "sport": v.get("sport"),
        "sub_sport": v.get("sub_sport") if v.get("sub_sport") not in (None, "generic") else None,
        "start": v["start_time"].strftime("%Y-%m-%d %H:%M UTC") if isinstance(v.get("start_time"), datetime) else None,
        "duration_min": minutes(v.get("total_timer_time")),
        "elapsed_min": minutes(v.get("total_elapsed_time")),
        "distance_km": rnd(v["total_distance"] / 1000, 2) if isinstance(v.get("total_distance"), (int, float)) else None,
        "elev_gain_m": v.get("total_ascent"),
        "avg_hr_bpm": v.get("avg_heart_rate"),
        "max_hr_bpm": v.get("max_heart_rate"),
        "avg_power_w": v.get("avg_power"),
        "np_w": v.get("normalized_power"),
        "tss": rnd(v.get("training_stress_score")),
        "avg_cadence": v.get("avg_cadence"),
        "pool_length_m": rnd(v.get("pool_length"), 0) if family == "swim" else None,
        "lengths": v.get("num_active_lengths"),
    }
    row.update(pace(_speed(v), family))
    return clean(row)


def _lap(index: int, v: dict, family: str) -> dict:
    row = {
        "lap": index,
        "duration_min": minutes(v.get("total_timer_time"), 2),
        "distance_m": rnd(v.get("total_distance"), 0),
        "avg_hr_bpm": v.get("avg_heart_rate"),
        "max_hr_bpm": v.get("max_heart_rate"),
        "avg_power_w": v.get("avg_power"),
        "np_w": v.get("normalized_power"),
        "avg_cadence": v.get("avg_cadence"),
        "elev_gain_m": v.get("total_ascent"),
        "intensity": v.get("intensity") if v.get("intensity") not in (None, "active") else None,
        "swim_stroke": v.get("swim_stroke"),
        "lengths": v.get("num_active_lengths") or None,
    }
    row.update(pace(_speed(v), family))
    return clean(row)


def zone_time(records: list[tuple[datetime, int]], floors: list[int] | None) -> list[float] | None:
    """Minutes in HR zones 1-5 given the zone floor heart rates (Garmin's zone1Floor..zone5Floor)."""
    if not floors or len(floors) != 5 or any(f is None for f in floors) or len(records) < 2:
        return None
    totals = [0.0] * 5
    for (t0, hr), (t1, _) in zip(records, records[1:], strict=False):
        gap = (t1 - t0).total_seconds()
        if gap <= 0 or gap > MAX_RECORD_GAP_S or hr is None or hr < floors[0]:
            continue
        zone = max(i for i, floor in enumerate(floors) if hr >= floor)
        totals[zone] += gap
    return [rnd(t / 60, 1) for t in totals]


def summarize_fit(data: bytes, hr_zone_floors: list[int] | None = None) -> dict:
    fit = fitparse.FitFile(io.BytesIO(data))
    sessions: list[dict] = []
    laps_raw: list[dict] = []
    records: list[tuple[datetime, int]] = []
    for message in fit.get_messages(["session", "lap", "record"]):
        values = message.get_values()
        if message.name == "session":
            sessions.append(values)
        elif message.name == "lap":
            laps_raw.append(values)
        elif message.name == "record":
            ts, hr = values.get("timestamp"), values.get("heart_rate")
            if isinstance(ts, datetime) and isinstance(hr, int):
                records.append((ts, hr))

    families = [sport_family(str(s.get("sport") or "")) for s in sessions]
    default_family = families[0] if len(set(families)) == 1 and families else "other"

    laps = []
    for i, values in enumerate(laps_raw[:MAX_LAPS], start=1):
        family = sport_family(str(values.get("sport") or "")) if values.get("sport") else default_family
        laps.append(_lap(i, values, family))

    out: dict[str, Any] = {
        "sessions": [_session(s) for s in sessions],
        "laps": laps,
    }
    if len(laps_raw) > MAX_LAPS:
        out["laps_omitted"] = len(laps_raw) - MAX_LAPS
    zones = zone_time(records, hr_zone_floors)
    if zones:
        out["hr_zone_min_from_records"] = zones
    return clean(out)
