"""Offline downloads stay tied to the complete currently displayed snapshot."""
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
import streamlit as st
from google import genai
from streamlit.testing.v1 import AppTest

from plant_ai import analytics, data, exhibition
from plant_ai.data import DataLoadError

APP = Path(__file__).resolve().parents[1] / "app.py"


@pytest.fixture
def app(monkeypatch):
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL", "SHEET_ID", "SHEET_GID"):
        monkeypatch.delenv(name, raising=False)
    forbidden = Mock(side_effect=AssertionError("Offline export must not contact a network service."))
    monkeypatch.setattr(genai, "Client", forbidden)
    monkeypatch.setattr(data, "urlopen", forbidden)
    st.cache_data.clear()
    st.cache_resource.clear()
    instance = AppTest.from_file(str(APP), default_timeout=30)
    instance.secrets = {"TEST_ISOLATION": True}
    yield instance
    forbidden.assert_not_called()
    st.cache_data.clear()
    st.cache_resource.clear()


def records(rows=2):
    return pd.DataFrame({
        "Timestamp": pd.date_range("2025-02-01 10:00", periods=rows, freq="2min"),
        "Temperature": [20.0] * rows,
        "Humidity": [50.0] * rows,
        "Soil Moisture": [600.0] * rows,
    })


def open_sheet(app, monkeypatch, responses):
    fetch = Mock(side_effect=responses)
    monkeypatch.setattr(data, "fetch_sheet", fetch)
    app.run()
    app.text_input[0].input("offline-test-sheet").run()
    assert not app.exception
    return fetch


def build(app):
    app.button(key="build_offline").click().run()
    assert not app.exception
    return app.session_state["offline_export"]


def refresh(app):
    next(button for button in app.button if button.label == "기록 다시 읽기").click().run()
    assert not app.exception


def download_labels(app):
    return [button.label for button in app.get("download_button")]


def test_invalid_sensor_numbers_show_guidance_without_crashing(app):
    app.run()
    app.selectbox(key="data_mode").select("CSV 기록 파일").run()
    bad = b"Timestamp,Temperature,Humidity,Soil Moisture\n2025-02-01 10:00,bad,bad,bad\n"
    app.file_uploader[0].set_value(("invalid-values.csv", bad, "text/csv")).run()
    assert not app.exception
    assert build(app) is None
    assert "유효한 센서 수치가 없습니다" in " ".join(error.value for error in app.error)
    assert "단일 HTML 받기" not in download_labels(app)


@pytest.mark.parametrize("error", [ValueError("열람본 형식을 확인해 주세요."), OSError("private/template/path")])
def test_failed_rebuild_removes_previous_download(app, monkeypatch, error):
    open_sheet(app, monkeypatch, [records()])
    assert build(app)
    monkeypatch.setattr(exhibition, "render_html", Mock(side_effect=error))
    assert build(app) is None
    assert app.error
    assert "private/template/path" not in " ".join(item.value for item in app.error)
    assert "단일 HTML 받기" not in download_labels(app)


def test_new_sheet_reading_invalidates_previous_export(app, monkeypatch):
    old = records()
    newer = records(3)
    newer.loc[2, "Temperature"] = 30
    fetch = open_sheet(app, monkeypatch, [old, newer])
    previous = build(app)
    assert previous
    refresh(app)
    assert fetch.call_count == 2
    assert app.metric[0].value == "30.0 °C"
    assert app.session_state["offline_export"] is None
    assert "단일 HTML 받기" not in download_labels(app)
    current = build(app)
    assert current["source_identity"] != previous["source_identity"]
    assert current["html"] != previous["html"]


def test_past_only_edit_in_large_sheet_invalidates_export_and_analytics(app, monkeypatch):
    old = records(60_001)
    corrected = old.copy()
    corrected.loc[123, "Temperature"] = 35
    prepare = Mock(wraps=analytics.SensorAnalytics)
    monkeypatch.setattr(analytics, "SensorAnalytics", prepare)
    fetch = open_sheet(app, monkeypatch, [old, corrected])
    previous = build(app)
    previous_metrics = [metric.value for metric in app.metric]
    assert prepare.call_count == 1
    # Mimic a refreshed Sheets cache while keeping the analytics resource cache:
    # the last row, timestamps, shape, source ID, and current metrics are identical.
    st.cache_data.clear()
    app.run()
    assert not app.exception
    assert fetch.call_count == 2
    assert [metric.value for metric in app.metric] == previous_metrics
    assert app.session_state["offline_export"] is None
    assert prepare.call_count == 2
    assert prepare.call_args.args[0].iloc[123]["Temperature"] == 35
    assert app.session_state["offline_source_identity"] != previous["source_identity"]


def test_unchanged_refresh_keeps_download_for_that_snapshot(app, monkeypatch):
    original = records()
    fetch = open_sheet(app, monkeypatch, [original, original.copy()])
    previous = build(app)
    st.cache_data.clear()
    app.run()
    assert not app.exception
    assert fetch.call_count == 2
    assert app.session_state["offline_export"] == previous
    assert "단일 HTML 받기" in download_labels(app)


def test_connection_failure_preserves_export_of_displayed_last_snapshot(app, monkeypatch):
    fetch = open_sheet(app, monkeypatch, [records(), DataLoadError("연결을 확인해 주세요.")])
    previous = build(app)
    refresh(app)
    assert fetch.call_count == 2
    assert "연결 중단" in " ".join(item.value for item in app.warning)
    assert app.session_state["offline_export"] == previous
    assert "단일 HTML 받기" in download_labels(app)


def test_different_sheet_identity_clears_export_even_for_identical_values(app, monkeypatch):
    original = records()
    open_sheet(app, monkeypatch, [original, original.copy()])
    previous = build(app)
    app.text_input[0].input("different-offline-test-sheet").run()
    assert not app.exception
    assert app.session_state["offline_export"] is None
    assert app.session_state["offline_source_identity"] != previous["source_identity"]
