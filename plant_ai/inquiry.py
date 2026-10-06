"""Local inquiry missions and an optional, content-free rehearsal recorder.

This module never contacts an AI service or writes a file. A visitor clicking
"observed" is a self-report, not an objective measurement of looking at a plant.
The recorder supports software rehearsals, not collection of research results.
"""
from __future__ import annotations

from copy import deepcopy
from enum import Enum
import json
import math
import time
from typing import Callable
from uuid import uuid4


OBSERVATION_NOTE = "관찰 완료 표시는 사용자의 자기보고입니다. 실제 관찰·주시·이해가 측정되었다는 뜻이 아닙니다."
REHEARSAL_NOTE = "연구 리허설용 기능 점검 기록이며 연구 결과가 아닙니다. 방문자 연구 데이터 수집은 별도의 연구 절차가 필요합니다."
TIMING_NOTE = "elapsed_seconds는 세션 경과 시간, active_elapsed_seconds는 명시적으로 시작하고 끝낸 미션 구간의 합입니다. 실제 관찰·집중 시간을 측정하지 않습니다."
SCHEMA_VERSION = "1.0"
MAX_EVENTS = 1000

_MISSIONS = (
    {
        "id": "observe_plant",
        "title": "1. 눈앞의 바질을 관찰해요",
        "observe_prompt": "실제 화분을 보고 잎의 색, 잎의 모양, 줄기의 모습을 천천히 살펴보세요. 보이지 않는 부분은 추측하지 않아도 돼요.",
        "question": "눈으로 본 잎과 줄기 모습만으로 바질의 건강 상태를 확정할 수 있을까?",
        "quiz_prompt": "직접 본 사실과 해석을 가장 잘 구분한 설명은 무엇일까요?",
        "choices": [
            {"key": "a", "label": "잎이 초록색이면 바질이 건강하다고 확정할 수 있어요."},
            {"key": "b", "label": "실제로 본 색과 모양은 묘사할 수 있지만, 그것만으로 건강 상태를 확정할 수는 없어요."},
            {"key": "c", "label": "예전 센서 숫자만 보면 지금 잎의 색을 알 수 있어요."},
        ],
        "correct_key": "b",
        "explanation": "관찰은 눈앞에서 실제로 확인한 색과 모양을 기록하는 일이에요. 건강 상태나 변화의 원인을 설명하려면 추가 정보가 필요해요. 앱이 현재 잎이나 줄기 모습을 자동으로 관측한 것은 아니에요.",
    },
    {
        "id": "record_time",
        "title": "2. 기록의 시간과 지금을 구분해요",
        "observe_prompt": "눈앞의 화분을 다시 살펴보고, 화면에 표시된 마지막 측정 시각과 분석 기준일을 찾아보세요.",
        "question": "마지막 측정 기록은 지금 눈앞에 있는 화분의 상태와 어떻게 다를까?",
        "quiz_prompt": "만약 마지막 센서 측정이 한 달 전이었다면, 가장 알맞은 설명은 무엇일까요?",
        "choices": [
            {"key": "a", "label": "한 달 전 관측을 설명할 수 있지만, 지금 상태를 알려면 새 측정이나 관찰이 필요해요."},
            {"key": "b", "label": "앱을 오늘 열었으므로 한 달 전 측정값도 오늘의 실시간 값이에요."},
            {"key": "c", "label": "분석 기준을 마지막 측정일로 바꾸면 센서 기록도 새로 수집돼요."},
        ],
        "correct_key": "a",
        "explanation": "측정 시각은 데이터가 기록된 때예요. 마지막 측정일을 분석 기준으로 삼아 과거의 오늘·어제·이번 주를 비교할 수 있지만, 실제 현재 시각이나 기록의 최신성은 바뀌지 않아요. 한 달 전이라는 조건은 퀴즈의 가정이에요.",
    },
    {
        "id": "sensor_units",
        "title": "3. 습도와 토양 센서값을 구분해요",
        "observe_prompt": "화분 주변의 공기와 흙이 서로 다른 대상을 뜻한다는 점을 생각해 보세요. 화면에서 공기 습도와 토양 센서값의 단위도 찾아보세요.",
        "question": "공기 습도와 보정하지 않은 토양 센서값을 같은 퍼센트로 읽어도 될까?",
        "quiz_prompt": "공기 습도와 토양 센서 원시값을 비교할 때 맞는 설명은 무엇일까요?",
        "choices": [
            {"key": "a", "label": "토양 센서 원시값을 그대로 퍼센트로 읽으면 돼요."},
            {"key": "b", "label": "공기 습도가 높으면 흙도 반드시 충분히 젖어 있어요."},
            {"key": "c", "label": "측정 대상과 단위가 달라요. 토양 원시값은 보정 없이 퍼센트나 물주기 판단으로 바꿀 수 없어요."},
        ],
        "correct_key": "c",
        "explanation": "공기 습도는 공기 중 습기를, 토양 센서는 흙과 관련된 신호를 측정해요. 보정 전 토양 원시값만으로 수분 퍼센트나 건조·습윤 방향을 확정하지 않아요. 실제 흙 상태와 센서 보정 정보가 필요해요.",
    },
)


