import io
import socket
from urllib.error import HTTPError, URLError
from unittest.mock import patch

import pandas as pd
import pytest

from plant_ai.data import (
    MAX_CSV_BYTES,
    DataLoadError,
    Settings,
    fetch_sheet,
    get_settings,
    load_csv_bytes,
    make_demo_frame,
)


VALID_CSV = b"Timestamp,Temperature,Humidity,Soil Moisture\n2026-10-04 12:00:00,24,60,512\n"
SHEET_ID = "private-sheet-identifier"


def test_settings_precedence_legacy_and_fallback():
    settings = get_settings(
        {"GOOGLE_API_KEY": "secrets-key", "GEMINI_MODEL": " model-from-secrets ", "SHEET_ID": " sheet-from-secrets "},
        {"GEMINI_API_KEY": "env-key", "SHEET_GID": " 123 "},
    )
    assert settings.api_key == "env-key"
    assert settings.model == "model-from-secrets"
    assert settings.sheet_id == "sheet-from-secrets"
    assert settings.sheet_gid == "123"
    assert get_settings({}, {"GOOGLE_API_KEY": "legacy", "GEMINI_API_KEY": "alternate"}).api_key == "legacy"
    assert get_settings({"GEMINI_API_KEY": "fallback"}, {}).api_key == "fallback"
    assert get_settings({}, {}) == Settings()


def test_key_is_not_sanitized_or_shown_in_repr():
    original = " bad\n키-value "
    settings = get_settings({"GOOGLE_API_KEY": original}, {})
    assert settings.api_key == original
    assert original not in repr(settings)
    assert "api_key" not in repr(settings)


@pytest.mark.parametrize("encoding", ["utf-8-sig", "cp949"])
def test_csv_encodings_and_original_headers(encoding):
    content = "측정시각,온도,습도,토양수분\n2026-10-04 12:00:00,24,60,512\n".encode(encoding)
    frame = load_csv_bytes(content)
    assert list(frame.columns) == ["측정시각", "온도", "습도", "토양수분"]
    assert frame.iloc[0]["온도"] == 24


@pytest.mark.parametrize("content", [
    b"", b" \n", b"Timestamp,Temperature\n", b"not a csv", b"<html>private-sheet-identifier</html>",
    b'{"error":"secret-provider-message"}', b"a,b\n1,2,3\n", b"a,a\n1,2\n", b"a,\n1,2\n",
    b'a,b\n"unfinished,2', b"a,b\n1,\x002\n",
])
def test_csv_rejects_nondata_without_echo(content):
    with pytest.raises(DataLoadError) as error:
        load_csv_bytes(content)
    assert "private-sheet-identifier" not in str(error.value)
    assert "secret-provider-message" not in str(error.value)


def test_csv_size_is_bounded():
    with pytest.raises(DataLoadError, match="25MB"):
        load_csv_bytes(b"x" * (MAX_CSV_BYTES + 1))


@pytest.mark.parametrize("sheet_id,gid", [
    ("https://evil.test/file", "0"), ("abc?token=secret", "0"), (SHEET_ID, "0&x=secret"),
    (SHEET_ID, "-1"), (SHEET_ID, ""), ("", "0"),
])
def test_sheet_identifiers_checked_before_network(sheet_id, gid):
    with patch("plant_ai.data.urlopen") as network:
        with pytest.raises(DataLoadError):
            fetch_sheet(sheet_id, gid)
        network.assert_not_called()


class FakeResponse(io.BytesIO):
    def __init__(self, body=VALID_CSV, headers=None):
        super().__init__(body)
        self.headers = headers or {"Content-Type": "text/csv"}
        self.read_limit = None

    def read(self, size=-1):
        self.read_limit = size
        return super().read(size)


def test_sheet_fixed_url_timeout_and_read_limit():
    response = FakeResponse()
    with patch("plant_ai.data.urlopen", return_value=response) as network:
        frame = fetch_sheet(SHEET_ID, "321", timeout=3)
    assert len(frame) == 1
    request = network.call_args.args[0]
    assert request.full_url == f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=321"
    assert network.call_args.kwargs["timeout"] == 3
    assert response.read_limit == MAX_CSV_BYTES + 1


@pytest.mark.parametrize("headers,body", [
    ({"Content-Type": "text/html"}, b"private-sheet-identifier"),
    ({"Content-Type": "text/csv"}, b"<!DOCTYPE html><html>login</html>"),
    ({"Content-Type": "text/csv", "Content-Length": str(MAX_CSV_BYTES + 1)}, b""),
])
def test_sheet_rejects_login_pages_and_oversized_exports(headers, body):
    with patch("plant_ai.data.urlopen", return_value=FakeResponse(body, headers)):
        with pytest.raises(DataLoadError) as error:
            fetch_sheet(SHEET_ID)
    assert SHEET_ID not in str(error.value)


@pytest.mark.parametrize("failure", [
    socket.timeout("private-sheet-identifier"),
    URLError("secret-provider-message"),
    HTTPError("https://example.test/private-sheet-identifier", 403, "secret-provider-message", {}, None),
    HTTPError("https://example.test/private-sheet-identifier", 429, "secret-provider-message", {}, None),
])
def test_network_failures_are_safe_and_never_return_demo(failure):
    with patch("plant_ai.data.urlopen", side_effect=failure):
        with pytest.raises(DataLoadError) as error:
            fetch_sheet(SHEET_ID)
    assert SHEET_ID not in str(error.value)
    assert "secret-provider-message" not in str(error.value)


def test_demo_is_deterministic_and_has_two_minute_sensor_records():
    now = pd.Timestamp("2026-10-04T12:01:59+09:00")
    frame = make_demo_frame(now=now, days=2)
    pd.testing.assert_frame_equal(frame, make_demo_frame(now=now, days=2))
    assert len(frame) == 1440
    assert list(frame.columns) == ["Timestamp", "Temperature", "Humidity", "Soil Moisture"]
    assert frame["Timestamp"].iloc[-1] == now.floor("2min")
    assert frame["Timestamp"].diff().dropna().dt.total_seconds().eq(120).all()
    assert frame["Humidity"].between(0, 100).all()
    assert frame["Soil Moisture"].between(0, 1023).all()


@pytest.mark.parametrize("days", [0, -1, 1.5, True, 366])
def test_demo_rejects_invalid_duration(days):
    with pytest.raises(ValueError):
        make_demo_frame(days=days)


def test_optional_demo_url_is_https_only_and_preserves_no_credentials():
    url = "https://example-demo.streamlit.app/"
    assert get_settings({"DEMO_SITE_URL": url}, {}).demo_site_url == url
    for invalid in ["javascript:alert(1)", "http://example.test", "https://name:password@example.test", "https://[bad", "https://example.test/a b"]:
        assert get_settings({"DEMO_SITE_URL": invalid}, {}).demo_site_url == ""
