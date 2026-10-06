"""Local mission content, self-report honesty, and opt-in recorder checks."""
import json
import socket
from uuid import UUID

import pytest

from plant_ai.inquiry import (
    OBSERVATION_NOTE, REHEARSAL_NOTE, EventType, SessionRecorder,
    check_answer, get_mission, get_missions,
)


class Clock:
    def __init__(self, value=100.0):
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.value

    def advance(self, seconds):
        self.value += seconds


def test_three_local_missions_have_complete_stable_quizzes():
    missions = get_missions()
    assert [mission["id"] for mission in missions] == ["observe_plant", "record_time", "sensor_units"]
    fields = {"id", "title", "observe_prompt", "question", "quiz_prompt", "choices", "correct_key", "explanation"}
    for mission in missions:
        assert fields <= set(mission)
        assert len(mission["choices"]) == 3
        keys = [choice["key"] for choice in mission["choices"]]
        assert len(set(keys)) == 3 and mission["correct_key"] in keys
        assert all(choice["label"] for choice in mission["choices"])
        for key in keys:
            answer = check_answer(mission["id"], key)
            assert answer["correct"] is (key == mission["correct_key"])
            assert answer["research_result"] is False
            assert answer["observation_note"] == OBSERVATION_NOTE
    json.dumps(missions, ensure_ascii=False, allow_nan=False)


def test_missions_distinguish_observation_time_and_raw_sensor_units():
    first, second, third = get_missions()
    assert "실제" in first["observe_prompt"]
    assert "확정" in first["explanation"] or "추가 정보" in first["explanation"]
    assert "만약" in second["quiz_prompt"] and "가정" in second["explanation"]
    assert "최신성" in second["explanation"]
    assert "보정" in third["explanation"] and "원시값" in third["explanation"]
    assert "자기보고" in OBSERVATION_NOTE and "연구 결과가 아닙니다" in REHEARSAL_NOTE


def test_mission_copies_cannot_corrupt_later_visitors():
    missions = get_missions()
    missions[0]["choices"][0]["label"] = "changed"
    card = get_mission("observe_plant")
    assert card["choices"][0]["label"] != "changed"
    card["correct_key"] = "wrong"
    assert get_mission("observe_plant")["correct_key"] == "b"
    assert get_mission("does_not_exist") is None
    with pytest.raises(ValueError):
        check_answer("does_not_exist", "a")
    with pytest.raises(ValueError):
        check_answer("observe_plant", "not-a-choice")


def test_default_disabled_recorder_collects_nothing_and_does_not_read_clock():
    clock = Clock()
    recorder = SessionRecorder(clock=clock)
    assert recorder.enabled is False
    assert not recorder.start()
    assert not recorder.start_task("observe_plant")
    assert not recorder.record("observation_self_report", taskid="observe_plant")
    assert not recorder.record("question", question="must not be collected")
    assert not recorder.finish_task("observe_plant")
    assert not recorder.finish()
    exported = json.loads(recorder.export_json())
    assert clock.calls == 0
    assert exported["session_id"] is None
    assert exported["events"] == []
    assert exported["summary"]["elapsed_seconds"] == 0
    assert exported["summary"]["sessions_started"] == 0
    assert "must not be collected" not in recorder.export_json()


def test_each_enabled_run_gets_an_independent_anonymous_id():
    first, second = SessionRecorder(True), SessionRecorder(True)
    assert first.session_id is None
    first.start()
    second.start()
    assert UUID(first.session_id).version == 4
    assert first.session_id != second.session_id
    exported = first.export()
    assert exported["schema_version"] == "1.0"
    assert exported["research_result"] is False
    assert "created_at" not in exported and "visitor_id" not in exported


