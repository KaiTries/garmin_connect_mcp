"""Turn Garmin's large JSON payloads into compact, unit-labelled rows for an LLM.

Field names follow Garmin's Personal API (camelCase, as in the OpenAPI document).
Every output key carries its unit (``_km``, ``_min``, ``_bpm``…) so the model never
has to guess, and absent values are dropped instead of sent as nulls.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from statistics import mean
from typing import Any

Json = Any


# -- small helpers ---------------------------------------------------------------------


def g(obj: Json, *path: str, default: Any = None) -> Any:
    """Nested get that tolerates missing keys and non-dict values."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def first(obj: Json, *keys: str) -> Any:
    for key in keys:
        value = g(obj, key)
        if value is not None:
            return value
    return None


def rnd(value: Any, digits: int = 1) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        out = round(float(value), digits)
        return int(out) if digits == 0 or out.is_integer() else out
    return value


def minutes(seconds: Any, digits: int = 1) -> float | None:
    return rnd(seconds / 60, digits) if isinstance(seconds, (int, float)) else None


def hours(seconds: Any) -> float | None:
    return rnd(seconds / 3600, 2) if isinstance(seconds, (int, float)) else None


def clean(row: dict) -> dict:
    """Drop None, empty containers and negative 'no data' sentinels Garmin uses for some stats."""
    return {k: v for k, v in row.items() if v is not None and v != [] and v != {} and v != ""}


def compact(value: Json, *, max_list: int = 50, depth: int = 0) -> Json:
    """Generic squeeze for payloads without a hand-written transform."""
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if v is None or v == [] or v == {}:
                continue
            if any(t in k.lower() for t in ("profileimage", "imageurl", "userprofilepk", "ownerid", "uuid")):
                continue
            out[k] = compact(v, max_list=max_list, depth=depth + 1)
        return out
    if isinstance(value, list):
        items = [compact(v, max_list=max_list, depth=depth + 1) for v in value[:max_list]]
        if len(value) > max_list:
            items.append(f"… {len(value) - max_list} more items omitted")
        return items
    if isinstance(value, float):
        return rnd(value, 2)
    return value


