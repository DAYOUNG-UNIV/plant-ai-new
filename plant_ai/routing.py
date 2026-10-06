"""Conservative deterministic routing, without an extra model request.

A plan narrows tools only when its time expression and operation are explicit.
Unresolved questions retain the complete read-only tool registry.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
import re

ALL_TOOLS = ["get_current_status", "summarize_period", "summarize_range",
             "compare_periods", "daily_trend", "detect_events", "consult_basil_guide"]
PERIOD = re.compile(
    r"(?<!\d)(?:\d{4}-\d{2}(?:-\d{2})?|\d{4}년\d{1,2}월(?:\d{1,2}일)?|"
    r"\d{4}년?(?:spring|summer|autumn|fall|winter|봄|여름|가을|겨울)|"
    r"지난달|이번달|지난주|이번주|어제|오늘|yesterday|today|last_week|this_week|last_month|this_month)(?!\d)"
)
ALIASES = {"지난달":"last_month", "이번달":"this_month", "지난주":"last_week",
           "이번주":"this_week", "어제":"yesterday", "오늘":"today"}
UNKNOWN_TIME = re.compile(r"\d{4}|\d+(?:월|일|주|년|시간|분)|작년|재작년|저번|내일|다음|내년|ago|next")
CARE = re.compile(r"햇빛|햇볕|가이드|키우|재배|가지치기|수확|비료|원산지|적정|물주|물줘|물을|질병|병충해|잎|흙|sunlight|guide|care")
ANALYSIS = re.compile(r"평균|요약|통계|리포트|상태|최고|최저|최대|최소|온도|습도|토양|수분|average|summary|mean|min|max")
UNSUPPORTED = re.compile(r"예측|내일|다음|내년|상관|원인|전체|모든|횟수|얼마나자|predict|correlation|cause")


@dataclass
class QueryPlan:
    intent: str = "unresolved"
    resolved: bool = False
    calls: list[dict] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=lambda: list(ALL_TOOLS))
    context_used: bool = False
    note: str = "질문 범위가 불명확하여 전체 읽기 도구를 유지합니다."

    def to_dict(self):
        return asdict(self)


def _resolved(intent, calls, *, context_used=False, note=""):
    names = list(dict.fromkeys(call["tool"] for call in calls))
    # Freshness is a useful prerequisite for every sensor query.
    if intent != "guide" and "get_current_status" not in names:
        names.insert(0, "get_current_status")
    return QueryPlan(intent, True, calls, names, context_used, note)



def is_units_question(question):
    compact = re.sub(r"\s+", "", str(question).lower())
    if PERIOD.search(compact) or re.search(r"평균|통계|요약|추세|급변|최고|최저|최대|최소|mean|average|trend", compact):
        return False
    return bool(re.search(r"토양|흙|soil", compact) and
                re.search(r"퍼센트|%|percent|보정|원시값|단위", compact))


def plan_question(question: str, history=None, real_today=None) -> QueryPlan:
    if not isinstance(question, str) or not question.strip():
        return QueryPlan()
    compact = re.sub(r"\s+", "", question.lower())
    if is_units_question(question):
        return _resolved("current", [{"tool": "get_current_status", "arguments": {}}], note="현재 센서의 단위·보정 상태를 확인합니다.")
    if UNSUPPORTED.search(compact):
        return QueryPlan(note="예측·원인 또는 지원되지 않는 범위는 자동으로 추측하지 않습니다.")
    periods = [ALIASES.get(match.group(), match.group()) for match in PERIOD.finditer(compact)]
    remainder = PERIOD.sub("", compact)
    day_match = re.search(r"(?:최근|지난|last)(\d{1,3})(?:일|days?)", compact)
    trend = bool(re.search(r"추세|변화|trend", compact))
    events = bool(re.search(r"급변|공백|끊|이상|event|anomal", compact))
    compare = bool(re.search(r"비교|compare", compact))
    if (sum((trend, events, compare)) > 1 or
        (CARE.search(compact) and (periods or trend or events or compare))):
        return QueryPlan(note="복합 질문은 도구를 제한하지 않고 각 근거를 확인합니다.")
    if day_match:
        remainder = remainder.replace(day_match.group(), "")
    if UNKNOWN_TIME.search(remainder) or (not (trend or events) and re.search(r"최근|지난|이전|저번|동안", remainder)):
        return QueryPlan(note="해석되지 않은 날짜나 기간이 있어 자동 조회하지 않습니다.")
    if "실제오늘" in compact:
        if periods != ["today"] or not real_today:
            return QueryPlan(note="실제 오늘 날짜를 확인한 뒤 조회해야 합니다.")
        periods = [str(real_today)[:10]]
    if trend or events:
        if periods or (re.search(r"최근|지난|last", remainder) and day_match is None and not re.search(r"최근(?:추세|변화|급변|공백)", compact)):
            return QueryPlan(note="일별 도구가 지원하는 최근 일수로 범위를 명시해 주세요.")
        days = int(day_match.group(1)) if day_match else 7
        if not 1 <= days <= 90:
            return QueryPlan(note="최근 일별 분석은 1~90일을 지원합니다.")
        tool = "daily_trend" if trend else "detect_events"
        return _resolved("trend" if trend else "events", [{"tool": tool, "arguments": {"days": days}}],
                         note="analysis_reference 기준 최근 일수입니다." + (" 기본 7일을 사용합니다." if not day_match else ""))
    if day_match:
        return QueryPlan(note="최근 N일 평균은 날짜 범위로 명시해 주세요.")
    if compare:
        if len(periods) != 2 or re.search(r"부터|까지|~", compact):
            return QueryPlan(note="비교할 두 기간이 명확해야 합니다.")
        return _resolved("compare", [{"tool": "compare_periods", "arguments": {"period_a": periods[0], "period_b": periods[1]}}])
    if len(periods) == 2 and re.search(r"부터|~", compact):
        if not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in periods):
            return QueryPlan(note="날짜 범위는 두 개의 YYYY-MM-DD 날짜가 필요합니다.")
        try:
            start, inclusive_end = [date.fromisoformat(value) for value in periods]
            if inclusive_end < start:
                return QueryPlan(note="시작일과 종료일의 순서를 확인해 주세요.")
            end = inclusive_end + timedelta(days=1)
        except ValueError:
            return QueryPlan(note="유효하지 않은 날짜입니다.")
        if re.search(r"미포함|제외|exclusive|이전", compact):
            return QueryPlan(note="끝 날짜 포함 여부를 자동으로 추측하지 않습니다.")
        return _resolved("range", [{"tool": "summarize_range", "arguments": {"start": start.isoformat(), "end": end.isoformat()}}],
                         note="자연어의 마지막 날짜를 포함하도록 종료일 다음 날을 끝 제외 경계로 조회합니다.")
    if len(periods) > 1 or re.search(r"부터|까지|~", compact):
        return QueryPlan(note="여러 기간이나 열린 범위는 도구를 제한하지 않습니다.")
    if len(periods) == 1:
        context_used = False
        if not ANALYSIS.search(compact):
            previous = next((str(item.get("content", ""))[:2000] for item in reversed((history or [])[-8:])
                             if isinstance(item, dict) and item.get("role") == "user"), "")
            previous_compact = re.sub(r"\s+", "", previous.lower())
            if not previous or not re.search(r"평균|요약|통계|average|summary|mean", previous_compact):
                return QueryPlan(note="이 기간에 무엇을 확인할지 명확하지 않습니다.")
            context_used = True
        return _resolved("period", [{"tool": "summarize_period", "arguments": {"period": periods[0]}}],
                         context_used=context_used)
    if re.search(r"현재|지금|상태|기분|목말|안녕|current|status", compact) and not CARE.search(compact):
        return _resolved("current", [{"tool": "get_current_status", "arguments": {}}])
    if CARE.search(compact) and re.search(r"현재|지금|상태|current|status", compact):
        return QueryPlan(note="현재 센서와 관리 지식이 함께 필요한 질문입니다.")
    if CARE.search(compact):
        return _resolved("guide", [{"tool": "consult_basil_guide", "arguments": {"query": question[:500]}}])
    return QueryPlan()