def test_total_and_active_elapsed_are_distinct_and_interruptions_are_not_completions():
    clock = Clock()
    recorder = SessionRecorder(True, clock=clock)
    recorder.start(mode="inquiry", source="demo", reference_mode="latest")
    clock.advance(10)
    recorder.start_task("observe_plant", mode="explainer")
    clock.advance(5)
    recorder.record("observation_self_report", taskid="observe_plant")
    recorder.record("quiz_answer", taskid="observe_plant", success=True)
    recorder.finish_task("observe_plant", success=True)
    clock.advance(20)
    recorder.start_task("record_time", mode="character")
    clock.advance(7)
    recorder.start_task("sensor_units")  # switching interrupts the second task
    clock.advance(3)
    recorder.finish()  # ending the run does not invent a completed third task
    exported = recorder.export()
    summary = exported["summary"]
    assert summary["elapsed_seconds"] == 45
    assert summary["active_elapsed_seconds"] == 15
    assert summary["tasks_started"] == 3
    assert summary["tasks_finished"] == 1
    assert summary["tasks_interrupted"] == 2
    assert summary["active_task_count"] == 0
    assert summary["observation_self_reports"] == 1
    assert summary["quiz_answers"] == summary["correct_answers"] == 1
    assert summary["sessions_started"] == summary["sessions_finished"] == 1
    assert "실제 관찰·집중 시간을 측정하지 않습니다" in exported["timing_note"]
    assert all("at_seconds" in event and "timestamp" not in event for event in exported["events"])


def test_repeated_ui_reruns_do_not_duplicate_starts_or_double_count_time():
    clock = Clock(0)
    recorder = SessionRecorder(True, clock=clock)
    assert recorder.start_task("observe_plant")
    clock.advance(4)
    assert not recorder.start()
    assert not recorder.start_task("observe_plant")
    assert recorder.export()["summary"]["active_elapsed_seconds"] == 4
    assert recorder.export()["summary"]["active_elapsed_seconds"] == 4
    clock.advance(2)
    assert recorder.finish_task("observe_plant", success=False)
    assert not recorder.finish_task("observe_plant")
    exported = recorder.export()
    assert exported["summary"]["active_elapsed_seconds"] == 6
    assert exported["summary"]["tasks_started"] == exported["summary"]["tasks_finished"] == 1
    assert exported["summary"]["quiz_answers"] == 0


def test_finish_freezes_timing_and_new_events_need_a_new_run():
    clock = Clock()
    recorder = SessionRecorder(True, clock=clock)
    recorder.start()
    clock.advance(2)
    recorder.finish()
    before = recorder.export_json()
    clock.advance(1000)
    assert not recorder.start()
    assert not recorder.start_task("sensor_units")
    assert not recorder.record("ai_request", calls=1)
    assert not recorder.finish()
    assert recorder.export_json() == before


@pytest.mark.parametrize("field", ["question", "answer", "rawresponse", "raw_response", "audio", "email", "name", "visitor_id", "ip", "user_agent"])
def test_free_text_and_identifying_metadata_are_rejected_before_collection(field):
    recorder = SessionRecorder(True)
    secret = "private visitor information must not be saved"
    with pytest.raises(ValueError):
        recorder.record("ai_request", **{field: secret})
    assert recorder.session_id is None
    assert recorder.export()["events"] == []
    assert secret not in recorder.export_json()


@pytest.mark.parametrize("metadata", [
    {"mode": "my private sentence"}, {"taskid": "visitor name"}, {"source": "https://private.url"},
    {"reference_mode": "free text"}, {"success": "true"}, {"success": 1},
    {"calls": True}, {"calls": -1}, {"calls": 17}, {"calls": 1.2},
    {"latency": True}, {"latency": -1}, {"latency": float("nan")},
    {"latency": float("inf")}, {"latency": 3_600_001}, {"mode": {"nested": "private"}},
])
def test_even_whitelisted_metadata_has_controlled_values(metadata):
    recorder = SessionRecorder(True)
    with pytest.raises(ValueError):
        recorder.record("ai_request", **metadata)
    assert recorder.export()["events"] == []


