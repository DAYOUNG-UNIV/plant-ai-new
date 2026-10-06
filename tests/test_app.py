"""Exercise the actual Streamlit UI with isolated secrets and no network."""

from pathlib import Path
from unittest.mock import Mock

import pytest
import streamlit as st
from google import genai
from streamlit.testing.v1 import AppTest

from plant_ai import data
from plant_ai.data import DataLoadError, make_demo_frame


APP = Path(__file__).resolve().parents[1] / "app.py"


@pytest.fixture
def app(monkeypatch):
    for name in ["GOOGLE_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL", "SHEET_ID", "SHEET_GID"]:
        monkeypatch.delenv(name, raising=False)
    # A nonempty secrets mapping makes AppTest replace st.secrets, so even a
    # developer's real global secrets file cannot affect these tests.
    app = AppTest.from_file(str(APP), default_timeout=20)
    app.secrets = {"TEST_ISOLATION": True}
    forbidden_client = Mock(side_effect=AssertionError("An integration test attempted an AI network call."))
    forbidden_network = Mock(side_effect=AssertionError("An integration test attempted a Sheets network call."))
    monkeypatch.setattr(genai, "Client", forbidden_client)
    monkeypatch.setattr(data, "urlopen", forbidden_network)
    st.cache_data.clear()
    st.cache_resource.clear()
    yield app
    forbidden_client.assert_not_called()
    forbidden_network.assert_not_called()
    st.cache_data.clear()
    st.cache_resource.clear()


def values(elements):
    return "\n".join(str(element.value) for element in elements)


def demo(app):
    app.run()
    app.selectbox(key="data_mode").select("합성 시연 데이터").run()
    assert not app.exception
    return app


def test_no_key_or_sheet_opens_setup_without_network(app):
    app.run()
    assert not app.exception
    assert not app.metric
    assert app.radio(key="reference_label").value == "마지막 기록 기준"
    assert "Google Sheets 문서 ID를 설정하면" in values(app.info)
    assert app.text_input(key="session_api_key").value == ""


def test_explicit_demo_displays_source_metrics_and_local_conversation(app):
    demo(app)
    assert len(app.metric) == 3
    assert "합성 시연 데이터" in values(app.warning)
    assert "실제 바질에서 측정한 값이 아닙니다" in values(app.warning)
    assert app.radio(key="reference_label").value == "마지막 기록 기준"
    app.chat_input[0].set_value("지난주와 이번 주를 비교해 줘").run()
    assert not app.exception
    messages = app.session_state["messages"]
    assert len(messages) == 2
    assert messages[-1]["mode"] == "local"
    comparison = next(item for item in messages[-1]["evidence"] if item["tool"] == "compare_periods")
    assert comparison["arguments"] == {"period_a": "last_week", "period_b": "this_week"}
    assert messages[-1]["audit"]["status"] == "satisfied"
    assert "기본 안내 · AI 생성 응답 아님" in values(app.caption)
    assert app.session_state["telemetry"][-1]["model_calls"] == 0
    app.radio(key="reference_label").set_value("실제 오늘 기준").run()
    assert not app.exception
    assert app.session_state["messages"] == []
    assert app.session_state["telemetry"] == []


def test_configured_key_is_not_contacted_while_browsing(app):
    app.secrets["GOOGLE_API_KEY"] = "fake-test-key-not-a-credential"
    demo(app)
    assert not app.exception
    assert "Gemini 키 설정됨" in values(app.caption)
    assert "fake-test-key-not-a-credential" not in values(app.caption) + values(app.markdown)


def test_stale_csv_uses_last_record_reference_and_discloses_staleness(app):
    frame = make_demo_frame(now="2020-02-10 12:00:00+09:00", days=14)
    content = frame.to_csv(index=False).encode("utf-8-sig")
    app.run()
    app.selectbox(key="data_mode").select("CSV 기록 파일").run()
    app.file_uploader[0].set_value(("old-sensor-records.csv", content, "text/csv")).run()
    assert not app.exception
    assert len(app.metric) == 3
    assert "업로드한 기록 분석 · 실시간 연결이 아닙니다" in values(app.info)
    assert "현재 상태는 확인할 수 없습니다" in values(app.info)
    assert "분석 기준일: 2020-02-10" in values(app.caption)
    assert "합성 시연 데이터" not in values(app.warning)
    app.chat_input[0].set_value("지난주와 이번 주를 비교해 줘").run()
    assert not app.exception
    assert app.session_state["messages"][-1]["mode"] == "local"


def test_connection_loss_preserves_only_the_same_sheets_snapshot(app, monkeypatch):
    frame = make_demo_frame(now="2020-02-10 12:00:00+09:00", days=14)
    fetch = Mock(side_effect=[frame, DataLoadError("인터넷 연결을 확인해 주세요.")])
    monkeypatch.setattr(data, "fetch_sheet", fetch)
    app.run()
    app.text_input[0].input("integration-test-sheet").run()
    assert not app.exception
    old_values = [metric.value for metric in app.metric]
    assert len(old_values) == 3
    next(button for button in app.button if button.label == "기록 다시 읽기").click().run()
    assert not app.exception
    assert fetch.call_count == 2
    assert [metric.value for metric in app.metric] == old_values
    assert "연결 중단" in values(app.warning)
    assert "합성 시연 데이터" not in values(app.warning)


def test_failed_new_sheet_never_displays_previous_sheet_or_demo(app, monkeypatch):
    frame = make_demo_frame(now="2020-02-10 12:00:00+09:00", days=14)
    fetch = Mock(side_effect=[frame, DataLoadError("이 시트를 읽을 수 없습니다.")])
    monkeypatch.setattr(data, "fetch_sheet", fetch)
    app.run()
    app.text_input[0].input("integration-first-sheet").run()
    assert len(app.metric) == 3
    app.text_input[0].input("integration-second-sheet").run()
    assert not app.exception
    assert not app.metric
    assert "이 시트를 읽을 수 없습니다" in values(app.error)
    assert "직접 선택할 때만" in values(app.info)
    assert "합성 시연 데이터" not in values(app.warning)


def test_invalid_csv_shows_recovery_message_without_substituting_data(app):
    app.run()
    app.selectbox(key="data_mode").select("CSV 기록 파일").run()
    app.file_uploader[0].set_value(("login.csv", b"<!DOCTYPE html><html>login</html>", "text/csv")).run()
    assert not app.exception
    assert not app.metric
    assert "CSV 대신" in values(app.error)
    assert "직접 선택할 때만" in values(app.info)


def test_hosted_key_locks_model_and_shows_only_configured_demo_link(app):
    app.secrets["GOOGLE_API_KEY"] = "fake-server-key"
    app.secrets["GEMINI_MODEL"] = "operator-selected-model"
    app.secrets["DEMO_SITE_URL"] = "https://example-demo.streamlit.app/"
    demo(app)
    assert "Gemini 모델" not in [field.label for field in app.text_input]
    assert "운영 모델: operator-selected-model" in values(app.caption)
    links = app.get("link_button")
    assert any(link.label == "API 없이 기록 데모 보기" and link.proto.url == "https://example-demo.streamlit.app/" for link in links)
    assert "fake-server-key" not in values(app.caption) + values(app.markdown)