def local_clock(value: Any) -> str | None:
    """Garmin 'Local' timestamps: epoch-ms of local wall time, or 'YYYY-MM-DD HH:MM:SS' strings."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")
    if isinstance(value, str) and value:
        return value.replace("T", " ")[:16]
    return None


def nonneg(value: Any) -> Any:
    """Garmin reports -1/-2 for 'not enough data' in stress and similar fields."""
    return value if isinstance(value, (int, float)) and value >= 0 else None


# -- recovery -----------------------------------------------------------------------------


def sleep_row(s: dict) -> dict:
    scores = g(s, "sleepScores") or {}
    sleep_s = g(s, "sleepTimeSeconds")
    return clean(
        {
            "sleep_h": hours(sleep_s),
            "sleep_score": g(scores, "overall", "value"),
            "sleep_quality": g(scores, "overall", "qualifierKey"),
            "deep_min": minutes(g(s, "deepSleepSeconds"), 0),
            "light_min": minutes(g(s, "lightSleepSeconds"), 0),
            "rem_min": minutes(g(s, "remSleepSeconds"), 0),
            "awake_min": minutes(g(s, "awakeSleepSeconds"), 0),
            "awakenings": g(s, "awakeCount"),
            "bedtime": local_clock(g(s, "sleepStartTimestampLocal")),
            "wake": local_clock(g(s, "sleepEndTimestampLocal")),
            "nap_min": minutes(g(s, "napTimeSeconds"), 0) or None,
            "sleep_need_min": g(s, "sleepNeed", "actual"),
            "sleep_hr_bpm": rnd(g(s, "avgHeartRate")),
            "sleep_stress": rnd(nonneg(g(s, "avgSleepStress"))),
            "sleep_resp_brpm": rnd(g(s, "averageRespirationValue")),
            "sleep_spo2_pct": rnd(g(s, "averageSpO2Value")),
            "sleep_feedback": g(s, "sleepScoreFeedback"),
        }
    )


def hrv_row(h: dict) -> dict:
    return clean(
        {
            "hrv_ms": g(h, "lastNightAvg"),
            "hrv_5min_high_ms": g(h, "lastNight5MinHigh"),
            "hrv_7d_avg_ms": g(h, "weeklyAvg"),
            "hrv_feedback": g(h, "feedbackPhrase"),
        }
    )


def daily_row(d: dict) -> dict:
    return clean(
        {
            "resting_hr_bpm": g(d, "heartRate", "restingValue"),
            "stress_avg": nonneg(g(d, "stress", "avgLevel")),
            "stress_max": nonneg(g(d, "stress", "maxLevel")),
            "stress_qualifier": g(d, "stress", "qualifier"),
            "bb_max": g(d, "bodyBattery", "maxValue"),
            "bb_min": g(d, "bodyBattery", "minValue"),
            "bb_charged": g(d, "bodyBattery", "chargedValue"),
            "bb_drained": g(d, "bodyBattery", "drainedValue"),
            "bb_before_sleep": g(d, "bodyBattery", "endOfDayValue"),
            "intensity_moderate_min": g(d, "intensityMinutes", "moderate"),
            "intensity_vigorous_min": g(d, "intensityMinutes", "vigorous"),
            "steps": g(d, "movement", "steps"),
            "resp_avg_brpm": rnd(g(d, "respiration", "avgValue")),
            "spo2_avg_pct": g(d, "pulseOx", "avgValue"),
        }
    )


def _date_key(obj: dict) -> str | None:
    value = first(obj, "calendarDate", "summaryDate")
    return str(value)[:10] if value else None


def recovery(daily: list[dict], sleep: list[dict], hrv: list[dict]) -> dict:
    """One row per calendar date merging daily summary, last night's sleep and HRV."""
    days: dict[str, dict] = defaultdict(dict)
    for items, fn in ((daily, daily_row), (sleep, sleep_row), (hrv, hrv_row)):
        for item in items or []:
            if isinstance(item, dict) and (key := _date_key(item)):
                days[key].update(fn(item))
    rows = [{"date": k, **v} for k, v in sorted(days.items()) if v]
    return {"days": rows, "averages": averages(rows)}


AVERAGE_KEYS = (
    "sleep_h",
    "sleep_score",
    "hrv_ms",
    "resting_hr_bpm",
    "stress_avg",
    "bb_max",
    "bb_before_sleep",
    "steps",
)


def averages(rows: list[dict]) -> dict:
    out = {}
    for key in AVERAGE_KEYS:
        values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        if values:
            out[key] = rnd(mean(values), 2 if key == "sleep_h" else 1)
            out[f"{key}_days"] = len(values)
    return out


# -- activities ---------------------------------------------------------------------------

SWIM, BIKE, RUN = "swim", "bike", "run"


def sport_family(type_key: str | None) -> str:
    key = (type_key or "").lower()
    if not key:
        return "other"
    if "swim" in key:
        return SWIM
    if any(t in key for t in ("cycling", "biking", "ride", "bike", "gravel", "e_bike")):
        return BIKE
    if any(t in key for t in ("running", "run", "treadmill", "track")):
        return RUN
    if any(t in key for t in ("multi_sport", "triathlon", "duathlon", "transition")):
        return "multisport"
    if "strength" in key or "hiit" in key:
        return "strength"
    return "other"


def pace(speed_mps: Any, family: str) -> dict:
    """Run: min/km; swim: min/100 m; everything else: km/h."""
    if not isinstance(speed_mps, (int, float)) or speed_mps <= 0:
        return {}
    if family == RUN:
        sec = 1000 / speed_mps
        return {"pace_per_km": f"{int(sec // 60)}:{int(round(sec % 60)):02d}"}
    if family == SWIM:
        sec = 100 / speed_mps
        return {"pace_per_100m": f"{int(sec // 60)}:{int(round(sec % 60)):02d}"}
    return {"speed_kmh": rnd(speed_mps * 3.6)}


