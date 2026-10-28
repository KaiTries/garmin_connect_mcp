"""Thin, thread-safe layer over Garmin's official Personal API client.

Handles the bits an MCP server needs and the raw client leaves to callers:
token exchange and renewal (24 h tokens, no refresh tokens), one retry after a
rejected token, splitting long date ranges into the windows each endpoint
accepts, and turning HTTP failures into messages a person can act on.
"""

from __future__ import annotations

import io
import threading
import zipfile
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any

import requests
from garmin_personal_api import (
    GarminPersonalApiClient,
    GarminPersonalApiError,
    HttpError,
    PersonalApiAuthenticator,
    resolve_api_key,
)

from .config import Settings

KEY_SETTINGS_URL = "https://connect.garmin.com/app/settings/personalApiToken"

# Longest inclusive range each endpoint accepts (documented by gcpapi-sync, which
# tracks Garmin's OpenAPI spec). Activity search pages are capped at 45.
MAX_DAYS = {
    "steps": 28,
    "stress": 28,
    "hydration": 31,
    "body_battery": 31,
    "sleep": 31,
    "daily_summaries": 31,
    "hrv": 365,
}
ACTIVITY_PAGE_SIZE = 45
MAX_FIT_BYTES = 64 * 1024 * 1024


class GarminError(Exception):
    """An error whose message is safe and useful to show to the model and the user."""


def windows(start: date, end: date, max_days: int) -> Iterator[tuple[date, date]]:
    """Split the inclusive range start..end into consecutive windows of at most max_days."""
    cursor = start
    while cursor <= end:
        window_end = min(end, cursor + timedelta(days=max_days - 1))
        yield cursor, window_end
        cursor = window_end + timedelta(days=1)


