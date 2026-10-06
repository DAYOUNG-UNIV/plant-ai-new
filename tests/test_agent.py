"""Exercise actual Google SDK messages with a deterministic, offline provider."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import socket

import pandas as pd
import pytest
from google import genai
from google.genai import errors, types

from plant_ai.agent import (
    MAX_HISTORY_CHARS, MAX_MODEL_EVIDENCE_CHARS, PlantTools, TOOLS,
    answer_question, classify_error, local_answer,
)
from plant_ai.analytics import SensorAnalytics
from plant_ai.knowledge import BasilKnowledge


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Agent tests must not connect to the network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def plant_tools():
    frame = pd.DataFrame({
        "Timestamp": ["2025-08-15 12:00", "2026-08-27 12:00", "2026-09-02 11:58"],
        "Temperature": [29, 24, 22], "Humidity": [60, 55, 52], "Soil Moisture": [500, 510, 520],
    })
    analytics = SensorAnalytics(frame, now="2026-10-04 12:00+09:00", reference_mode="latest")
    knowledge = BasilKnowledge(Path(__file__).resolve().parents[1] / "basil_knowledge.json")
    return PlantTools(analytics, knowledge)


def reply(*parts, tokens=11):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(total_token_count=tokens),
    )


def tool_reply(name="summarize_period", arguments=None, call_id="call-1"):
    return reply(types.Part(
        function_call=types.FunctionCall(name=name, args=arguments or {"period": "today"}, id=call_id),
        thought_signature=b"opaque-provider-signature",
    ))


class FakeClient:
    def __init__(self, steps, close_error=None):
        self.steps = list(steps)
        self.models = self
        self.calls = []
        self.closed = 0
        self.close_error = close_error

    def generate_content(self, *, model, contents, config):
        # SDK Content accepts arbitrary strings at construction, while its role
        # contract and provider API require user/model, including tool replies.
        assert isinstance(config, types.GenerateContentConfig)
        assert all(isinstance(message, types.Content) and message.role in {"user", "model"} for message in contents)
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def close(self):
        self.closed += 1
        if self.close_error:
            raise self.close_error


def test_tool_loop_preserves_original_parts_signatures_and_call_ids(plant_tools):
    first = reply(
        types.Part(text="private reasoning", thought=True, thought_signature=b"thought-token"),
        types.Part(function_call=types.FunctionCall(name="summarize_period", args={"period": "today"}, id="period-a"),
                   thought_signature=b"tool-signature"),
        types.Part(function_call=types.FunctionCall(name="consult_basil_guide", args={"query": "햇빛"}, id="guide-b")),
        tokens=17,
    )
    original = deepcopy(first.candidates[0].content.model_dump())
    final = reply(types.Part(text="숨겨진 생각", thought=True), types.Part(text="마지막 관측일 기준 평균은 22°C야."), tokens=23)
    client = FakeClient([first, final])
    answer = answer_question("오늘 평균과 햇빛 조건을 알려줘", plant_tools, client=client)
    assert answer.mode == "gemini"
    assert answer.model_calls == 2 and answer.tokens == 40
    assert answer.text == "마지막 관측일 기준 평균은 22°C야."
    contents = client.calls[1]["contents"]
    assert contents[-2] is first.candidates[0].content
    assert contents[-2].model_dump() == original
    assert contents[-1].role == "user"
    assert [part.function_response.id for part in contents[-1].parts] == ["period-a", "guide-b"]
    assert contents[-1].parts[0].function_response.response["metrics"]["temperature"]["mean"] == 22
    assert client.closed == 0  # Caller retains ownership of injected clients.
    config = client.calls[0]["config"]
    assert config.automatic_function_calling.disable is True
    assert len(config.tools[0].function_declarations) == len(TOOLS)
    assert all(isinstance(item, types.FunctionDeclaration) for item in config.tools[0].function_declarations)


@pytest.mark.parametrize("limit,expected", [(1, 1), (0, 1), (2, 2), (100, 4)])
def test_model_and_tool_loops_are_bounded(plant_tools, limit, expected):
    client = FakeClient([tool_reply() for _ in range(5)])
    answer = answer_question("현재 상태", plant_tools, client=client, max_model_calls=limit)
    assert answer.mode == "local" and answer.error_code == "step_limit"
    assert answer.model_calls == len(client.calls) == expected


def test_excessive_parallel_calls_are_not_executed(plant_tools):
    client = FakeClient([reply(*[
        types.Part(function_call=types.FunctionCall(name="daily_trend", args={"days": 90}, id=str(i)))
        for i in range(5)
    ])])
    answer = answer_question("원산지", plant_tools, client=client)
    assert answer.error_code == "step_limit"
    assert not any(item["tool"] == "daily_trend" for item in answer.evidence)
    assert answer.model_calls == 1


@pytest.mark.parametrize("name,args", [
    ("__class__", {}), ("_frame", {}), ("eval", {"code": "print('unsafe')"}),
    ("daily_trend", {"days": True}), ("daily_trend", {"days": 0}),
    ("daily_trend", {"days": 91}), ("daily_trend", {"days": 2.5}),
    ("daily_trend", {"days": "7"}), ("summarize_period", {}),
    ("summarize_period", {"period": "today", "extra": "ignored?"}),
    ("summarize_period", {"period": " "}), ("summarize_period", {"period": "x" * 501}),
    ("consult_basil_guide", {"query": None}), ("get_current_status", {"days": 7}),
    ("get_current_status", []),
])
def test_allowlist_and_argument_validation(name, args, plant_tools):
    result = plant_tools.dispatch(name, args)
    assert result["ok"] is False
    assert isinstance(result["error"], str)


def test_unknown_model_tool_receives_structured_rejection(plant_tools):
    client = FakeClient([tool_reply("__dict__", {}), reply(types.Part(text="이 도구는 사용할 수 없어."))])
    answer = answer_question("현재 상태", plant_tools, client=client)
    assert answer.mode == "gemini"
    tool_response = client.calls[1]["contents"][-1].parts[0].function_response
    assert tool_response.id == "call-1"
    assert tool_response.response["ok"] is False


def test_provider_429_is_sanitized_without_retry_and_cleanup_cannot_override(plant_tools, monkeypatch):
    secret = "AIza-DO-NOT-LEAK-PRIVATE-TOKEN"
    failure = errors.ClientError(429, {"error": {"code": 429, "message": "quota; key=" + secret}})
    client = FakeClient([failure], close_error=RuntimeError("close failed " + secret))
    creation = []
    def factory(**kwargs):
        creation.append(kwargs)
        return client
    monkeypatch.setattr(genai, "Client", factory)
    answer = answer_question("현재 상태", plant_tools, api_key=secret)
    assert answer.mode == "local" and answer.error_code == "quota"
    assert answer.model_calls == 1 and client.closed == 1
    assert "한도" in answer.text and secret not in json.dumps(asdict(answer), ensure_ascii=False)
    assert creation[0]["http_options"].retry_options.attempts == 1
    assert creation[0]["http_options"].timeout == 20000



@pytest.mark.parametrize("status,message,expected", [
    (402, "Payment Required; previous 429 RESOURCE_EXHAUSTED", "billing"),
    (429, "Your prepayment credits are depleted. Visit AI Studio billing prepay.", "billing"),
    (429, "Your PREPAID CREDIT BALANCE has been exhausted.", "billing"),
    (429, "Too many requests: rate limit exceeded.", "quota"),
    (429, "Rate limit exceeded. Your prepaid credits remain available.", "quota"),
])
def test_payment_error_takes_priority_over_generic_quota(status, message, expected):
    failure = errors.ClientError(status, {"error": {"code": status, "message": message}})
    code, safe_message = classify_error(failure)
    assert code == expected
    assert message not in safe_message
    if expected == "billing":
        assert "운영자의 API 결제 상태" in safe_message
        assert all(word not in safe_message for word in ["분당", "일일", "무료", "재시도", "충전", "구매"])


def test_wrapped_embedding_prepayment_error_is_recognized_without_status_attribute():
    failure = RuntimeError("Error embedding content: 429 Your prepayment credits are depleted.")
    assert classify_error(failure)[0] == "billing"


@pytest.mark.parametrize("status,message", [
    (402, "Payment Required"),
    (429, "Your prepayment credits are depleted. See https://aistudio.google.com/billing/prepay"),
])
def test_billing_failure_keeps_local_evidence_without_retry_or_secret_leak(plant_tools, monkeypatch, status, message):
    secret = "fake-private-billing-test-key"
    failure = errors.ClientError(status, {"error": {"code": status, "message": message + "; key=" + secret}})
    client = FakeClient([failure])
    creation = []
    def factory(**kwargs):
        creation.append(kwargs)
        return client
    monkeypatch.setattr(genai, "Client", factory)
    answer = answer_question("현재 상태", plant_tools, api_key=secret)
    assert answer.mode == "local" and answer.error_code == "billing"
    assert answer.model_calls == len(client.calls) == 1
    assert client.closed == 1
    assert creation[0]["http_options"].retry_options.attempts == 1
    assert "운영자의 API 결제 상태" in answer.text and "기본 안내" in answer.text
    assert "2026-09-02" in answer.text
    assert any(record["tool"] == "get_current_status" and record["result"]["ok"] for record in answer.evidence)
    exported = json.dumps(asdict(answer), ensure_ascii=False)
    assert secret not in exported and message not in exported


def test_successful_answer_survives_transport_close_error(plant_tools, monkeypatch):
    client = FakeClient([reply(types.Part(text="확인한 기록이야."))], close_error=RuntimeError("cleanup token=secret"))
    monkeypatch.setattr(genai, "Client", lambda **kwargs: client)
    answer = answer_question("현재 상태", plant_tools, api_key="not-a-real-key")
    assert answer.mode == "gemini" and answer.text == "확인한 기록이야."
    assert client.closed == 1


def test_no_key_is_offline_and_stale_data_is_explicit(plant_tools, monkeypatch):
    monkeypatch.setattr(genai, "Client", lambda **kwargs: pytest.fail("No client should be created"))
    answer = answer_question("현재 상태", plant_tools)
    assert answer.mode == "local" and answer.model_calls == 0
    assert "2026-09-02" in answer.text
    assert "현재 식물 상태는 알 수 없어" in answer.text
    assert "원시값" in answer.text


@pytest.mark.parametrize("question", [None, 12, "", "   ", "q" * 2001])
def test_invalid_input_never_calls_provider(question, plant_tools):
    client = FakeClient([])
    answer = answer_question(question, plant_tools, client=client)
    assert answer.error_code == "input" and not client.calls and not answer.evidence


def test_missing_period_dictionary_error_is_rendered_as_text(plant_tools):
    answer = local_answer("2020-01 평균", plant_tools)
    assert isinstance(answer.text, str)
    assert "관측이 없습니다" in answer.text
    assert "invalid" not in answer.text
    assert answer.evidence[0]["result"]["error"]["code"] == "no_data"


def test_historical_state_query_never_turns_into_current_status(plant_tools):
    answer = local_answer("2025년 8월 15일 상태가 어땠어?", plant_tools)
    assert [item["tool"] for item in answer.evidence] == ["summarize_period"]
    assert answer.evidence[0]["arguments"]["period"] == "2025년8월15일"
    assert "29.0°C" in answer.text
    assert "2025-08-15" in answer.text


@pytest.mark.parametrize("question", [
    "작년 여름 상태", "최근 30일 평균", "2025-01-01부터 오늘까지 평균",
    "다음 주 상태 예측", "최근 2시간 상태", "지난 두 주 상태", "전체 기간 평균",
])
def test_unsupported_complex_local_queries_are_explicit_not_today(question, plant_tools):
    answer = local_answer(question, plant_tools)
    assert "정확히 해석할 수 없어" in answer.text
    assert answer.evidence == []


def test_supported_week_comparison_uses_selected_reference(plant_tools):
    answer = local_answer("이번 주와 지난주 비교", plant_tools)
    assert answer.evidence[0]["tool"] == "compare_periods"
    assert answer.evidence[0]["arguments"] == {"period_a": "this_week", "period_b": "last_week"}
    assert "마지막 측정일 2026-09-02" in answer.text


def test_model_context_uses_reference_mode_but_real_clock_for_freshness(plant_tools):
    client = FakeClient([reply(types.Part(text="마지막 측정일 기준이야."))])
    answer_question("오늘 평균", plant_tools, client=client)
    system = client.calls[0]["config"].system_instruction
    assert "analysis_reference" in system and "mode=latest" in system and "mode=calendar" in system
    assert "freshness.checked_at" in system
    records = json.loads(system.split("이번 질문의 조회 근거(JSON):\n", 1)[1])
    status = records[0]["result"]
    assert status["analysis_reference"]["date"] == "2026-09-02"
    assert status["freshness"]["checked_at"] == "2026-10-04T12:00:00+09:00"
    assert status["freshness"]["stale"] is True


def test_large_tool_evidence_is_replaced_with_complete_narrowing_response(plant_tools):
    client = FakeClient([
        tool_reply("daily_trend", {"days": 90}),
        reply(types.Part(text="조회할 날짜 범위를 줄여 줘.")),
    ])
    answer = answer_question("최근 추세를 알려줘", plant_tools, client=client)
    result = client.calls[1]["contents"][-1].parts[0].function_response.response
    assert result["ok"] is False and result["error"]["code"] == "evidence_budget"
    assert "days" in result["error"]["message"]
    # The inspection trace retains full bounded analytics; the LLM sees no
    # malformed or silently partial version of that JSON.
    trace = next(record for record in answer.evidence if record["tool"] == "daily_trend" and record["arguments"] == {"days": 90})
    assert len(trace["result"]["days"]) == 90
    context = client.calls[0]["config"].system_instruction.split("이번 질문의 조회 근거(JSON):\n", 1)[1]
    assert len(context) + len(json.dumps(result, ensure_ascii=False)) <= MAX_MODEL_EVIDENCE_CHARS


def test_evidence_budget_is_cumulative_across_successive_tools(plant_tools, monkeypatch):
    def medium(days):
        return {"ok": True, "days": days, "payload": "관측" * 4500}
    monkeypatch.setattr(plant_tools.analytics, "daily_trend", medium)
    client = FakeClient([
        tool_reply("daily_trend", {"days": 1}, "a"),
        tool_reply("daily_trend", {"days": 2}, "b"),
        reply(types.Part(text="나머지는 범위를 줄여 줘.")),
    ])
    answer = answer_question("온도 추세와 급변도", plant_tools, client=client)
    first = client.calls[1]["contents"][-1].parts[0].function_response.response
    second = client.calls[2]["contents"][-1].parts[0].function_response.response
    assert first["ok"] is True
    assert second["error"]["code"] == "evidence_budget"
    context = client.calls[0]["config"].system_instruction.split("이번 질문의 조회 근거(JSON):\n", 1)[1]
    assert len(context) + sum(len(json.dumps(item, ensure_ascii=False)) for item in [first, second]) <= MAX_MODEL_EVIDENCE_CHARS
    assert answer.mode == "gemini"


def test_large_preloaded_guide_is_bounded_and_trace_remains_honest(plant_tools, monkeypatch):
    monkeypatch.setattr(plant_tools.knowledge, "search", lambda query: {"status": "ok", "matches": [], "payload": "가이드" * 10000})
    client = FakeClient([reply(types.Part(text="짧은 질문으로 범위를 좁혀 줘."))])
    answer = answer_question("햇빛", plant_tools, client=client)
    context = client.calls[0]["config"].system_instruction.split("이번 질문의 조회 근거(JSON):\n", 1)[1]
    assert len(context) <= MAX_MODEL_EVIDENCE_CHARS
    records = json.loads(context)
    assert records[0]["result"]["error"]["code"] == "evidence_budget"
    assert len(answer.evidence[0]["result"]["payload"]) == 30000


def test_history_is_bounded_and_only_supported_message_roles_are_sent(plant_tools):
    history = [{"role": "assistant" if i % 2 else "user", "content": str(i) + "x" * 5000} for i in range(20)]
    client = FakeClient([reply(types.Part(text="현재 질문에 답할게."))])
    answer_question("현재 상태", plant_tools, client=client, history=history)
    messages = client.calls[0]["contents"][:-1]
    assert len(messages) <= 8
    assert sum(len(part.text or "") for message in messages for part in message.parts) <= MAX_HISTORY_CHARS
    assert messages[0].role == "user"
    assert all(len(part.text) <= 2000 for message in messages for part in message.parts)


@pytest.mark.parametrize("model,thinking", [("gemini-3.8-flash", True), ("gemini-2.5-flash", False)])
def test_thinking_budget_is_set_only_for_supported_model_family(plant_tools, model, thinking):
    client = FakeClient([reply(types.Part(text="기록을 확인했어."))])
    answer = answer_question("현재 상태", plant_tools, client=client, model=model)
    config = client.calls[0]["config"]
    assert answer.mode == "gemini"
    assert config.max_output_tokens == 2048
    if thinking:
        assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW
    else:
        assert config.thinking_config is None


def test_explicit_real_today_does_not_use_latest_record_date():
    from pathlib import Path
    import pandas as pd
    from plant_ai.analytics import SensorAnalytics
    from plant_ai.knowledge import BasilKnowledge
    from plant_ai.agent import PlantTools, local_answer
    frame = pd.DataFrame({"Timestamp": ["2026-09-20 12:00"], "Temperature": [25], "Humidity": [50], "Soil Moisture": [400]})
    analytics = SensorAnalytics(frame, now="2026-10-04 12:00", reference_mode="latest")
    knowledge = BasilKnowledge(Path(__file__).resolve().parents[1] / "basil_knowledge.json")
    response = local_answer("실제 오늘 평균은 어때?", PlantTools(analytics, knowledge))
    assert response.evidence[0]["arguments"] == {"period": "2026-10-04"}
    assert response.evidence[0]["result"]["row_count"] == 0


from plant_ai.routing import ALL_TOOLS, plan_question
from plant_ai.verification import audit_evidence


@pytest.mark.parametrize("question,intent,tool", [
    ("현재 상태", "current", "get_current_status"),
    ("오늘 평균 온도", "period", "summarize_period"),
    ("2026-09-01부터 2026-09-02까지 평균", "range", "summarize_range"),
    ("이번 주와 지난주 비교", "compare", "compare_periods"),
    ("최근 7일 온도 추세", "trend", "daily_trend"),
    ("최근 7일 급변", "events", "detect_events"),
])
def test_planned_evidence_is_preloaded_without_an_extra_model_call(plant_tools, question, intent, tool):
    client = FakeClient([reply(types.Part(text="해당 기간의 기록을 확인했어."))])
    answer = answer_question(question, plant_tools, client=client)
    assert answer.mode == "gemini"
    assert answer.model_calls == 1
    assert answer.audit["status"] == "satisfied"
    assert answer.audit["plan"]["intent"] == intent
    assert any(record["tool"] == tool for record in answer.evidence)
    assert all(record["evidence_id"].startswith("E") for record in answer.evidence)
    names = {item.name for item in client.calls[0]["config"].tools[0].function_declarations}
    assert names == {tool, "get_current_status"}
    assert "consult_basil_guide" not in names


def test_plan_range_includes_the_natural_language_final_date():
    plan = plan_question("2026-09-01부터 2026-09-02까지 평균")
    assert plan.calls == [{"tool": "summarize_range", "arguments": {"start": "2026-09-01", "end": "2026-09-03"}}]


@pytest.mark.parametrize("question", [
    "작년 여름 평균", "최근 2시간 추세", "내일 상태 예측", "2026-09-01부터 오늘까지",
    "온도 추세와 급변", "오늘 평균과 햇빛 조건", "2026-09-02부터 2026-09-01까지",
    "2026-09-01부터 2026-09-02 이전까지", "2026-100 평균", "지난 두 주 평균",
    "지금 흙이 마른 것 같아 물을 줘도 돼?", "상관관계를 분석해줘",
])
def test_unresolved_scope_keeps_all_tools_without_inventing_a_range(question):
    plan = plan_question(question)
    assert not plan.resolved
    assert plan.calls == []
    assert plan.allowed_tools == ALL_TOOLS


def test_period_followup_uses_user_context_but_not_assistant_claims(plant_tools):
    history = [{"role": "user", "content": "이번 달 평균 온도를 알려줘"},
               {"role": "assistant", "content": "지난달 수치는 999야"}]
    plan = plan_question("그럼 지난달은?", history)
    assert plan.resolved and plan.context_used
    assert plan.calls[0]["arguments"] == {"period": "last_month"}
    answer = answer_question("그럼 지난달은?", plant_tools, history=history)
    assert answer.audit["status"] == "satisfied"
    assert "24.0" in answer.text and "999" not in answer.text
    assert not plan_question("그럼 지난달은?", [{"role": "assistant", "content": "평균 온도"}]).resolved


def test_real_today_route_never_relabels_old_observations(plant_tools):
    client = FakeClient([reply(types.Part(text="지금 평균은 22도야."))])
    answer = answer_question("실제 오늘 평균", plant_tools, client=client)
    assert answer.mode == "local"
    assert answer.error_code == "evidence_empty"
    assert answer.audit["status"] == "empty"
    assert answer.audit["plan"]["calls"][0]["arguments"] == {"period": "2026-10-04"}
    assert "지금 평균은 22도야" not in answer.text
    assert "관측이 없습니다" in answer.text


def test_failed_required_query_blocks_ungrounded_model_completion(plant_tools, monkeypatch):
    monkeypatch.setattr(plant_tools.analytics, "summarize_period",
                        lambda period: {"ok": False, "error": {"code": "tool_error", "message": "요청한 통계를 확인할 수 없습니다."}})
    client = FakeClient([reply(types.Part(text="평균 온도는 999도야."))])
    answer = answer_question("오늘 평균", plant_tools, client=client)
    assert answer.mode == "local" and answer.error_code == "evidence_failed"
    assert answer.audit["status"] == "failed"
    assert "999" not in answer.text


def test_wrong_scope_tool_is_not_executed(plant_tools, monkeypatch):
    monkeypatch.setattr(plant_tools.analytics, "daily_trend", lambda days: pytest.fail("Out-of-scope tool executed"))
    client = FakeClient([tool_reply("daily_trend", {"days": 7}), reply(types.Part(text="마지막 관측 기록이야."))])
    answer = answer_question("현재 상태", plant_tools, client=client)
    result = client.calls[1]["contents"][-1].parts[0].function_response.response
    assert result["error"]["code"] == "route_rejected"
    assert answer.audit["status"] == "satisfied"
    assert any(card["status"] == "failed" for card in answer.audit["cards"])


def test_scope_audit_distinguishes_missing_empty_failed_and_unresolved(plant_tools):
    plan = plan_question("오늘 평균")
    assert audit_evidence(plan, [])["status"] == "missing"
    wrong = [{"tool": "summarize_period", "arguments": {"period": "yesterday"},
              "result": plant_tools.analytics.summarize_period("today")}]
    assert audit_evidence(plan, wrong)["status"] == "missing"
    empty = [{"tool": "summarize_period", "arguments": {"period": "today"},
              "result": {"ok": False, "error": {"code": "no_data", "message": "없음"}}}]
    assert audit_evidence(plan, empty)["status"] == "empty"
    failed = deepcopy(empty)
    failed[0]["result"]["error"]["code"] = "invalid_period"
    assert audit_evidence(plan, failed)["status"] == "failed"
    assert audit_evidence(plan_question("분석해줘"), [])["status"] == "unresolved"


def test_inspection_only_evidence_cannot_ground_a_model_answer(plant_tools):
    plan = plan_question("오늘 평균")
    evidence = [{"tool": "summarize_period", "arguments": {"period": "today"},
                 "result": plant_tools.analytics.summarize_period("today"), "model_visible": False}]
    audit = audit_evidence(plan, evidence)
    assert audit["status"] == "failed"
    assert audit["cards"][0]["metrics"]["temperature"]["mean"] == 22
    assert audit["cards"][0]["model_visible"] is False


def test_audit_is_explicitly_not_a_prose_truth_or_diagnosis_certificate(plant_tools):
    client = FakeClient([reply(types.Part(text="기록에 대해 설명할게."))])
    answer = answer_question("오늘 평균", plant_tools, client=client)
    assert answer.audit["scope"] == "query_scope_and_tool_execution"
    assert "모든 문장" in answer.audit["limitations"]
    assert "보증하지 않습니다" in answer.audit["limitations"]
    card = next(card for card in answer.audit["cards"] if card["tool"] == "summarize_period")
    assert card["analysis_reference"]["date"] == "2026-09-02"
    assert card["freshness"]["stale"] is True
    assert card["metrics"]["temperature"]["mean"] == 22
    assert card["source"] == "live"
    json.dumps(asdict(answer), allow_nan=False, ensure_ascii=False)


def test_no_thought_text_is_stored_in_audit_or_trace(plant_tools):
    client = FakeClient([reply(types.Part(text="PRIVATE-MODEL-REASONING", thought=True), types.Part(text="마지막 측정 기록이야."))])
    answer = answer_question("현재 상태", plant_tools, client=client)
    assert "PRIVATE-MODEL-REASONING" not in json.dumps(asdict(answer), ensure_ascii=False)


@pytest.mark.parametrize("question", ["오늘 최고 온도", "2025년 여름과 이번 주 상태 비교", "최근 7일 추세", "최근 7일 공백"])
def test_new_local_workflows_preserve_computed_evidence(question, plant_tools):
    answer = local_answer(question, plant_tools)
    assert answer.mode == "local" and answer.model_calls == 0
    assert answer.audit["status"] == "satisfied"
    assert answer.evidence and "정확히 해석할 수 없어" not in answer.text


def test_easy_narrator_is_visible_in_local_output_and_no_duplicate_styling(plant_tools):
    answer = answer_question("현재 상태", plant_tools, audience="easy", persona="narrator")
    assert "식물 관찰 안내" in answer.text
    assert "센서 숫자" in answer.text
    assert "흙 표면과 잎" in answer.text
    assert answer.audit["presentation"] == {"audience": "easy", "persona": "narrator"}
    client = FakeClient([RuntimeError("offline")])
    fallback = answer_question("현재 상태", plant_tools, client=client, audience="easy", persona="narrator")
    assert fallback.text.count("식물 관찰 안내") == 1
    assert fallback.text.count("쉽게 살펴볼게요") == 1


def test_guide_only_routing_avoids_unrelated_sensor_preloads(plant_tools):
    client = FakeClient([reply(types.Part(text="가이드의 햇빛 조건을 참고해 줘."))])
    answer = answer_question("햇빛 조건", plant_tools, client=client)
    assert answer.audit["status"] == "satisfied"
    assert [record["tool"] for record in answer.evidence] == ["consult_basil_guide"]
    assert [item.name for item in client.calls[0]["config"].tools[0].function_declarations] == ["consult_basil_guide"]


def test_humidity_and_uncalibrated_soil_are_not_presented_as_same_percentage(plant_tools, monkeypatch):
    monkeypatch.setattr(plant_tools.knowledge, "search", lambda query: pytest.fail("Unit question must not quote unrelated care-guide thresholds"))
    question = "공기 습도와 보정하지 않은 토양 센서값을 같은 퍼센트로 읽어도 될까?"
    answer = local_answer(question, plant_tools)
    assert "같은 퍼센트로 비교하면 안 돼" in answer.text
    assert "보정되지 않은 원시값" in answer.text
    assert [record["tool"] for record in answer.evidence] == ["get_current_status"]
    assert answer.audit["status"] == "satisfied"


def test_soil_units_words_do_not_override_an_explicit_period(plant_tools):
    answer = local_answer("오늘 토양 센서 원시값 평균", plant_tools)
    assert answer.evidence[0]["tool"] == "summarize_period"
    assert answer.audit["plan"]["intent"] == "period"
    assert "520.0" in answer.text


def test_time_weighted_request_distinguishes_observation_mean_and_supported_intervals():
    frame = pd.DataFrame({
        "Timestamp": ["2026-09-02 11:50", "2026-09-02 11:52", "2026-09-02 11:58"],
        "Temperature": [20, 30, 40], "Humidity": [50, 60, 70], "Soil Moisture": [500, 510, 520],
    })
    analytics = SensorAnalytics(frame, now="2026-10-04 12:00+09:00", reference_mode="latest")
    knowledge = BasilKnowledge(Path(__file__).resolve().parents[1] / "basil_knowledge.json")
    answer = local_answer("오늘 시간 가중 평균 온도", PlantTools(analytics, knowledge))
    result = answer.evidence[0]["result"]
    assert result["metrics"]["temperature"]["mean"] == 30
    assert result["metrics"]["temperature"]["time_weighted_mean"] == 27.5
    assert "27.5" in answer.text
    assert "10분 이하" in answer.text and "긴 공백" in answer.text
    assert "측정값 개수 기준" in answer.text