def zone_minutes(obj: dict, prefix: str, count: int) -> list | None:
    values = [g(obj, f"{prefix}{i}") for i in range(1, count + 1)]
    if all(v is None for v in values):
        return None
    return [minutes(v or 0, 0) for v in values]


def activity_row(a: dict) -> dict:
    type_key = g(a, "activityType", "typeKey")
    family = sport_family(type_key)
    duration = first(a, "duration", "elapsedDuration")
    row = {
        "id": g(a, "activityId"),
        "start": local_clock(g(a, "startTimeLocal")),
        "name": g(a, "activityName"),
        "type": type_key,
        "sport": family,
        "duration_min": minutes(duration),
        "moving_min": minutes(g(a, "movingDuration")),
        "distance_km": rnd(g(a, "distance") / 1000, 2) if isinstance(g(a, "distance"), (int, float)) else None,
        "elev_gain_m": rnd(g(a, "elevationGain"), 0),
        "avg_hr_bpm": rnd(g(a, "averageHR"), 0),
        "max_hr_bpm": rnd(g(a, "maxHR"), 0),
        "avg_power_w": rnd(g(a, "avgPower"), 0),
        "np_w": rnd(g(a, "normPower"), 0),
        "tss": rnd(g(a, "trainingStressScore")),
        "if": rnd(g(a, "intensityFactor"), 2),
        "training_load": rnd(g(a, "activityTrainingLoad")),
        "aerobic_te": rnd(g(a, "aerobicTrainingEffect")),
        "anaerobic_te": rnd(g(a, "anaerobicTrainingEffect")),
        "te_label": g(a, "trainingEffectLabel"),
        "hr_zone_min": zone_minutes(a, "hrTimeInZone_", 5),
        "power_zone_min": zone_minutes(a, "powerTimeInZone_", 7),
        "calories": rnd(g(a, "calories"), 0),
        "avg_swolf": rnd(g(a, "averageSwolf"), 0),
        "pool_length_m": rnd(g(a, "poolLength"), 0) if family == SWIM else None,
        "multisport_parent": True if g(a, "isParent") else None,
        "parent_id": g(a, "parentId"),
        "body_battery_delta": g(a, "differenceBodyBattery"),
    }
    row.update(pace(first(a, "averageSpeed"), family))
    return clean(row)


