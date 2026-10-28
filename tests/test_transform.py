from garmin_pulse_mcp import transform as t
from garmin_pulse_mcp.fit import summarize_fit, zone_time
from tests import fixtures as f
from tests.conftest import build_brick_fit


def test_recovery_merges_sources_by_date():
    out = t.recovery(f.DAILY_SUMMARIES["data"], f.SLEEP_SUMMARIES, f.HRV_SUMMARIES["hrvSummaries"])
    days = {d["date"]: d for d in out["days"]}
    assert list(days) == ["2026-10-26", "2026-10-27"]

    d26 = days["2026-10-26"]
    assert d26["resting_hr_bpm"] == 46
    assert d26["bb_before_sleep"] == 21
    assert d26["hrv_ms"] == 58 and d26["hrv_7d_avg_ms"] == 61
    assert "sleep_h" not in d26

    d27 = days["2026-10-27"]
    assert d27["sleep_h"] == 7.25
    assert d27["sleep_score"] == 81 and d27["sleep_quality"] == "GOOD"
    assert d27["deep_min"] == 90 and d27["rem_min"] == 120
    assert d27["bedtime"] == "2026-10-26 22:30"
    assert d27["sleep_need_min"] == 480
    # -1/-2 sentinels must not leak through as stress values
    assert "stress_avg" not in d27 and "stress_max" not in d27

    assert out["averages"]["resting_hr_bpm"] == 47.5
    assert out["averages"]["hrv_ms"] == 55
    assert out["averages"]["sleep_h_days"] == 1


def test_activity_row_run_pace_zones_and_noise_removed():
    row = t.activity_row(f.RUN)
    assert row["sport"] == "run"
    assert row["pace_per_km"] == "4:59"
    assert row["distance_km"] == 10.02
    assert row["duration_min"] == 50
    assert row["hr_zone_min"] == [5, 35, 10, 0, 0]
    assert "power_zone_min" not in row
    assert not any("image" in k.lower() for k in row)


def test_activity_row_swim_and_bike():
    swim = t.activity_row(f.SWIM)
    assert swim["sport"] == "swim" and swim["pace_per_100m"] == "1:48"
    assert swim["pool_length_m"] == 25
    ride = t.activity_row(f.RIDE)
    assert ride["sport"] == "bike" and ride["speed_kmh"] == 32.0
    assert ride["np_w"] == 214 and ride["if"] == 0.81


def test_training_summary_groups_by_iso_week_and_skips_multisport_children():
    out = t.training_summary([f.RUN, f.SWIM, f.RIDE, f.TRI_CHILD], "week")
    periods = {p["period"]: p for p in out["periods"]}
    assert set(periods) == {"2026-W43", "2026-W44"}
    w44 = periods["2026-W44"]  # Mon 26 Oct - Sun 1 Nov
    assert set(w44["by_sport"]) == {"run", "swim"}
    assert w44["total"]["count"] == 2
    assert w44["total"]["hours"] == 1.6
    assert w44["by_sport"]["run"]["hr_zone_min"] == [5, 35, 10, 0, 0]
    w43 = periods["2026-W43"]
    assert w43["by_sport"]["bike"]["count"] == 1  # child of the tri not double counted
    assert w43["by_sport"]["bike"]["tss"] == 63.2


def test_body_composition_converts_grams_and_sorts():
    out = t.body_composition(f.WEIGHT_RANGE)
    assert [d["date"] for d in out["days"]] == ["2026-10-20", "2026-10-25"]
    assert out["days"][1]["weight_kg"] == 72.5
    assert out["days"][1]["muscle_kg"] == 35.2
    assert out["range_average"] == {"weight_kg": 72.7, "bmi": 22.4}


def test_sport_family():
    assert t.sport_family("trail_running") == "run"
    assert t.sport_family("open_water_swimming") == "swim"
    assert t.sport_family("indoor_cycling") == "bike"
    assert t.sport_family("multi_sport") == "multisport"
    assert t.sport_family("strength_training") == "strength"
    assert t.sport_family(None) == "other"


def test_compact_drops_nulls_and_truncates():
    data = {"a": None, "b": [], "c": list(range(60)), "ownerProfileImageUrl": "x", "d": 1.23456}
    out = t.compact(data)
    assert out["d"] == 1.23
    assert len(out["c"]) == 51 and out["c"][-1].startswith("… 10 more")
    assert set(out) == {"c", "d"}


def test_fit_brick_sessions_laps_and_zone_time():
    out = summarize_fit(build_brick_fit(), [110, 130, 145, 160, 172])
    sessions = out["sessions"]
    assert [s["sport"] for s in sessions] == ["cycling", "running"]
    assert sessions[0]["np_w"] == 205 and sessions[0]["tss"] == 38.5
    assert sessions[1]["pace_per_km"] == "5:00"
    laps = out["laps"]
    assert len(laps) == 3
    assert laps[0]["avg_power_w"] == 180 and "speed_kmh" in laps[0]
    assert laps[2]["pace_per_km"] == "5:00"
    # 20 min at 125 (Z1), 10 at 150 (Z3), 10 at 165 (Z4); the last record has no successor
    assert out["hr_zone_min_from_records"] == [20.0, 0.0, 10.0, 10.0, 0.0]


def test_zone_time_ignores_pauses_and_missing_zones():
    from datetime import datetime, timedelta

    t0 = datetime(2026, 10, 27)
    # 6 minutes at HR 140 sampled every 5 s, then a 10-minute pause, then one more sample
    recs = [(t0 + timedelta(seconds=5 * i), 140) for i in range(73)]
    recs.append((recs[-1][0] + timedelta(minutes=10), 140))
    floors = [110, 130, 145, 160, 172]
    assert zone_time(recs, floors) == [0.0, 6.0, 0.0, 0.0, 0.0]
    assert zone_time(recs, None) is None
    assert zone_time(recs, [110, 130, None, 160, 172]) is None
