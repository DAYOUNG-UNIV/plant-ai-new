"""Integration scenarios for local inquiry and the opt-in rehearsal UI.

Reuse the existing AppTest fixture: real secrets are isolated and all provider
and Sheets network entrypoints remain forbidden throughout these tests.
"""
import json
from uuid import UUID

import pytest

from test_app import app, demo, values  # noqa: F401: the imported app is a pytest fixture
from plant_ai import agent
from plant_ai.inquiry import SessionRecorder, get_mission


def button(app, key):
    return app.button(key=key)


def start_rehearsal(app):
    button(app, "rehearsal_start").click().run()
    assert not app.exception
    recorder = app.session_state["rehearsal"]
    assert recorder.enabled and recorder.session_id is not None
    return recorder


def answer_quiz(app, mission_id):
    mission = get_mission(mission_id)
    app.radio(key=f"quiz_{mission_id}").set_value(mission["correct_key"]).run()
    button(app, "check_quiz").click().run()
    assert not app.exception


def test_inquiry_starts_without_recording_and_mission_question_gets_local_answer(app):
    demo(app)
    recorder = app.session_state["rehearsal"]
    assert recorder.enabled is False
    assert recorder.session_id is None
    assert recorder.export()["events"] == []
    assert app.selectbox(key="inquiry_mission").value == "observe_plant"
    assert button(app, "check_quiz").disabled is True
    button(app, "mission_ask").click().run()
    assert not app.exception
    messages = app.session_state["messages"]
    assert messages[-2]["content"] == get_mission("observe_plant")["question"]
    assert messages[-1]["mode"] == "local"
    assert messages[-1]["content"]
    assert app.session_state["inquiry_replies"]["observe_plant"] == messages[-1]["content"]
    assert recorder.export()["events"] == []
    assert "자기보고" in values(app.caption)
    assert "외부 AI 없이" in values(app.caption)