def activity_detail(detail: dict) -> dict:
    """GET /activity-service/activity/{id}: header plus summaryDTO."""
    s = g(detail, "summaryDTO") or {}
    type_key = g(detail, "activityTypeDTO", "typeKey")
    family = sport_family(type_key)
    row = {
        "name": g(detail, "activityName"),
        "type": type_key,
        "sport": family,
        "description": g(detail, "description"),
        "location": g(detail, "locationName"),
        "multisport_parent": True if g(detail, "isMultiSportParent") else None,
        "parent_id": g(detail, "parentId"),
        "start": local_clock(g(s, "startTimeLocal")),
        "duration_min": minutes(g(s, "duration")),
        "moving_min": minutes(g(s, "movingDuration")),
        "distance_km": rnd(g(s, "distance") / 1000, 2) if isinstance(g(s, "distance"), (int, float)) else None,
        "elev_gain_m": rnd(g(s, "elevationGain"), 0),
        "avg_hr_bpm": rnd(g(s, "averageHR"), 0),
        "max_hr_bpm": rnd(g(s, "maxHR"), 0),
        "avg_power_w": rnd(g(s, "averagePower"), 0),
        "np_w": rnd(g(s, "normalizedPower"), 0),
        "max_20min_power_w": rnd(g(s, "maxPowerTwentyMinutes"), 0),
        "ftp_w": g(s, "functionalThresholdPower"),
        "tss": rnd(g(s, "trainingStressScore")),
        "if": rnd(g(s, "intensityFactor"), 2),
        "aerobic_te": rnd(g(s, "trainingEffect")),
        "anaerobic_te": rnd(g(s, "anaerobicTrainingEffect")),
        "te_message": g(s, "aerobicTrainingEffectMessage"),
        "avg_run_cadence_spm": rnd(g(s, "averageRunCadence"), 0),
        "avg_bike_cadence_rpm": rnd(g(s, "averageBikeCadence"), 0),
        "avg_swim_cadence_spm": rnd(g(s, "averageSwimCadence"), 0),
        "ground_contact_ms": rnd(g(s, "groundContactTime"), 0),
        "vertical_osc_cm": rnd(g(s, "verticalOscillation")),
        "stride_m": rnd(g(s, "strideLength") / 100, 2) if isinstance(g(s, "strideLength"), (int, float)) else None,
        "lr_balance": rnd(g(s, "leftBalance")),
        "lactate_threshold_hr_bpm": g(s, "lactateThresholdHeartRate"),
        "pool_length_m": rnd(g(s, "poolLength"), 0) if family == SWIM else None,
        "active_lengths": g(s, "numberOfActiveLengths"),
        "avg_swolf": rnd(g(s, "averageSWOLF"), 0),
        "temp_avg_c": rnd(g(s, "averageTemperature")),
        "calories": rnd(g(s, "calories"), 0),
    }
    row.update(pace(first(s, "averageMovingSpeed", "averageSpeed"), family))
    splits = []
    for sp in g(detail, "splitSummaries") or []:
        splits.append(
            clean(
                {
                    "type": g(sp, "splitType"),
                    "count": g(sp, "noOfSplits"),
                    "duration_min": minutes(g(sp, "duration")),
                    "distance_km": rnd(g(sp, "distance") / 1000, 2)
                    if isinstance(g(sp, "distance"), (int, float))
                    else None,
                }
            )
        )
    row["split_summaries"] = splits
    return clean(row)


def planned_workout(workouts: list[dict]) -> list[dict]:
    out = []
    for w in workouts or []:
        out.append(
            clean(
                {
                    "name": g(w, "workoutName"),
                    "sport": g(w, "sport"),
                    "summary": g(w, "workoutPhrase"),
                    "steps": compact(g(w, "steps"), max_list=30),
                }
            )
        )
    return out


# -- training summary -----------------------------------------------------------------


def period_key(day: date, group_by: str) -> str:
    if group_by == "month":
        return day.strftime("%Y-%m")
    if group_by == "day":
        return day.isoformat()
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def training_summary(activities: list[dict], group_by: str = "week") -> dict:
    """Totals per period and sport, computed locally from the activity list."""
    periods: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    for a in activities:
        if g(a, "parentId"):
            continue  # multisport child; the parent already carries the totals
        start = str(g(a, "startTimeLocal") or "")[:10]
        try:
            day = date.fromisoformat(start)
        except ValueError:
            continue
        sport = sport_family(g(a, "activityType", "typeKey"))
        bucket = periods[period_key(day, group_by)][sport]
        bucket["count"] += 1
        bucket["hours"] += (first(a, "duration", "elapsedDuration") or 0) / 3600
        bucket["distance_km"] += (g(a, "distance") or 0) / 1000
        bucket["elev_gain_m"] += g(a, "elevationGain") or 0
        bucket["training_load"] += g(a, "activityTrainingLoad") or 0
        bucket["tss"] += g(a, "trainingStressScore") or 0
        for i in range(1, 6):
            bucket[f"hr_z{i}_min"] += (g(a, f"hrTimeInZone_{i}") or 0) / 60

    out = []
    for key in sorted(periods):
        sports = {}
        total = defaultdict(float)
        for sport, vals in sorted(periods[key].items()):
            sports[sport] = {k: rnd(v, 1) for k, v in vals.items() if v}
            for k, v in vals.items():
                total[k] += v
        zones = [rnd(total.get(f"hr_z{i}_min", 0), 0) for i in range(1, 6)]
        total_row = {k: rnd(v, 1) for k, v in total.items() if v and not k.startswith("hr_z")}
        if any(zones):
            total_row["hr_zone_min"] = zones
        for s in sports.values():
            z = [s.pop(f"hr_z{i}_min", 0) for i in range(1, 6)]
            if any(z):
                s["hr_zone_min"] = [rnd(v, 0) for v in z]
        out.append({"period": key, "total": total_row, "by_sport": sports})
    return {"group_by": group_by, "periods": out}


