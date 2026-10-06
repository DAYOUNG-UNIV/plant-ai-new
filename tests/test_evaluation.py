"""Check that the evaluation can fail for incorrect evidence, not just run."""
import copy
from plant_ai.agent import Answer, answer_question
from scripts.evaluate_agent import fixture_tools, evaluate_case, run_suite

def case():
    return {"id":"test", "category":"period", "question":"오늘 평균 온도",
            "expected_status":"satisfied", "expected_tool":"summarize_period",
            "expected_arguments":{"period":"today"},
            "checks":{"metrics.temperature.mean":31.0, "row_count":2}}

def test_curated_suite_runs_without_model():
    report = run_suite()
    assert report["total"] == 30
    assert report["passed"] == 30, [r for r in report["results"] if not r["passed"]]
    assert report["model_calls"] == 0
    assert report["live_model_evaluated"] is False
    assert report["natural_language_factuality_evaluated"] is False

def test_grader_rejects_wrong_numeric_ground_truth():
    item = case()
    answer = answer_question(item["question"], fixture_tools())
    item["checks"]["metrics.temperature.mean"] = 900
    assert "metrics.temperature.mean" in evaluate_case(item,answer)["failures"]

def test_grader_rejects_correct_number_from_wrong_period():
    item = case()
    answer = answer_question(item["question"], fixture_tools())
    wrong = copy.deepcopy(answer)
    for record in wrong.evidence:
        if record["tool"] == "summarize_period":
            record["arguments"] = {"period":"yesterday"}
    assert "tool_or_arguments" in evaluate_case(item,wrong)["failures"]

def test_grader_rejects_audit_label_without_execution():
    answer = Answer("31도", "local", audit={"status":"satisfied"})
    assert "tool_or_arguments" in evaluate_case(case(),answer)["failures"]
