"""Audit query scope and tool execution, not the truth of generated prose."""
from __future__ import annotations

LIMITATIONS = (
    "질문 범위와 도구 실행·관측 유무를 확인한 결과입니다. "
    "생성된 모든 문장, 인과 설명, 식물 건강이나 관리 효과가 참임을 보증하지 않습니다."
)


def _result_status(result):
    if not isinstance(result, dict):
        return "failed"
    error = result.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if result.get("ok") is False:
        if code == "comparison_unavailable":
            statuses = [_result_status(result.get(key)) for key in ("period_a", "period_b")]
            return "empty" if "empty" in statuses and "failed" not in statuses else "failed"
        return "empty" if code == "no_data" else "failed"
    if "matches" in result:
        return "satisfied" if result["matches"] else "empty"
    if "values" in result:
        return "satisfied" if isinstance(result["values"], dict) and any(value is not None for value in result["values"].values()) else "empty"
    if "row_count" in result:
        if not result["row_count"]:
            return "empty"
        if "metrics" in result and not any(item.get("count", 0) for item in result["metrics"].values()):
            return "empty"
        return "satisfied"
    if "period_a" in result and "period_b" in result:
        statuses = [_result_status(result["period_a"]), _result_status(result["period_b"])]
        return "satisfied" if all(status == "satisfied" for status in statuses) else (
            "failed" if "failed" in statuses else "empty")
    return "failed"


def evidence_card(record):
    result = record.get("result") or {}
    result = result if isinstance(result, dict) else {}
    card = {
        "evidence_id": record.get("evidence_id"),
        "tool": record.get("tool"), "arguments": record.get("arguments", {}),
        "status": _result_status(result),
        "model_visible": record.get("model_visible"),
    }
    for key in ("source", "timezone", "timestamp", "start", "end", "end_exclusive", "row_count",
                "analysis_reference", "freshness", "units", "values", "metrics", "differences",
                "event_count", "truncated", "error", "coverage", "quality"):
        if key in result:
            card[key] = result[key]
    if "matches" in result:
        card["guide_items"] = [
            {key: match[key] for key in ("title", "source") if key in match}
            for match in result["matches"][:3] if isinstance(match, dict)
        ]
    if isinstance(result.get("days"), list):
        card["daily_count"] = len(result["days"])
        card["daily"] = [
            {key: item[key] for key in ("start", "end", "row_count", "metrics") if key in item}
            for item in result["days"][-7:]
        ]
        card["daily_truncated"] = len(result["days"]) > 7
    if "period_a" in result:
        card["periods"] = [{key: result[name][key] for key in ("period", "start", "end", "row_count", "metrics") if key in result[name]}
                           for name in ("period_a", "period_b") if isinstance(result.get(name), dict)]
    return card


def audit_evidence(plan, evidence):
    """Require exact planned tool+arguments, and distinguish absent/empty/failed.

    Records marked model_visible=False cannot support a model-generated answer:
    their inspection trace may be complete while the model received a budget
    error. Local records omit this flag and remain usable.
    """
    for index, record in enumerate(evidence, 1):
        record["evidence_id"] = f"E{index}"
    checks = []
    for required in plan.calls:
        matches = [record for record in evidence if record.get("tool") == required["tool"]
                   and record.get("arguments") == required["arguments"]]
        visible = [record for record in matches if record.get("model_visible") is not False]
        statuses = [_result_status(record.get("result")) for record in visible]
        if "satisfied" in statuses:
            status = "satisfied"
        elif "empty" in statuses:
            status = "empty"
        elif matches:
            status = "failed"
        else:
            status = "missing"
        checks.append({"tool": required["tool"], "arguments": required["arguments"], "status": status,
                       "evidence_ids": [record["evidence_id"] for record in matches]})
    if not plan.resolved:
        status = "unresolved"
    elif checks and all(check["status"] == "satisfied" for check in checks):
        status = "satisfied"
    else:
        statuses = [check["status"] for check in checks]
        status = next((value for value in ("missing", "failed", "empty") if value in statuses), "missing")
    return {"status": status, "scope": "query_scope_and_tool_execution",
            "plan": plan.to_dict(), "checks": checks,
            "cards": [evidence_card(record) for record in evidence[-24:]], "limitations": LIMITATIONS}