def get_missions() -> list[dict]:
    """Return fresh JSON-serializable mission cards; callers may modify copies."""
    return deepcopy(list(_MISSIONS))


def get_mission(mission_id: str) -> dict | None:
    """Return one independent card, or None for an unknown stable identifier."""
    return next((deepcopy(mission) for mission in _MISSIONS if mission["id"] == mission_id), None)


def check_answer(mission_id: str, choice_key: str) -> dict:
    """Check a local quiz; never infer that the visitor actually observed it."""
    mission = get_mission(mission_id)
    if mission is None:
        raise ValueError("알 수 없는 미션입니다.")
    if choice_key not in {choice["key"] for choice in mission["choices"]}:
        raise ValueError("제시된 선택지 중 하나를 선택해 주세요.")
    return {
        "mission_id": mission_id,
        "correct": choice_key == mission["correct_key"],
        "correct_key": mission["correct_key"],
        "explanation": mission["explanation"],
        "observation_note": OBSERVATION_NOTE,
        "research_result": False,
    }


class EventType(str, Enum):
    SESSION_STARTED = "session_started"
    TASK_STARTED = "task_started"
    TASK_FINISHED = "task_finished"
    TASK_INTERRUPTED = "task_interrupted"
    OBSERVATION_SELF_REPORT = "observation_self_report"
    QUIZ_ANSWER = "quiz_answer"
    AI_REQUEST = "ai_request"
    SESSION_FINISHED = "session_finished"


_ALLOWED_TEXT = {
    "mode": {"explainer", "character", "local", "gemini", "inquiry"},
    "taskid": {mission["id"] for mission in _MISSIONS} | {"free_question"},
    "source": {"live", "demo", "upload", "csv", "local", "sheets", "google_sheets", "none", "unknown"},
    "reference_mode": {"latest", "calendar"},
}
_ALLOWED_METADATA = set(_ALLOWED_TEXT) | {"success", "latency", "calls"}
_PUBLIC_EVENTS = {EventType.OBSERVATION_SELF_REPORT, EventType.QUIZ_ANSWER, EventType.AI_REQUEST}


def _metadata(values: dict) -> dict:
    """No free text fields: even allowed text keys take controlled values."""
    if set(values) - _ALLOWED_METADATA:
        raise ValueError("리허설 기록에는 허용된 메타데이터만 넣을 수 있습니다.")
    clean = {}
    for key, value in values.items():
        if key in _ALLOWED_TEXT:
            if not isinstance(value, str) or value not in _ALLOWED_TEXT[key]:
                raise ValueError("메타데이터 값이 허용된 범위에 없습니다.")
        elif key == "success":
            if not isinstance(value, bool):
                raise ValueError("success는 참 또는 거짓이어야 합니다.")
        elif key == "calls":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 16:
                raise ValueError("calls는 0~16의 정수여야 합니다.")
        elif key == "latency":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 3_600_000:
                raise ValueError("latency는 0~3,600,000 범위의 밀리초 값이어야 합니다.")
        clean[key] = value
    return clean


