"""Offline workflow evaluation on hand-checked synthetic records.

This is neither a live Gemini benchmark nor a natural-language factuality score.
No credentials are read and no network client is created.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from plant_ai.agent import PlantTools, answer_question
from plant_ai.analytics import SensorAnalytics
from plant_ai.knowledge import BasilKnowledge


def fixture_tools():
    times = ["2026-08-31 09:00", "2026-09-01 09:00", "2026-09-01 09:02",
             "2026-09-07 09:00", "2026-09-07 09:02", "2026-09-13 09:00",
             "2026-09-13 09:02", "2026-09-14 09:00", "2026-09-14 09:02"]
    frame = pd.DataFrame({"Timestamp": times, "Temperature": [20,21,23,24,26,27,29,30,32],
                          "Humidity": [40,41,43,44,46,47,49,50,52],
                          "Soil Moisture": [700,650,640,600,580,560,550,500,490]})
    analytics = SensorAnalytics(frame, now="2026-10-04 12:00:00+09:00",
                                source="demo", reference_mode="latest")
    return PlantTools(analytics, BasilKnowledge(ROOT / "basil_knowledge.json"))


def _path(result, path):
    for part in path.split("."):
        result = result[part]
    return result


def evaluate_case(case, answer):
    failures = []
    if answer.audit.get("status") != case["expected_status"]:
        failures.append("audit_status")
    if answer.model_calls != 0 or answer.mode != "local":
        failures.append("unexpected_model_call")
    if case.get("expected_tool"):
        matches = [e for e in answer.evidence if e.get("tool") == case["expected_tool"]
                   and e.get("arguments") == case["expected_arguments"]]
        if not matches:
            failures.append("tool_or_arguments")
        else:
            result = matches[-1]["result"]
            for path, expected in case.get("checks", {}).items():
                try:
                    actual = _path(result, path)
                    equal = (math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
                             if isinstance(expected, (int,float)) and not isinstance(expected, bool)
                             else actual == expected)
                    if not equal:
                        failures.append(path)
                except (KeyError, TypeError, ValueError):
                    failures.append(path)
            if result.get("source") != "demo":
                failures.append("source")
            if result.get("analysis_reference", {}).get("date") != "2026-09-14":
                failures.append("reference_date")
            if result.get("freshness", {}).get("stale") is not True:
                failures.append("stale_data")
    return {"id": case["id"], "category": case["category"], "passed": not failures,
            "failures": failures, "audit_status": answer.audit.get("status"),
            "model_calls": answer.model_calls, "elapsed_ms": answer.elapsed_ms,
            "tool_count": len(answer.evidence)}


def run_suite(path=None):
    suite = json.loads(Path(path or ROOT / "evals/scenarios.json").read_text(encoding="utf-8"))
    tools = fixture_tools()
    results = [evaluate_case(case, answer_question(case["question"], tools,
               history=case.get("history"))) for case in suite["cases"]]
    categories = {}
    for result in results:
        counter = categories.setdefault(result["category"], {"passed": 0, "total": 0})
        counter["total"] += 1
        counter["passed"] += int(result["passed"])
    return {"schema_version": "1.0", "evaluation": "offline_query_workflow",
            "data": suite["data"], "live_model_evaluated": False,
            "natural_language_factuality_evaluated": False,
            "passed": sum(r["passed"] for r in results), "total": len(results),
            "median_elapsed_ms": statistics.median(r["elapsed_ms"] for r in results),
            "model_calls": sum(r["model_calls"] for r in results),
            "categories": categories, "results": results,
            "limitations": "Hand-authored regression scenarios on synthetic data; not an independent held-out benchmark, Gemini quality score, or user study."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_suite()
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