class GarminSource:
    def __init__(self, settings: Settings, *, session: requests.Session | None = None) -> None:
        self._settings = settings
        self._session = session or requests.Session()
        self._lock = threading.Lock()
        self._token: str | None = None
        self._authenticator = PersonalApiAuthenticator(
            timeout=settings.request_timeout, session=self._session
        )

    # -- auth -------------------------------------------------------------------------

    def _api_key(self) -> str:
        try:
            if self._settings.api_key_file is not None:
                return resolve_api_key(key_file=self._settings.api_key_file)
            return resolve_api_key()
        except GarminPersonalApiError as exc:
            raise GarminError(
                f"No usable Garmin Personal API key: {exc} Create one at {KEY_SETTINGS_URL}."
            ) from exc

    def _bearer(self, *, force_refresh: bool = False) -> str:
        with self._lock:
            if self._token is None or force_refresh:
                try:
                    token = self._authenticator.obtain_token(self._api_key(), force_refresh=force_refresh)
                except HttpError as exc:
                    if exc.status_code in (400, 401, 403):
                        raise GarminError(
                            "Garmin rejected the Personal API key (HTTP "
                            f"{exc.status_code}). It has probably expired or been deleted; "
                            f"create a new one at {KEY_SETTINGS_URL}."
                        ) from exc
                    raise GarminError(f"Could not obtain a Garmin access token: HTTP {exc.status_code}") from exc
                self._token = token.access_token
            return self._token

    def _call(self, fn: Callable[[GarminPersonalApiClient], Any]) -> Any:
        """Run fn with an authenticated client; renew the token once if Garmin rejects it."""
        for attempt in (0, 1):
            try:
                client = GarminPersonalApiClient(
                    self._bearer(force_refresh=attempt == 1),
                    timeout=self._settings.request_timeout,
                    session=self._session,
                )
                return fn(client)
            except HttpError as exc:
                if exc.status_code == 401 and attempt == 0:
                    continue
                raise _friendly(exc) from exc
            except requests.Timeout as exc:
                raise GarminError("Garmin did not answer in time; try again or narrow the date range.") from exc
            except requests.ConnectionError as exc:
                raise GarminError(f"Could not reach Garmin: {exc.__class__.__name__}") from exc
        raise AssertionError("unreachable")

    # -- generic ----------------------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._call(lambda c: c.get(path, params=params))

    def collect_range(
        self,
        kind: str,
        fetch: Callable[[GarminPersonalApiClient, date, date], Any],
        start: date,
        end: date,
        *,
        unwrap: Callable[[Any], list] | None = None,
    ) -> list:
        """Fetch start..end in windows the endpoint accepts and concatenate the lists."""
        rows: list = []
        for lo, hi in windows(start, end, MAX_DAYS[kind]):
            page = self._call(lambda c, lo=lo, hi=hi: fetch(c, lo, hi))
            if unwrap is not None:
                page = unwrap(page)
            if isinstance(page, list):
                rows.extend(page)
            elif page:
                rows.append(page)
        return rows

    # -- endpoints ---------------------------------------------------------------------

    def daily_summaries(self, start: date, end: date) -> list[dict]:
        return self.collect_range(
            "daily_summaries",
            lambda c, a, b: c.get_daily_summaries(a, b),
            start,
            end,
            unwrap=lambda p: (p or {}).get("data") or [] if isinstance(p, dict) else (p or []),
        )

    def sleep_summaries(self, start: date, end: date) -> list[dict]:
        return self.collect_range("sleep", lambda c, a, b: c.get_daily_sleep_summaries(a, b), start, end)

    def hrv_summaries(self, start: date, end: date) -> list[dict]:
        return self.collect_range(
            "hrv",
            lambda c, a, b: c.get_daily_hrv_summaries(a, b),
            start,
            end,
            unwrap=lambda p: (p or {}).get("hrvSummaries") or [] if isinstance(p, dict) else (p or []),
        )

    def weight_range(self, start: date, end: date) -> dict:
        """Per-day weight summaries plus the range average. Garmin reports masses in grams."""
        return self._call(lambda c: c.get_weight_measurements(start, end)) or {}

    def hydration(self, start: date, end: date) -> list[dict]:
        return self.collect_range("hydration", lambda c, a, b: c.get_daily_hydration(a, b), start, end)

    def daily_sleep(self, day: date) -> dict | None:
        return self._call(lambda c: c.get_daily_sleep(day))

    def daily_hrv(self, day: date) -> dict | None:
        return self._call(lambda c: c.get_daily_hrv(day))

    def body_battery(self, start: date, end: date) -> list[dict]:
        return self.collect_range("body_battery", lambda c, a, b: c.get_daily_body_battery(a, b), start, end)

    def food_logs(self, day: date) -> dict | None:
        return self._call(lambda c: c.get_daily_food_logs(day))

    def settings(self) -> dict | None:
        return self._call(lambda c: c.get_user_profile_settings())

    def heart_rate_zones(self) -> list[dict]:
        return self._call(lambda c: c.get_heart_rate_zones()) or []

    def activities(
        self, start: date, end: date, *, activity_type: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Newest-first activities in start..end, paging through Garmin's 45-per-page cap."""
        found: list[dict] = []
        offset = 0
        while len(found) < limit:
            size = min(ACTIVITY_PAGE_SIZE, limit - len(found))
            page = self._call(
                lambda c, offset=offset, size=size: c.search_activities(
                    start=offset, limit=size, start_date=start, end_date=end, activity_type=activity_type
                )
            ) or []
            found.extend(page)
            if len(page) < size:
                break
            offset += len(page)
        return found[:limit]

    def activity(self, activity_id: int) -> dict:
        return self._call(lambda c: c.get_activity(activity_id))

    def activity_workouts(self, activity_id: int) -> list[dict]:
        return self._call(lambda c: c.get_activity_workouts(activity_id)) or []

    def activity_fit(self, activity_id: int) -> bytes | None:
        """The activity's original FIT file, or None when the archive holds no FIT file."""
        archive = self._call(lambda c: c.download_activity(activity_id))
        try:
            bundle = zipfile.ZipFile(io.BytesIO(archive))
        except zipfile.BadZipFile:
            return archive if archive[8:12] == b".FIT" else None
        with bundle:
            for info in bundle.infolist():
                if info.filename.lower().endswith(".fit") and info.file_size <= MAX_FIT_BYTES:
                    return bundle.read(info)
        return None


def _friendly(exc: HttpError) -> GarminError:
    status = exc.status_code
    path = exc.url.split("connectapi.garmin.com", 1)[-1]
    if status in (401, 403):
        return GarminError(
            f"Garmin refused access to {path} (HTTP {status}). If this keeps happening the "
            f"Personal API key may have expired; create a new one at {KEY_SETTINGS_URL}."
        )
    if status == 404:
        return GarminError(f"Garmin has nothing at {path} (HTTP 404). Check the ID or date.")
    if status == 429:
        return GarminError("Garmin is rate limiting requests (HTTP 429). Wait a minute and retry with a smaller range.")
    if status == 400:
        detail = exc.response_text.strip()[:300]
        return GarminError(f"Garmin rejected the request to {path} (HTTP 400): {detail or 'no details'}")
    return GarminError(f"Garmin returned HTTP {status} for {path}.")