class SessionRecorder:
    """Opt-in, in-memory rehearsal events with anonymous per-run identifiers.

    start() is idempotent; recording a valid event also starts an enabled run.
    start_task() switches tasks by interrupting, not completing, the old one.
    finish_task() counts an explicit completion; finish() interrupts an open
    task and freezes timing. Repeated UI reruns do not duplicate starts.
    A new recorder is required for a new run after finish().
    """

    def __init__(self, enabled: bool = False, *, clock: Callable[[], float] = time.monotonic, max_events: int = MAX_EVENTS):
        if not isinstance(enabled, bool):
            raise ValueError("enabled는 참 또는 거짓이어야 합니다.")
        if isinstance(max_events, bool) or not isinstance(max_events, int) or not 1 <= max_events <= MAX_EVENTS:
            raise ValueError("max_events는 1~1000의 정수여야 합니다.")
        self._max_events = max_events
        self._enabled = enabled
        self._clock = clock
        self._session_id: str | None = None
        self._started: float | None = None
        self._ended: float | None = None
        self._last_clock: float | None = None
        self._active: tuple[str, float] | None = None
        self._active_elapsed = 0.0
        self._events: list[dict] = []
        self._event_counts = {kind: 0 for kind in EventType}
        self._correct_answers = 0
        self._model_calls = 0
        self._total_events = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def _now(self) -> float:
        value = float(self._clock())
        if not math.isfinite(value) or (self._last_clock is not None and value < self._last_clock):
            raise ValueError("경과 시간을 측정할 수 없습니다.")
        self._last_clock = value
        return value

    def _append(self, event: EventType, now: float, metadata: dict) -> None:
        self._total_events += 1
        self._event_counts[event] += 1
        if event == EventType.QUIZ_ANSWER and metadata.get("success") is True:
            self._correct_answers += 1
        if event == EventType.AI_REQUEST:
            self._model_calls += metadata.get("calls", 0)
        if len(self._events) >= self._max_events:
            return  # Preserve bounded aggregate counts, not event bodies.
        self._events.append({
            "sequence": self._total_events,
            "event": event.value,
            "at_seconds": round(now - self._started, 6),
            "metadata": dict(metadata),
        })

    def start(self, **metadata) -> bool:
        if not self.enabled or self._ended is not None or self._started is not None:
            return False
        clean = _metadata(metadata)
        now = self._now()
        self._started = now
        self._session_id = str(uuid4())
        self._append(EventType.SESSION_STARTED, now, clean)
        return True

    def _running(self) -> bool:
        if not self.enabled or self._ended is not None:
            return False
        if self._started is None:
            self.start()
        return True

    def start_task(self, taskid: str, **metadata) -> bool:
        if not self.enabled or self._ended is not None:
            return False
        clean = _metadata({**metadata, "taskid": taskid})
        if self._active and self._active[0] == taskid:
            return False
        self._running()
        now = self._now()
        if self._active:
            self._close_task(now, completed=False)
        self._active = (taskid, now)
        self._append(EventType.TASK_STARTED, now, clean)
        return True

    def _close_task(self, now: float, *, completed: bool, metadata: dict | None = None) -> None:
        taskid, began = self._active
        self._active_elapsed += now - began
        clean = dict(metadata or {})
        clean["taskid"] = taskid
        self._append(EventType.TASK_FINISHED if completed else EventType.TASK_INTERRUPTED, now, clean)
        self._active = None

    def finish_task(self, taskid: str, **metadata) -> bool:
        if not self.enabled or self._ended is not None or self._active is None:
            return False
        clean = _metadata({**metadata, "taskid": taskid})
        if self._active[0] != taskid:
            raise ValueError("시작한 미션과 종료할 미션이 다릅니다.")
        self._close_task(self._now(), completed=True, metadata=clean)
        return True

    def record(self, event: str | EventType, **metadata) -> bool:
        if not self.enabled or self._ended is not None:
            return False
        try:
            kind = EventType(event)
        except (ValueError, TypeError):
            raise ValueError("지원하지 않는 리허설 이벤트입니다.") from None
        if kind not in _PUBLIC_EVENTS:
            raise ValueError("시작과 종료 이벤트는 전용 메서드로 기록해 주세요.")
        clean = _metadata(metadata)
        if kind in {EventType.OBSERVATION_SELF_REPORT, EventType.QUIZ_ANSWER} and "taskid" not in clean:
            raise ValueError("관찰 자기보고와 퀴즈에는 미션 ID가 필요합니다.")
        if kind == EventType.QUIZ_ANSWER and "success" not in clean:
            raise ValueError("퀴즈 기록에는 정답 여부가 필요합니다.")
        self._running()
        self._append(kind, self._now(), clean)
        return True

    def finish(self, **metadata) -> bool:
        if not self.enabled or self._started is None or self._ended is not None:
            return False
        clean = _metadata(metadata)
        now = self._now()
        if self._active:
            self._close_task(now, completed=False)
        self._ended = now
        self._append(EventType.SESSION_FINISHED, now, clean)
        return True

    def export(self) -> dict:
        """Return an independent JSON-safe snapshot; no file or network write."""
        now = self._ended if self._ended is not None else (self._now() if self._started is not None else None)
        elapsed = now - self._started if self._started is not None else 0.0
        active = self._active_elapsed + (now - self._active[1] if self._active else 0.0)
        counts = self._event_counts
        dropped = self._total_events - len(self._events)
        summary = {
            "events_count": self._total_events,
            "stored_events_count": len(self._events),
            "dropped_events_count": dropped,
            "sessions_started": counts[EventType.SESSION_STARTED],
            "sessions_finished": counts[EventType.SESSION_FINISHED],
            "tasks_started": counts[EventType.TASK_STARTED],
            "tasks_finished": counts[EventType.TASK_FINISHED],
            "tasks_interrupted": counts[EventType.TASK_INTERRUPTED],
            "active_task_count": int(self._active is not None),
            "observation_self_reports": counts[EventType.OBSERVATION_SELF_REPORT],
            "quiz_answers": counts[EventType.QUIZ_ANSWER],
            "correct_answers": self._correct_answers,
            "ai_requests": counts[EventType.AI_REQUEST],
            "model_calls": self._model_calls,
            "elapsed_seconds": round(elapsed, 6),
            "active_elapsed_seconds": round(active, 6),
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "enabled": self.enabled,
            "truncated": dropped > 0,
            "dropped_events_count": dropped,
            "event_limit": self._max_events,
            "session_id": self.session_id,
            "research_result": False,
            "rehearsal_note": REHEARSAL_NOTE,
            "observation_note": OBSERVATION_NOTE,
            "timing_note": TIMING_NOTE,
            "metadata_units": {"latency": "milliseconds"},
            "summary": summary,
            "events": deepcopy(self._events),
        }

    def export_json(self) -> str:
        return json.dumps(self.export(), ensure_ascii=False, indent=2, allow_nan=False)