# -- body, nutrition, profile ------------------------------------------------------------


def _kg(grams: Any) -> float | None:
    return rnd(grams / 1000, 1) if isinstance(grams, (int, float)) else None


def weight_entry(w: dict) -> dict:
    return clean(
        {
            "weight_kg": _kg(g(w, "weight")),
            "bmi": rnd(g(w, "bmi")),
            "body_fat_pct": rnd(g(w, "bodyFat")),
            "body_water_pct": rnd(g(w, "bodyWater")),
            "muscle_kg": _kg(g(w, "muscleMass")),
            "bone_kg": _kg(g(w, "boneMass")),
            "visceral_fat": g(w, "visceralFat"),
            "source": g(w, "sourceType"),
        }
    )


def body_composition(payload: dict) -> dict:
    days = []
    for summary in g(payload, "dailyWeightSummaries") or []:
        latest = g(summary, "latestWeight") or {}
        days.append({"date": _date_key(summary), **weight_entry(latest)})
    days.sort(key=lambda r: r.get("date") or "")
    avg = g(payload, "totalAverage") or {}
    return clean({"days": days, "range_average": weight_entry(avg)})


def nutrition(food: dict | None, hydration: list[dict]) -> dict:
    content = g(food, "dailyNutritionContent") or {}
    goals = g(food, "dailyNutritionGoals") or {}
    meals = []
    for meal in g(food, "mealDetails") or []:
        meals.append(compact(meal, max_list=20))
    water = next((h for h in hydration or [] if isinstance(h, dict)), {})
    return clean(
        {
            "totals": compact(content),
            "goals": compact(goals),
            "meals": meals,
            "hydration": clean(
                {
                    "intake_ml": rnd(g(water, "valueInML"), 0),
                    "goal_ml": rnd(g(water, "goalInML"), 0),
                    "sweat_loss_ml": rnd(g(water, "sweatLossInML"), 0),
                }
            ),
        }
    )


def hr_zones(zones: list[dict]) -> list[dict]:
    out = []
    for z in zones or []:
        floors = [g(z, f"zone{i}Floor") for i in range(1, 6)]
        out.append(
            clean(
                {
                    "sport": g(z, "sport") or "DEFAULT",
                    "method": g(z, "trainingMethod"),
                    "zone_floors_bpm": floors if any(f is not None for f in floors) else None,
                    "max_hr_bpm": g(z, "maxHeartRateUsed"),
                    "resting_hr_bpm": g(z, "restingHeartRateUsed"),
                    "lthr_bpm": g(z, "lactateThresholdHeartRateUsed"),
                }
            )
        )
    return out


def profile(settings: dict | None, zones: list[dict]) -> dict:
    return clean(
        {
            "display_name": g(settings, "displayName"),
            "measurement_system": g(settings, "measurementSystem"),
            "time_zone": g(settings, "timeZone"),
            "first_day_of_week": compact(g(settings, "firstDayOfWeek")),
            "training_days": g(settings, "availableTrainingDays"),
            "long_training_days": g(settings, "preferredLongTrainingDays"),
            "heart_rate_zones": hr_zones(zones),
        }
    )
