"""Talking Basil Pot: Streamlit exhibit and sensor exploration interface."""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import time

import pandas as pd
import streamlit as st

from plant_ai.agent import Answer, PlantTools, answer_question, local_answer
from plant_ai.analytics import SensorAnalytics
from plant_ai.data import DataLoadError, fetch_sheet, get_settings, load_csv_bytes, make_demo_frame
from plant_ai.knowledge import BasilKnowledge
from plant_ai.inquiry import get_missions, check_answer, SessionRecorder
from plant_ai.exhibition import payload_from_frame, render_html

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="쫑알쫑알 화분", page_icon="🌿", layout="wide")
st.title("🌿 쫑알쫑알 화분")
st.caption("바질의 기록을 함께 읽어요 · 센서와 관리 가이드에 근거한 대화")

try:
    secrets = dict(st.secrets)
except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
    secrets = {}
settings = get_settings(secrets)
if settings.demo_site_url:
    st.link_button("API 없이 기록 데모 보기", settings.demo_site_url)


def frame_fingerprint(frame):
    """Hash every row without serializing another full CSV or sampling history."""
    digest = hashlib.sha256()
    schema = [(str(column), str(dtype)) for column, dtype in zip(frame.columns, frame.dtypes)]
    digest.update(json.dumps(schema, ensure_ascii=False).encode("utf-8"))
    for offset in range(0, len(frame), 20_000):
        rows = pd.util.hash_pandas_object(frame.iloc[offset:offset + 20_000], index=True)
        digest.update(rows.to_numpy(dtype="uint64").tobytes())
    return digest.hexdigest()


@st.cache_data(ttl=120, max_entries=4, show_spinner=False)
def sheet_snapshot(sheet_id, gid):
    frame = fetch_sheet(sheet_id, gid)
    # Compute once per fetch; cache_data preserves attrs along with the values.
    frame.attrs["exhibit_content_signature"] = frame_fingerprint(frame)
    return frame


@st.cache_resource(ttl=120, max_entries=8, show_spinner=False)
def prepare_snapshot(_frame, source, reference_mode, frame_signature):
    # Streamlit samples large DataFrames when hashing. The explicit full-content
    # signature also catches corrections in rows outside that sample.
    return SensorAnalytics(_frame, source=source, reference_mode=reference_mode)


@st.cache_resource(show_spinner=False)
def guide():
    return BasilKnowledge(ROOT / "basil_knowledge.json")


def reset_conversation():
    st.session_state.messages = []
    st.session_state.telemetry = []
    st.session_state.inquiry_replies = {}
    st.session_state.offline_export = None


with st.sidebar:
    st.header("체험 설정")
    data_mode = st.selectbox("데이터 선택", ["Google Sheets 기록", "CSV 기록 파일", "합성 시연 데이터"], key="data_mode")
    reference_label = st.radio("분석 기준", ["마지막 기록 기준", "실제 오늘 기준"],
                               help="수집이 멈춰도 마지막 관측일의 오늘·이번 주·이번 달을 탐색할 수 있어요. 측정 최신성은 실제 시각으로 별도 표시합니다.", key="reference_label")
    reference_mode = "latest" if reference_label == "마지막 기록 기준" else "calendar"
    sheet_id = settings.sheet_id
    sheet_gid = settings.sheet_gid
    uploaded = None
    if data_mode == "Google Sheets 기록" and not sheet_id:
        sheet_id = st.text_input("Google Sheets 문서 ID", help="기존 배포의 SHEET_ID 설정을 그대로 사용할 수 있어요.")
        sheet_gid = st.text_input("시트 탭 ID", value=sheet_gid)
    elif data_mode == "CSV 기록 파일":
        uploaded = st.file_uploader("센서 기록 CSV", type=["csv"], help="25MB 이하 · UTF-8 또는 CP949")
    st.caption("페이지를 열어 둔 동안 2분마다 기록을 확인합니다.")
    if st.button("기록 다시 읽기", width="stretch"):
        sheet_snapshot.clear()
        prepare_snapshot.clear()
        st.rerun()
    if st.button("대화 초기화", width="stretch"):
        reset_conversation()
        st.rerun()
    st.divider()
    persona_label = st.selectbox("안내 목소리", ["바질 캐릭터", "차분한 해설자"], key="persona_label")
    persona = "basil" if persona_label == "바질 캐릭터" else "narrator"
    easy = st.checkbox("쉬운 설명", value=False, key="easy_explanation")
    audience = "easy" if easy else "general"
    api_key = settings.api_key
    if api_key:
        st.caption("Gemini 키 설정됨 · 실제 연결은 질문 시 확인")
    else:
        st.info("키 없이도 기록 탐색과 가이드 기본 안내를 사용할 수 있어요.")
        api_key = st.text_input("Gemini API 키 · 이 세션만", type="password", key="session_api_key")
    with st.expander("운영 설정"):
        if settings.api_key:
            model = settings.model
            st.caption(f"운영 모델: {model} · 운영자가 설정한 모델로 응답합니다.")
        else:
            model = st.text_input("Gemini 모델", value=settings.model)
        st.caption("무료 여부와 사용량 한도는 연결한 Google 프로젝트 기준입니다. 이 앱은 결제를 활성화하지 않습니다.")
        st.caption("기존 GOOGLE_API_KEY, SHEET_ID 설정 지원 · 키는 대화와 근거 파일에 포함하지 않습니다.")
    st.caption("토양 수분은 보정 전 센서 원시값입니다. % 또는 물주기 필요로 단정하지 않아요.")