def test_persona_and_easy_setting_reset_text_and_reach_local_answer(app, monkeypatch):
    captured = []
    original = agent.answer_question

    def observe_call(*args, **kwargs):
        captured.append(dict(kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(agent, "answer_question", observe_call)
    demo(app)
    app.chat_input[0].set_value("바질의 원산지는?").run()
    assert not app.exception
    assert captured[-1]["persona"] == "basil" and captured[-1]["audience"] == "general"
    app.selectbox(key="persona_label").select("차분한 해설자").run()
    assert not app.exception
    assert app.session_state["messages"] == []
    assert app.session_state["inquiry_replies"] == {}
    app.checkbox(key="easy_explanation").check().run()
    app.chat_input[0].set_value("바질의 원산지는?").run()
    assert not app.exception
    assert captured[-1]["persona"] == "narrator" and captured[-1]["audience"] == "easy"
    assert app.session_state["messages"][-1]["mode"] == "local"
    assert app.session_state["rehearsal"].enabled is False


def test_rehearsal_explicit_start_observe_quiz_export_finish_and_clear(app, monkeypatch):
    exports = []
    original_export_json = SessionRecorder.export_json

    def capture_export(recorder):
        text = original_export_json(recorder)
        exports.append(text)
        return text

    monkeypatch.setattr(SessionRecorder, "export_json", capture_export)
    demo(app)
    assert not exports
    recorder = start_rehearsal(app)
    assert UUID(recorder.session_id).version == 4
    button(app, "observe_complete").click().run()
    assert not app.exception
    assert recorder.export()["summary"]["observation_self_reports"] == 1
    answer_quiz(app, "observe_plant")
    assert recorder.export()["summary"]["tasks_finished"] == 1
    assert recorder.export()["summary"]["correct_answers"] == 1
    private_question = "PRIVACY_SENTINEL_QUESTION 바질의 원산지는?"
    app.chat_input[0].set_value(private_question).run()
    assert not app.exception
    private_response = app.session_state["messages"][-1]["content"]
    assert private_response
    payload = json.loads(exports[-1])
    assert payload["research_result"] is False
    assert payload["summary"]["ai_requests"] == 1
    assert payload["summary"]["model_calls"] == 0
    assert payload["observation_note"].startswith("관찰 완료 표시는 사용자의 자기보고")
    assert "PRIVACY_SENTINEL_QUESTION" not in exports[-1]
    assert private_response not in exports[-1]
    allowed = {"mode", "taskid", "success", "latency", "calls", "source", "reference_mode"}
    assert all(set(event["metadata"]) <= allowed for event in payload["events"])
    assert any(element.proto.label == "리허설 기록 받기" for element in app.get("download_button"))
    button(app, "rehearsal_finish").click().run()
    assert not app.exception
    finished = recorder.export()
    assert finished["summary"]["sessions_finished"] == 1
    app.run()
    assert recorder.export() == finished
    button(app, "rehearsal_clear").click().run()
    assert not app.exception
    cleared = app.session_state["rehearsal"]
    assert not cleared.enabled and cleared.session_id is None and cleared.export()["events"] == []
    assert not any(element.proto.label == "리허설 기록 받기" for element in app.get("download_button"))


def test_switching_mission_before_quiz_interrupts_previous_without_exception(app):
    demo(app)
    recorder = start_rehearsal(app)
    button(app, "observe_complete").click().run()
    assert recorder.export()["summary"]["active_task_count"] == 1
    app.selectbox(key="inquiry_mission").select("record_time").run()
    answer_quiz(app, "record_time")  # no observation-button click for the new mission
    exported = recorder.export()
    assert exported["summary"]["tasks_started"] == 2
    assert exported["summary"]["tasks_interrupted"] == 1
    assert exported["summary"]["tasks_finished"] == 1
    assert exported["summary"]["active_task_count"] == 0
    assert [event["metadata"]["taskid"] for event in exported["events"] if event["event"] == "task_finished"] == ["record_time"]


def test_settings_changes_preserve_an_active_operational_rehearsal(app):
    demo(app)
    recorder = start_rehearsal(app)
    session_id = recorder.session_id
    button(app, "observe_complete").click().run()
    app.selectbox(key="persona_label").select("차분한 해설자").run()
    app.checkbox(key="easy_explanation").check().run()
    assert not app.exception
    assert app.session_state["rehearsal"].session_id == session_id
    assert recorder.export()["summary"]["observation_self_reports"] == 1
    answer_quiz(app, "observe_plant")
    assert recorder.export()["summary"]["tasks_finished"] == 1


def test_finished_rehearsal_does_not_resume_on_visitor_actions(app):
    demo(app)
    recorder = start_rehearsal(app)
    button(app, "rehearsal_finish").click().run()
    frozen = recorder.export()
    button(app, "observe_complete").click().run()
    answer_quiz(app, "observe_plant")
    button(app, "mission_ask").click().run()
    assert not app.exception
    assert recorder.export() == frozen


def test_start_control_cannot_silently_discard_running_rehearsal(app):
    demo(app)
    recorder = start_rehearsal(app)
    button(app, "observe_complete").click().run()
    assert recorder.export()["summary"]["observation_self_reports"] == 1
    # A second start must not silently replace the active run and its events.
    assert button(app, "rehearsal_start").disabled is True


@pytest.mark.parametrize("mission_id", ["observe_plant", "record_time", "sensor_units"])
def test_fixed_mission_question_remains_local_even_with_configured_api_key(app, mission_id):
    # The fixed inquiry missions are locally supported exhibit content.
    # Only visitors' free questions should depend on a paid/rate-limited API.
    app.secrets["GOOGLE_API_KEY"] = "fake-test-key-not-a-credential"
    demo(app)
    app.selectbox(key="inquiry_mission").select(mission_id).run()
    button(app, "mission_ask").click().run()
    assert not app.exception
    assert app.session_state["messages"][-1]["mode"] == "local"
    assert app.session_state["telemetry"][-1]["model_calls"] == 0
    # The imported fixture additionally verifies Client was never constructed.