def test_controlled_event_enum_and_required_quiz_fields():
    recorder = SessionRecorder(True)
    for event in ["raw_question", "session_started", "task_finished", "unknown", None]:
        with pytest.raises(ValueError):
            recorder.record(event)
    with pytest.raises(ValueError):
        recorder.record("observation_self_report")
    with pytest.raises(ValueError):
        recorder.record("quiz_answer", taskid="observe_plant")
    assert recorder.record(EventType.OBSERVATION_SELF_REPORT, taskid="observe_plant")
    assert recorder.export()["summary"]["observation_self_reports"] == 1


def test_ai_statistics_are_primitive_operational_metadata_without_content():
    clock = Clock()
    recorder = SessionRecorder(True, clock=clock)
    recorder.record("ai_request", taskid="free_question", mode="gemini",
                    success=True, latency=1234.5, calls=2, source="upload", reference_mode="latest")
    recorder.record("ai_request", taskid="free_question", mode="local",
                    success=False, latency=20, calls=0, source="demo", reference_mode="calendar")
    exported = json.loads(recorder.export_json())
    assert exported["summary"]["ai_requests"] == 2
    assert exported["summary"]["model_calls"] == 2
    assert exported["metadata_units"]["latency"] == "milliseconds"
    assert exported["research_result"] is False
    assert all(set(event["metadata"]) <= {"mode", "taskid", "success", "latency", "calls", "source", "reference_mode"}
               for event in exported["events"])


def test_snapshot_is_independent_and_wrong_task_cannot_be_completed():
    recorder = SessionRecorder(True)
    recorder.start_task("observe_plant")
    with pytest.raises(ValueError):
        recorder.finish_task("record_time")
    snapshot = recorder.export()
    snapshot["events"][0]["metadata"]["question"] = "injected"
    assert "injected" not in recorder.export_json()
    assert recorder.export()["summary"]["tasks_finished"] == 0


def test_injected_clock_rejects_backward_or_nonfinite_time():
    clock = Clock()
    recorder = SessionRecorder(True, clock=clock)
    recorder.start()
    clock.advance(-1)
    with pytest.raises(ValueError):
        recorder.export()
    clock.value = float("nan")
    with pytest.raises(ValueError):
        recorder.export()


def test_local_inquiry_requires_no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Network access is forbidden")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    recorder = SessionRecorder(True)
    recorder.start_task("sensor_units")
    result = check_answer("sensor_units", "c")
    recorder.record("quiz_answer", taskid="sensor_units", success=result["correct"])
    recorder.finish_task("sensor_units")
    recorder.finish()
    assert recorder.export()["summary"]["correct_answers"] == 1


def test_event_log_is_bounded_but_completion_and_timing_counts_stay_accurate():
    clock = Clock()
    recorder = SessionRecorder(True, clock=clock, max_events=3)
    recorder.start_task("observe_plant")
    recorder.record("observation_self_report", taskid="observe_plant")
    for index in range(10):
        recorder.record("quiz_answer", taskid="observe_plant", success=index % 2 == 0)
    clock.advance(5)
    recorder.finish_task("observe_plant")
    recorder.finish()
    result = recorder.export()
    assert len(result["events"]) == result["event_limit"] == 3
    assert result["truncated"] is True and result["dropped_events_count"] == 12
    assert result["summary"]["events_count"] == 15
    assert result["summary"]["stored_events_count"] == 3
    assert result["summary"]["quiz_answers"] == 10
    assert result["summary"]["correct_answers"] == 5
    assert result["summary"]["tasks_finished"] == 1
    assert result["summary"]["sessions_finished"] == 1
    assert result["summary"]["active_elapsed_seconds"] == 5


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.5])
def test_event_limit_cannot_disable_memory_bound(limit):
    with pytest.raises(ValueError):
        SessionRecorder(True, max_events=limit)