def show_evidence(items, audit=None):
    with st.expander("답변의 근거 확인"):
        if audit:
            labels = {"satisfied": "질문에 맞는 조회 근거 확보", "empty": "요청한 범위에 기록 없음",
                      "failed": "조회 실패", "missing": "필요한 조회 근거 부족", "unresolved": "질문 범위 자동 확인 어려움"}
            st.write(labels.get(audit.get("status"), "조회 확인"))
            st.caption("질문의 기간·조회 실행 여부를 확인합니다. 생성된 모든 문장이 사실임을 보증하는 검사는 아닙니다.")
            st.json(audit, expanded=False)
        for item in items:
            result = item.get("result", {})
            if item.get("tool") == "consult_basil_guide":
                for match in result.get("matches", []):
                    st.markdown(f"**{match['title']}**")
                    st.write(match["text"])
                    st.caption(f"사용자 작성 가이드 · {match['source']}")
            else:
                st.json(result, expanded=False)


def display_error(result):
    error = result.get("error", {})
    return error.get("message", "해당 기간에 기록이 없습니다.") if isinstance(error, dict) else str(error)


def show_summary(result):
    if not result.get("ok"):
        st.info(display_error(result))
        return
    st.caption(f"{result['start']}부터 {result['end']} 전까지 · 관측 {result['row_count']:,}개")
    rows = []
    for label, metric in [("온도", "temperature"), ("공기 습도", "humidity"), ("토양 센서", "soil_moisture")]:
        stat = result["metrics"][metric]
        rows.append({"측정 항목": label, "단위": stat["unit"], "관측 평균": stat["mean"],
                     "지원 시간 평균": stat.get("time_weighted_mean"),
                     "최소": stat["min"], "최대": stat["max"], "유효 관측": stat["count"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    coverage = result.get("coverage", {})
    sampling = result.get("sampling", {})
    if coverage:
        fraction = coverage["observed_support_fraction"]
        st.caption(f"조회 기간 중 짧은 간격의 기록이 뒷받침하는 시간: {fraction:.1%} · "
                   f"10분 초과 공백 {sampling['gap_count']}개 · 최대 관측 간격 {sampling.get('max_interval_seconds') or 0:,.0f}초")
        st.caption("지원 시간 평균은 양쪽 측정값이 유효하고 간격이 10분 이하인 구간에만 앞의 값을 적용한 근사입니다. 긴 공백과 마지막 측정 이후는 채우지 않습니다.")


def show_envelope(analytics, days):
    envelope = analytics.chart_envelope(days=days, max_bins=120)
    if envelope.empty:
        st.info("선택한 기준과 기간에 표시할 기록이 없습니다.")
        return
    st.caption("각 구간의 최솟값·최댓값을 세로 범위로, 유효한 구간의 평균을 점과 선으로 표시합니다. 긴 수집 공백이나 결측이 있는 구간에서는 선을 끊습니다.")
    for metric, label in [("temperature", "온도 (°C)"), ("humidity", "공기 습도 (%)"), ("soil_moisture", "토양 센서 원시값")]:
        plot = envelope.loc[envelope["metric"] == metric].copy()
        # Use KST wall time for chart labels on browsers in any timezone.
        plot["Timestamp"] = pd.to_datetime(plot["Timestamp"]).dt.strftime("%Y-%m-%dT%H:%M:%S")
        st.vega_lite_chart(plot, {
            "height": 170,
            "encoding": {
                "x": {"field": "Timestamp", "type": "temporal", "title": "기록 시각 (KST)"},
                "tooltip": [
                    {"field": "min", "type": "quantitative", "title": "최소"},
                    {"field": "max", "type": "quantitative", "title": "최대"},
                    {"field": "mean", "type": "quantitative", "title": "관측 평균"},
                    {"field": "count", "type": "quantitative", "title": "유효 관측 수"},
                    {"field": "min_at", "type": "nominal", "title": "최소 시각"},
                    {"field": "max_at", "type": "nominal", "title": "최대 시각"},
                    {"field": "contains_gap", "type": "nominal", "title": "긴 공백 포함"},
                ],
            },
            "layer": [
                {"mark": {"type": "rule", "color": "#65a987", "strokeWidth": 3, "opacity": 0.6},
                 "encoding": {"y": {"field": "min", "type": "quantitative", "title": label, "scale": {"zero": False}},
                              "y2": {"field": "max"}}},
                {"mark": {"type": "line", "color": "#246646", "point": True},
                 "encoding": {"y": {"field": "line_value", "type": "quantitative", "title": label},
                              "detail": {"field": "segment_id", "type": "nominal"}}},
            ],
        }, width="stretch")


@st.fragment(run_every=120)
def experience():
    source = "live"
    identity = f"sheet:{sheet_id}:{sheet_gid}"
    connection_error = None
    try:
        if data_mode == "합성 시연 데이터":
            source, identity = "demo", "demo"
            if "demo_frame" not in st.session_state:
                st.session_state.demo_frame = make_demo_frame()
                st.session_state.demo_frame.attrs["exhibit_content_signature"] = frame_fingerprint(st.session_state.demo_frame)
            frame = st.session_state.demo_frame
        elif data_mode == "CSV 기록 파일":
            if uploaded is None:
                st.info("왼쪽에서 CSV 기록 파일을 선택해 주세요. 실제 센서 파일 없이 둘러보려면 ‘합성 시연 데이터’를 선택하세요.")
                return
            content = uploaded.getvalue()
            content_signature = hashlib.sha256(content).hexdigest()
            identity, source = "csv:" + content_signature, "upload"
            frame = load_csv_bytes(content)
            frame.attrs["exhibit_content_signature"] = content_signature
        else:
            if not sheet_id:
                st.info("Google Sheets 문서 ID를 설정하면 실제 기록을 읽을 수 있어요. CSV 파일이나 합성 시연 데이터로도 시작할 수 있습니다.")
                return
            with st.spinner("센서 기록을 읽는 중…"):
                frame = sheet_snapshot(sheet_id, sheet_gid)
            st.session_state.last_snapshot = (identity, frame)
    except DataLoadError as error:
        previous = st.session_state.get("last_snapshot")
        if data_mode == "Google Sheets 기록" and previous and previous[0] == identity:
            frame = previous[1]
            connection_error = str(error)
        else:
            st.error(str(error))
            st.info("기록 연결을 복구하거나 CSV 기록 파일을 선택해 주세요. 시연 데이터는 직접 선택할 때만 사용합니다.")
            return
    conversation_identity = (identity, reference_mode, model, persona, audience)
    if st.session_state.get("conversation_identity") != conversation_identity:
        reset_conversation()
        st.session_state.conversation_identity = conversation_identity
    frame_signature = frame.attrs.get("exhibit_content_signature") or frame_fingerprint(frame)
    offline_identity = (identity, frame_signature)
    if st.session_state.get("offline_source_identity") != offline_identity:
        st.session_state.offline_export = None
        st.session_state.offline_source_identity = offline_identity
    analytics = prepare_snapshot(frame, source, reference_mode, frame_signature)
    current = analytics.current_status()
    metadata = analytics.metadata()
    reference = current.get("analysis_reference", {})
    fresh = current["freshness"]
    if source == "demo":
        st.warning("합성 시연 데이터 · 실제 바질에서 측정한 값이 아닙니다.")
    elif source == "upload":
        st.info("업로드한 기록 분석 · 실시간 연결이 아닙니다.")
    if connection_error:
        st.warning("연결 중단 · 이 세션에서 마지막으로 읽은 기록을 표시합니다. " + connection_error)
    if fresh["stale"]:
        st.info("최근 측정이 없어요. 아두이노가 꺼져 있거나 전송이 멈췄을 수 있어요. 마지막 기록은 분석할 수 있지만 현재 상태는 확인할 수 없습니다.")
    st.caption(f"분석 기준일: {reference.get('date') or '기록 없음'} ({reference_label}) · 마지막 측정: {fresh['last_sample_at'] or '없음'}")
    if not current.get("ok"):
        st.error(display_error(current))
        with st.expander("데이터 형식 확인"):
            st.json(metadata)
        return
    values = current["values"]
    columns = st.columns(3)
    for col, title, key, unit in zip(columns, ["온도", "공기 습도", "토양 센서"],
                                     ["temperature", "humidity", "soil_moisture"], ["°C", "%", "원시값"]):
        value = values.get(key)
        col.metric(title, "측정 없음" if value is None else f"{value:,.1f} {unit}")
    st.caption(f"분석 가능한 관측 {metadata['row_count']:,}개 · {metadata.get('first_sample_at')}부터")
    inquiry_tab, chat_tab, history_tab, quality_tab = st.tabs(["식물 탐구", "바질과 대화", "기록 탐색", "데이터 상태"])
    tools = PlantTools(analytics, guide())
    if "rehearsal" not in st.session_state:
        st.session_state.rehearsal = SessionRecorder()
    recorder = st.session_state.rehearsal
    pending_question = None
    mission_id = None
    inquiry_reply = None
    with inquiry_tab:
        st.subheader("눈앞의 식물과 기록을 함께 읽어요")
        st.write("식물을 먼저 살펴본 뒤 기록과 비교하고, 궁금한 점을 질문해 보세요.")
        missions = get_missions()
        mission_map = {m["id"]: m for m in missions}
        mission_id = st.selectbox("탐구 주제", list(mission_map),
                                  format_func=lambda key: mission_map[key]["title"], key="inquiry_mission")
        mission = mission_map[mission_id]
        st.markdown("**1 · 식물을 직접 살펴보기**")
        st.write(mission["observe_prompt"])
        if st.button("식물을 살펴봤어요", key="observe_complete"):
            recorder.start_task(mission_id, mode="character" if persona == "basil" else "explainer")
            recorder.record("observation_self_report", taskid=mission_id, source=source, reference_mode=reference_mode)
            st.success("이제 실제로 본 모습과 센서가 알려주는 정보를 구분해 보세요.")
        st.caption("이 버튼은 관람객의 자기보고입니다. 카메라로 관찰 행동이나 식물 상태를 측정하지 않습니다.")
        st.markdown("**2 · 기록으로 확인하기**")
        st.write(f"마지막 센서 기록: {fresh['last_sample_at']} · 지금 눈앞의 식물 모습은 센서 기록만으로 알 수 없어요.")
        with st.expander("기준일의 기록 카드", expanded=False):
            show_summary(analytics.summarize_period("today"))
        st.markdown("**3 · 질문하고 생각해 보기**")
        st.write(mission["question"])
        if st.button("이 질문의 기본 안내 보기", key="mission_ask"):
            pending_question = mission["question"]
        inquiry_reply = st.empty()
        if mission_id in st.session_state.inquiry_replies:
            inquiry_reply.info(st.session_state.inquiry_replies[mission_id])
        choice_map = {choice["key"]: choice["label"] for choice in mission["choices"]}
        choice = st.radio(mission["quiz_prompt"], list(choice_map),
                          format_func=choice_map.get, index=None, key=f"quiz_{mission_id}")
        if st.button("생각 확인하기", key="check_quiz", disabled=choice is None):
            checked = check_answer(mission_id, choice)
            (st.success if checked["correct"] else st.info)(checked["explanation"])
            recorder.start_task(mission_id, mode="character" if persona == "basil" else "explainer")
            recorder.record("quiz_answer", taskid=mission_id, success=checked["correct"])
            recorder.finish_task(mission_id, success=checked["correct"])
        st.caption("미션 질문의 기본 안내와 퀴즈는 외부 AI 없이 동작합니다. 추가 질문은 ‘바질과 대화’에서 이어가세요.")
    with chat_tab:
        st.caption("예: 마지막 상태는 어때? / 지난주와 이번 주를 비교해 줘 / 바질은 어떤 온도를 좋아해?")
        for item in st.session_state.messages:
            with st.chat_message(item["role"]):
                st.markdown(item["content"])
                if item.get("evidence"):
                    show_evidence(item["evidence"], item.get("audit"))
                if item.get("mode"):
                    st.caption("Gemini 응답" if item["mode"] == "gemini" else "기본 안내 · AI 생성 응답 아님")
        question = st.chat_input("바질의 기록이나 키우는 법을 물어보세요", max_chars=2000) or pending_question
        if question:
            history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
            st.session_state.messages.append({"role": "user", "content": question})
            with st.chat_message("user"):
                st.markdown(question)
            with st.chat_message("assistant"):
                with st.spinner("기록과 가이드를 확인하는 중…"):
                    if pending_question:
                        answer = Answer("로컬 탐구 안내\n\n" + mission["explanation"], "local",
                                        evidence=[{"tool": "local_inquiry_content",
                                                   "arguments": {"mission_id": mission_id},
                                                   "result": {"source": "plant_ai/inquiry.py", "title": mission["title"],
                                                              "explanation": mission["explanation"]}}])
                        if mission_id == "record_time":
                            status = tools.dispatch("get_current_status", {})
                            answer.evidence.append({"tool": "get_current_status", "arguments": {}, "result": status})
                            answer.text += f"\n\n현재 선택한 자료의 마지막 측정: {status.get('timestamp') or '없음'}."
                            if status.get("freshness", {}).get("stale", True):
                                answer.text += " 오래된 기록이므로 지금 화분의 상태는 알 수 없어요."
                    elif time.time() < st.session_state.get("cooldown_until", 0):
                        answer = local_answer(question, tools, "API 한도 대기 중 · 기본 안내를 제공해요.", audience=audience, persona=persona)
                    else:
                        answer = answer_question(question, tools, api_key=api_key, model=model, history=history, audience=audience, persona=persona)
                    if answer.error_code == "quota":
                        st.session_state.cooldown_until = time.time() + 60
                st.markdown(answer.text)
                show_evidence(answer.evidence, answer.audit)
                st.caption("Gemini 응답" if answer.mode == "gemini" else "기본 안내 · AI 생성 응답 아님")
            st.session_state.messages.append({"role": "assistant", "content": answer.text,
                                              "evidence": answer.evidence, "mode": answer.mode, "audit": answer.audit})
            if pending_question and inquiry_reply is not None:
                st.session_state.inquiry_replies[mission_id] = answer.text
                inquiry_reply.info(answer.text)
            recorder.record("ai_request", mode="character" if persona == "basil" else "explainer",
                            latency=answer.elapsed_ms, calls=answer.model_calls, source=source,
                            reference_mode=reference_mode)
            st.session_state.messages = st.session_state.messages[-40:]
            st.session_state.telemetry.append({"mode": answer.mode, "error_code": answer.error_code,
                                               "model_calls": answer.model_calls, "elapsed_ms": answer.elapsed_ms,
                                               "tokens": answer.tokens, "tool_count": len(answer.evidence)})
            st.session_state.telemetry = st.session_state.telemetry[-100:]
    with history_tab:
        days = st.select_slider("추세 기간", options=[7, 14, 30, 90], value=14)
        show_envelope(analytics, days)
        a_col, b_col = st.columns(2)
        choices = {"기준일": "today", "기준일 전날": "yesterday", "이번 주": "this_week",
                   "지난주": "last_week", "이번 달": "this_month", "지난달": "last_month"}
        with a_col:
            label_a = st.selectbox("비교 기간 A", list(choices), index=3)
            show_summary(analytics.summarize_period(choices[label_a]))
        with b_col:
            label_b = st.selectbox("비교 기간 B", list(choices), index=2)
            show_summary(analytics.summarize_period(choices[label_b]))
        st.caption("평균은 유효 관측값 기준입니다. 수집 중단이나 기간 길이 차이가 있어 단순 평균 차이를 원인이나 생육 개선으로 해석하지 않습니다.")
        with st.expander("원하는 날짜 범위 조회"):
            date_a = st.date_input("시작일", value=pd.Timestamp(metadata["first_sample_at"]).date())
            date_b = st.date_input("마지막 포함일", value=pd.Timestamp(metadata["last_sample_at"]).date())
            if st.button("기간 조회"):
                end = pd.Timestamp(date_b) + pd.Timedelta(days=1)
                show_summary(analytics.summarize_range(str(date_a), str(end.date())))
        with st.expander("수집 공백·급변 후보"):
            events = analytics.detect_events(days=days)
            st.caption("규칙에 따른 탐색 결과입니다. 물주기나 질병의 원인을 진단한 결과가 아닙니다.")
            st.json(events, expanded=False)
    with quality_tab:
        st.write("수집 중단, 잘못된 시각, 중복, 결측과 센서값 범위를 확인합니다.")
        st.json(metadata, expanded=False)
        if st.session_state.telemetry:
            metrics = pd.DataFrame(st.session_state.telemetry)
            st.dataframe(metrics, hide_index=True, width="stretch")
            st.download_button("이 세션의 성능 기록 받기", metrics.to_csv(index=False).encode("utf-8-sig"),
                               file_name="plant-session-metrics.csv", mime="text/csv")
        with st.expander("이 기록으로 오프라인 열람본 만들기"):
            st.write("지금 선택한 자료의 그래프·대표 날짜·기본 질문 답변을 단일 HTML에 담습니다.")
            st.caption("파일을 만든 뒤에도 측정 날짜는 그대로 유지됩니다. 합성 자료는 합성으로 표시하며, 자유 AI 대화는 포함하지 않습니다.")
            if st.button("태블릿용 열람본 생성", key="build_offline"):
                st.session_state.offline_export = None
                try:
                    with st.spinner("기록을 정리하는 중…"):
                        payload, _ = payload_from_frame(frame, kind="synthetic" if source == "demo" else "actual",
                                                        source_name="합성 시연 자료" if source == "demo" else "사용자가 선택한 센서 기록")
                        st.session_state.offline_export = {
                            "source_identity": offline_identity,
                            "html": render_html(payload).encode("utf-8"),
                            "json": json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")}
                except ValueError as error:
                    st.error(f"열람본을 만들 수 없습니다. {error}")
                except OSError:
                    st.error("열람본 파일을 준비하지 못했습니다. 앱 설치 파일을 확인한 뒤 다시 시도해 주세요.")
            offline = st.session_state.offline_export
            if offline and offline.get("source_identity") == offline_identity:
                st.download_button("단일 HTML 받기", offline["html"], file_name="plant-tablet.html", mime="text/html")
                st.download_button("열람 데이터 JSON 받기", offline["json"], file_name="plant-exhibition-data.json", mime="application/json")
                st.caption("JavaScript가 실행되는 브라우저에서 여세요. iPad 파일 미리보기에서는 버튼이 동작하지 않을 수 있으므로 별도로 준비한 PDF 또는 전시 PC의 로컬 주소를 사용하세요.")
        with st.expander("운영자용 체험 리허설"):
            st.write("진행 순서와 버튼 기록을 점검하는 도구입니다. 관람객 연구 참여 동의나 연구 결과를 대신하지 않습니다.")
            st.caption("시작 버튼을 누른 뒤에만 임의의 세션 번호와 제한된 활동·성능 항목을 메모리에 기록합니다. 질문·답변·음성·이름은 이 내보내기 파일에 넣지 않습니다.")
            if st.button("리허설 기록 시작", key="rehearsal_start",
                         disabled=recorder.enabled and recorder.export()["summary"]["sessions_finished"] == 0):
                st.session_state.rehearsal = SessionRecorder(enabled=True)
                st.session_state.rehearsal.start()
                st.rerun()
            if recorder.enabled:
                if st.button("리허설 기록 종료", key="rehearsal_finish"):
                    recorder.finish()
                payload = recorder.export()
                st.json(payload, expanded=False)
                st.download_button("리허설 기록 받기", recorder.export_json(),
                                   file_name="plant-rehearsal.json", mime="application/json")
                if st.button("리허설 기록 지우기", key="rehearsal_clear"):
                    st.session_state.rehearsal = SessionRecorder()
                    st.rerun()
            st.caption("미션 버튼 사이의 경과 시간은 실제 식물을 본 시간이나 집중 시간의 측정값이 아닙니다. 정식 비교 실험의 동의·조건 배정·설문은 별도입니다.")
        st.caption("센서는 환경을 측정합니다. 바질의 말투는 체험을 위한 의인화이며 실제 감정을 측정하지 않습니다.")


experience()
