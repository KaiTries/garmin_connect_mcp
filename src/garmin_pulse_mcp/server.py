"""MCP tools over the Garmin Personal API."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import transform as t
from .config import Settings
from .fit import summarize_fit
from .garmin import GarminError, GarminSource

INSTRUCTIONS = """\
Read-only access to the user's own Garmin Connect data through Garmin's official Personal API.

- Units are metric and named in every key (_km, _min, _bpm, _w, _ms, _kg). Run pace is min/km,
  swim pace min/100 m, other sports km/h.
- Dates are the user's local calendar dates (YYYY-MM-DD); "today" and "yesterday" also work.
  Sleep and HRV are filed under the date the user woke up.
- Start with get_recovery for readiness questions, list_activities / get_training_summary for
  training history, and get_activity for a single session (laps, legs of a triathlon, HR zones).
- Values come from Garmin unless a key says otherwise; totals in get_training_summary and
  hr_zone_min_from_records are computed by this server from Garmin's data.
- Garmin's Personal API has no training status, training readiness, VO2max trend or race
  predictor endpoints, and cannot create workouts. Say so rather than estimating them.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

DateArg = Annotated[str | None, Field(description="YYYY-MM-DD, 'today' or 'yesterday'")]

PATH_RE = re.compile(r"^/[A-Za-z0-9_\-./]+$")
RAW_LIMIT_CHARS = 40_000


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def build_server(settings: Settings, source: GarminSource | None = None) -> FastMCP:
    garmin = source or GarminSource(settings)
    mcp = FastMCP(
        "garmin",
        instructions=INSTRUCTIONS,
        host=settings.host,
        port=settings.port,
        stateless_http=True,
    )

    def parse_day(value: str | None, default: date) -> date:
        if value is None or not str(value).strip():
            return default
        text = str(value).strip().lower()
        today = settings.today()
        if text == "today":
            return today
        if text == "yesterday":
            return today - timedelta(days=1)
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise ToolError(f"Invalid date {value!r}; use YYYY-MM-DD, 'today' or 'yesterday'.") from exc

    def parse_range(start: str | None, end: str | None, default_days: int, max_days: int) -> tuple[date, date]:
        hi = parse_day(end, settings.today())
        lo = parse_day(start, hi - timedelta(days=default_days - 1))
        if lo > hi:
            raise ToolError(f"start_date {lo} is after end_date {hi}.")
        if (hi - lo).days + 1 > max_days:
            raise ToolError(f"Range is {(hi - lo).days + 1} days; this tool accepts at most {max_days}.")
        return lo, hi

    async def run(fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except GarminError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=READ_ONLY)
    async def get_recovery(start_date: DateArg = None, end_date: DateArg = None) -> str:
        """Day-by-day recovery picture: sleep (duration, score, stages, bedtime), overnight HRV and
        its 7-day average, resting HR, stress, Body Battery and steps, plus range averages.
        Defaults to the last 7 days; at most 92 days per call."""
        lo, hi = parse_range(start_date, end_date, 7, 92)
        daily, sleep, hrv = await asyncio.gather(
            run(garmin.daily_summaries, lo, hi),
            run(garmin.sleep_summaries, lo, hi),
            run(garmin.hrv_summaries, lo, hi),
        )
        return _json({"from": lo, "to": hi, **t.recovery(daily, sleep, hrv)})

    @mcp.tool(annotations=READ_ONLY)
    async def get_day_detail(date: DateArg = None) -> str:
        """Deeper look at one day: sleep score breakdown and Garmin's sleep insight, overnight
        HRV readings range, and the Body Battery events (sleep charge, activities, stress) that
        explain the day's drain. Defaults to today."""
        day = parse_day(date, settings.today())
        sleep, hrv, bb, daily = await asyncio.gather(
            run(garmin.daily_sleep, day),
            run(garmin.daily_hrv, day),
            run(garmin.body_battery, day, day),
            run(garmin.daily_summaries, day, day),
        )
        out: dict[str, Any] = {"date": day}
        if daily:
            out.update(t.daily_row(daily[0]))
        if sleep:
            out["sleep"] = t.sleep_row(sleep)
            scores = t.g(sleep, "sleepScores") or {}
            out["sleep"]["subscores"] = {
                k: t.clean({"value": t.g(v, "value"), "rating": t.g(v, "qualifierKey")})
                for k, v in scores.items()
                if isinstance(v, dict) and k != "overall"
            }
            out["sleep"] = t.clean(
                {**out["sleep"], "insight": t.first(sleep, "sleepScorePersonalizedInsight", "sleepScoreInsight")}
            )
        if hrv:
            readings = [r.get("hrvValue") for r in t.g(hrv, "hrvReadings") or [] if isinstance(r, dict)]
            readings = [r for r in readings if isinstance(r, (int, float))]
            out["hrv"] = t.clean(
                {
                    **t.hrv_row(t.g(hrv, "hrvSummary") or {}),
                    "readings": len(readings) or None,
                    "min_ms": min(readings) if readings else None,
                    "max_ms": max(readings) if readings else None,
                }
            )
        events = []
        for report in bb or []:
            for e in t.g(report, "bodyBatteryActivityEvent") or []:
                events.append(
                    t.clean(
                        {
                            "type": t.g(e, "eventType"),
                            "start_utc": t.g(e, "eventStartTimeGmt"),
                            "duration_min": t.minutes((t.g(e, "durationInMilliseconds") or 0) / 1000, 0),
                            "impact": t.g(e, "bodyBatteryImpact"),
                            "activity": t.g(e, "activityName"),
                            "feedback": t.g(e, "shortFeedback"),
                        }
                    )
                )
            feedback = t.g(report, "bodyBatteryDynamicFeedbackEvent", "feedbackLongType") or t.g(
                report, "endOfDayBodyBatteryDynamicFeedbackEvent", "feedbackLongType"
            )
            if feedback:
                out["body_battery_feedback"] = feedback
        if events:
            out["body_battery_events"] = events
        return _json(t.clean(out))

    @mcp.tool(annotations=READ_ONLY)
    async def list_activities(
        start_date: DateArg = None,
        end_date: DateArg = None,
        activity_type: Annotated[
            str | None,
            Field(description="Garmin type key filter, e.g. running, cycling, swimming, multi_sport, strength_training"),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 30,
    ) -> str:
        """Activities newest first, one compact row each: duration, distance, pace/speed, HR,
        power, TSS/IF, training load, training effect and time in HR zones.
        Defaults to the last 30 days."""
        lo, hi = parse_range(start_date, end_date, 30, 3660)
        rows = await run(garmin.activities, lo, hi, activity_type=activity_type, limit=limit)
        return _json({"from": lo, "to": hi, "count": len(rows), "activities": [t.activity_row(a) for a in rows]})

    @mcp.tool(annotations=READ_ONLY)
    async def get_activity(
        activity_id: Annotated[int, Field(gt=0, description="Garmin activity ID from list_activities")],
        include_laps: Annotated[bool, Field(description="Download and parse the FIT file for laps, legs and HR zones")] = True,
    ) -> str:
        """One activity in detail: summary metrics, the planned workout it followed, and (from the
        FIT file) each leg of a multisport session, every lap, and minutes per HR zone."""
        detail, workouts = await asyncio.gather(
            run(garmin.activity, activity_id), run(garmin.activity_workouts, activity_id)
        )
        out: dict[str, Any] = {"id": activity_id, **t.activity_detail(detail)}
        if workouts:
            out["planned_workout"] = t.planned_workout(workouts)
        if include_laps:
            try:
                fit_bytes, zones = await asyncio.gather(
                    run(garmin.activity_fit, activity_id), run(garmin.heart_rate_zones)
                )
                if fit_bytes:
                    floors = _zone_floors(zones, out.get("sport"))
                    out.update(await asyncio.to_thread(summarize_fit, fit_bytes, floors))
                else:
                    out["laps_note"] = "Garmin's download held no FIT file for this activity."
            except ToolError as exc:
                out["laps_note"] = f"Could not load the FIT file: {exc}"
            except Exception as exc:  # malformed FIT: keep the rest of the answer
                out["laps_note"] = f"Could not parse the FIT file ({exc.__class__.__name__})."
        return _json(out)

    @mcp.tool(annotations=READ_ONLY)
    async def get_training_summary(
        start_date: DateArg = None,
        end_date: DateArg = None,
        group_by: Literal["week", "month", "day"] = "week",
    ) -> str:
        """Training volume per week (or month/day) split by sport: sessions, hours, distance,
        elevation, training load, TSS and HR-zone minutes. Computed by this server from the
        activity list. Defaults to the last 8 full ISO weeks including the current one."""
        today = settings.today()
        default_start = today - timedelta(days=today.weekday()) - timedelta(weeks=7)
        hi = parse_day(end_date, today)
        lo = parse_day(start_date, default_start)
        if lo > hi or (hi - lo).days > 366:
            raise ToolError("Use a range of at most 366 days with start_date before end_date.")
        rows = await run(garmin.activities, lo, hi, limit=1000)
        summary = t.training_summary(rows, group_by)
        return _json({"from": lo, "to": hi, "activities": len(rows), **summary})

    @mcp.tool(annotations=READ_ONLY)
    async def get_body_composition(start_date: DateArg = None, end_date: DateArg = None) -> str:
        """Weigh-ins per day (weight, body fat, muscle, water, BMI) and the range average.
        Defaults to the last 90 days."""
        lo, hi = parse_range(start_date, end_date, 90, 3660)
        payload = await run(garmin.weight_range, lo, hi)
        return _json({"from": lo, "to": hi, **t.body_composition(payload)})

    @mcp.tool(annotations=READ_ONLY)
    async def get_nutrition(date: DateArg = None) -> str:
        """Food log for one day from Garmin Connect nutrition (totals, goals, meals) plus
        hydration intake and estimated sweat loss. Defaults to today."""
        day = parse_day(date, settings.today())
        food, water = await asyncio.gather(run(garmin.food_logs, day), run(garmin.hydration, day, day))
        return _json({"date": day, **t.nutrition(food, water)})

    @mcp.tool(annotations=READ_ONLY)
    async def get_profile() -> str:
        """User settings relevant to interpreting data: units, time zone, training days and
        heart-rate zones per sport."""
        settings_json, zones = await asyncio.gather(run(garmin.settings), run(garmin.heart_rate_zones))
        out = t.profile(settings_json, zones)
        if settings.key_expires:
            days_left = (settings.key_expires - settings.today()).days
            out["api_key_expires"] = settings.key_expires
            if days_left <= 14:
                out["api_key_warning"] = f"The Garmin Personal API key expires in {days_left} days."
        return _json(out)

    @mcp.tool(annotations=READ_ONLY)
    async def garmin_api_get(
        path: Annotated[str, Field(description="Personal API path, e.g. /hrv-service/hrv/2026-10-27")],
        params: Annotated[dict[str, str] | None, Field(description="Query parameters")] = None,
    ) -> str:
        """Escape hatch: GET any Personal API path and return the JSON, squeezed (nulls and
        image/ID noise removed, long lists cut). Use only when no other tool covers the data,
        e.g. /usersummary-service/stats/stress/daily/{start}/{end},
        /wellness-service/wellness/dailyHeartRate?date=…,
        /wellness-service/wellness/daily/spo2acclimation/{date},
        /fitnessage-service/fitnessage/v2/{date}, /goal-service/goal/user/effective/weightgoal/{start}/{end}."""
        if not PATH_RE.match(path) or ".." in path:
            raise ToolError("path must be an absolute Personal API path like /hrv-service/hrv/2026-10-27")
        if path.startswith(("/personalapitoken-service", "/download-service")):
            raise ToolError("Token and file-download paths are not available through this tool.")
        data = await run(garmin.get, path, params or None)
        text = _json(t.compact(data))
        if len(text) > RAW_LIMIT_CHARS:
            text = text[:RAW_LIMIT_CHARS] + f"… [truncated, {len(text) - RAW_LIMIT_CHARS} more characters]"
        return text

    return mcp


def _zone_floors(zones: list[dict], sport: str | None) -> list[int] | None:
    """Pick the HR zone floors for the activity's sport, falling back to the default zones."""
    wanted = {"run": "RUNNING", "bike": "CYCLING", "swim": "SWIMMING"}.get(sport or "")
    chosen = None
    for z in zones or []:
        if not isinstance(z, dict):
            continue
        z_sport = z.get("sport")
        if wanted and z_sport == wanted:
            chosen = z
            break
        if z_sport in (None, "DEFAULT") and chosen is None:
            chosen = z
    if chosen is None:
        return None
    floors = [chosen.get(f"zone{i}Floor") for i in range(1, 6)]
    return floors if all(isinstance(f, int) for f in floors) else None
