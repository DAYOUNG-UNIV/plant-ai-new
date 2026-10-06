"""Read settings safely; only --live makes small external requests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plant_ai.agent import PlantTools, answer_question
from plant_ai.analytics import SensorAnalytics
from plant_ai.data import get_settings, fetch_sheet, DataLoadError
from plant_ai.knowledge import BasilKnowledge


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Use configured Sheet and Gemini key for a small live test.")
    args = parser.parse_args()
    path = ROOT / ".streamlit" / "secrets.toml"
    try:
        secrets = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    except (OSError, tomllib.TOMLDecodeError):
        print(json.dumps({"ok": False, "error": "설정 파일의 TOML 형식을 확인해 주세요."}, ensure_ascii=False))
        return 1
    settings = get_settings(secrets)
    report = {"api_key_configured": bool(settings.api_key), "sheet_configured": bool(settings.sheet_id),
              "model": settings.model, "live_requested": args.live}
    if not args.live:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if not settings.api_key or not settings.sheet_id:
        report.update(ok=False, error="로컬 secrets.toml에 키와 시트 ID가 모두 필요합니다.")
    else:
        try:
            data = fetch_sheet(settings.sheet_id, settings.sheet_gid)
            analytics = SensorAnalytics(data, reference_mode="latest")
            state = analytics.current_status()
            if not state["ok"]:
                report.update(ok=False, error="시트는 읽었지만 유효한 센서 기록이 없습니다.")
            else:
                tools = PlantTools(analytics, BasilKnowledge(ROOT / "basil_knowledge.json"))
                reply = answer_question("분석 기준일의 평균 온도를 도구로 확인하고 한 문장으로 알려줘.",
                                        tools, api_key=settings.api_key, model=settings.model)
                report.update(ok=reply.mode == "gemini", rows=analytics.metadata()["row_count"],
                              answer_mode=reply.mode, error_code=reply.error_code,
                              model_calls=reply.model_calls, elapsed_ms=reply.elapsed_ms,
                              tool_names=[x["tool"] for x in reply.evidence])
        except DataLoadError as error:
            report.update(ok=False, error=str(error))
        except Exception:
            report.update(ok=False, error="연결 확인을 완료하지 못했습니다. 로컬 설정과 의존성을 확인해 주세요.")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
