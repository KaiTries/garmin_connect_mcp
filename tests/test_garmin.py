import io
import zipfile
from datetime import date

import pytest
import responses
from responses import matchers

from garmin_connect_mcp.config import Settings
from garmin_connect_mcp.garmin import GarminError, GarminSource, windows
from tests.conftest import BASE, TOKEN_URL, fake_jwt

SLEEP_URL = BASE + "/sleep-service/sleep/dailySleepSummariesByDate"
SEARCH_URL = BASE + "/activitylist-service/activities/search/activities"


def test_windows_split_inclusive_ranges():
    parts = list(windows(date(2026, 9, 1), date(2026, 10, 15), 31))
    assert parts == [(date(2026, 9, 1), date(2026, 10, 1)), (date(2026, 10, 2), date(2026, 10, 15))]
    assert list(windows(date(2026, 1, 1), date(2026, 1, 1), 28)) == [(date(2026, 1, 1), date(2026, 1, 1))]


@responses.activate
def test_token_exchange_sends_api_key_and_bearer_is_used():
    responses.post(TOKEN_URL, body=fake_jwt(), match=[matchers.header_matcher({"Authorization": "ApiKey test-key"})])
    responses.get(BASE + "/userprofile-service/userprofile/settings", json={"displayName": "kai"})
    assert GarminSource(Settings()).settings() == {"displayName": "kai"}
    assert responses.calls[1].request.headers["Authorization"].startswith("Bearer ")


@responses.activate
def test_rejected_token_is_renewed_once():
    responses.post(TOKEN_URL, body=fake_jwt(tag="old"))
    responses.post(TOKEN_URL, body=fake_jwt(tag="new"))
    url = BASE + "/biometric-service/heartRateZones/v2"
    responses.get(url, status=401)
    responses.get(url, json=[{"zone1Floor": 100}])
    assert GarminSource(Settings()).heart_rate_zones() == [{"zone1Floor": 100}]
    assert [c.request.method for c in responses.calls] == ["POST", "GET", "POST", "GET"]


@responses.activate
def test_expired_key_gives_actionable_message():
    responses.post(TOKEN_URL, status=401, body="invalid key")
    with pytest.raises(GarminError, match="expired or been deleted.*personalApiToken"):
        GarminSource(Settings()).settings()


@responses.activate
def test_network_failure_during_token_exchange_is_reported_cleanly():
    import requests

    responses.post(TOKEN_URL, body=requests.ConnectionError("proxy said no"))
    with pytest.raises(GarminError, match="^Could not reach Garmin: ConnectionError$"):
        GarminSource(Settings()).settings()


def test_missing_key_gives_actionable_message(monkeypatch):
    monkeypatch.delenv("GARMIN_PERSONAL_API_KEY")
    with pytest.raises(GarminError, match="No usable Garmin Personal API key"):
        GarminSource(Settings()).settings()


@responses.activate
def test_sleep_range_is_split_into_31_day_windows():
    responses.post(TOKEN_URL, body=fake_jwt())
    responses.get(SLEEP_URL, json=[{"calendarDate": "a"}])
    responses.get(SLEEP_URL, json=[{"calendarDate": "b"}])
    rows = GarminSource(Settings()).sleep_summaries(date(2026, 9, 1), date(2026, 10, 15))
    assert rows == [{"calendarDate": "a"}, {"calendarDate": "b"}]
    gets = [c.request.url for c in responses.calls if c.request.method == "GET"]
    assert "startDate=2026-09-01&endDate=2026-10-01" in gets[0]
    assert "startDate=2026-10-02&endDate=2026-10-15" in gets[1]


@responses.activate
def test_activity_search_pages_past_45():
    responses.post(TOKEN_URL, body=fake_jwt())
    responses.get(SEARCH_URL, json=[{"activityId": i} for i in range(45)])
    responses.get(SEARCH_URL, json=[{"activityId": 100 + i} for i in range(10)])
    rows = GarminSource(Settings()).activities(date(2026, 1, 1), date(2026, 10, 27), limit=100)
    assert len(rows) == 55
    second = [c.request.url for c in responses.calls if c.request.method == "GET"][1]
    assert "start=45" in second and "limit=45" in second


@responses.activate
def test_rate_limit_and_not_found_messages():
    responses.post(TOKEN_URL, body=fake_jwt())
    responses.get(BASE + "/activity-service/activity/5", status=404)
    responses.get(BASE + "/activity-service/activity/6", status=429)
    source = GarminSource(Settings())
    with pytest.raises(GarminError, match="HTTP 404"):
        source.activity(5)
    with pytest.raises(GarminError, match="rate limiting"):
        source.activity(6)


@responses.activate
def test_fit_file_is_extracted_from_zip():
    fit = b"\x0e\x10\x00\x00\x00\x00\x00\x00.FITpayload"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("123_ACTIVITY.fit", fit)
    responses.post(TOKEN_URL, body=fake_jwt())
    responses.get(BASE + "/download-service/files/activity/123", body=buf.getvalue())
    assert GarminSource(Settings()).activity_fit(123) == fit
