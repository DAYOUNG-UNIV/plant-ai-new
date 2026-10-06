"""Reproducible, offline exhibition payloads. No API calls or implicit demo data."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from pathlib import Path
import shutil
import zipfile

import pandas as pd

from .agent import PlantTools, local_answer
from .analytics import SensorAnalytics
from .inquiry import get_missions
from .knowledge import BasilKnowledge

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "1.0"
LABELS = {"temperature": "온도", "humidity": "공기 습도", "soil_moisture": "토양 센서"}
UNITS = {"temperature": "°C", "humidity": "%RH", "soil_moisture": "원시값"}
PACKAGE_FILES = {"index.html", "tablet.html", "data.json", "source-records.csv",
                 "serve_exhibit.py", "start-exhibit.cmd", "README.txt",
                 "manifest.json", "sw.js", "icon.svg", "offline-guide.pdf",
                 "exhibit-files.json", "package-manifest.json", "verify-package.py"}
HTTP_FILES = PACKAGE_FILES - {"serve_exhibit.py", "start-exhibit.cmd", "verify-package.py", "package-manifest.json"}
ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192"><rect width="192" height="192" rx="40" fill="#143e32"/><path d="M49 102C46 48 114 36 149 43c4 57-25 87-69 84l-17 25-12-8 19-30c20-28 34-38 60-51-35 9-55 23-81 39" fill="#b9db96"/></svg>'


def _text(value):
    return "측정 없음" if value is None else f"{value:,.1f}"


def _iso(value):
    return None if pd.isna(value) else pd.Timestamp(value).isoformat()


def normalize_local_timestamps(frame):
    """Recognize the explicit Korean Google Sheets timestamp format only."""
    result = frame.copy()
    pattern = re.compile(r"\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\s*(오전|오후)\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*")
    columns = [c for c in result.columns if re.sub(r"[\W_]+", "", str(c)).lower()
               in {"time", "timestamp", "datetime", "date", "시간", "측정시간", "기록시간", "일시", "날짜", "타임스탬프"}]
    if not columns:
        return result
    def convert(value):
        match = pattern.fullmatch(value) if isinstance(value, str) else None
        if match is None:
            return value
        year, month, day, ampm, hour, minute, second = match.groups()
        if not 1 <= int(hour) <= 12:
            return value
        hour = int(hour) % 12 + (12 if ampm == "오후" else 0)
        try:
            return datetime(int(year), int(month), int(day), hour, int(minute), int(second or 0)).isoformat()
        except ValueError:
            return value
    result[columns[0]] = result[columns[0]].map(convert)
    return result


def payload_from_frame(frame=None, *, kind="none", source_name="", now=None, guide_path=None):
    if kind not in {"actual", "synthetic", "none"}:
        raise ValueError("kind는 actual, synthetic, none 중 하나여야 합니다.")
    if (frame is None) != (kind == "none"):
        raise ValueError("실제·합성 자료에는 데이터가 필요하며 none에는 데이터를 넣을 수 없습니다.")
    exported = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="Asia/Seoul")
    exported = exported.tz_localize("Asia/Seoul") if exported.tzinfo is None else exported.tz_convert("Asia/Seoul")
    payload = {
        "schema_version": SCHEMA_VERSION, "exported_at": exported.isoformat(),
        "provenance": {"kind": kind,
            "label": {"actual": "사용자 제공 센서 기록", "synthetic": "합성 시연 자료", "none": "실제 기록 연결 전"}[kind],
            "source_name": str(source_name)[:200], "last_record_at": None,
            "notice": "저장된 과거 자료입니다. 현재 화분의 실시간 상태가 아닙니다." if kind != "none" else
                      "아직 실제 센서 기록을 연결하지 않았습니다. 기록 수치와 그래프는 비워 두었습니다."},
        "overview": {"record_count": 0, "period_start": None, "period_end": None},
        "metrics": [], "spotlights": [], "charts": [], "missions": get_missions(), "guided_qa": [],
    }
    knowledge = BasilKnowledge(guide_path or ROOT / "basil_knowledge.json")
    if frame is None:
        payload["guided_qa"] = [
            {"id": mission["id"], "question": mission["question"], "answer": mission["explanation"],
             "sources": ["프로젝트의 로컬 탐구 안내"]} for mission in get_missions()
        ]
        return payload, None
    engine = SensorAnalytics(normalize_local_timestamps(frame), now=exported, source="demo" if kind == "synthetic" else "upload",
                             reference_mode="latest")
    meta = engine.metadata()
    current = engine.current_status()
    if not current.get("ok") or not meta["row_count"]:
        raise ValueError("유효한 측정 시각이 없습니다. 날짜 형식과 센서 CSV를 확인해 주세요.")
    if not any(value is not None for value in current["values"].values()) and not any(
        engine._frame[column].notna().any() for column in engine.COLUMNS.values()
    ):
        raise ValueError("유효한 센서 수치가 없습니다. 열 이름과 측정 단위를 확인해 주세요.")
    payload["provenance"].update(last_record_at=meta["last_sample_at"])
    payload["overview"].update(record_count=meta["row_count"], period_start=meta["first_sample_at"],
        period_end=meta["last_sample_at"], quality=meta["quality"], analysis_reference=meta["analysis_reference"])
    for key in engine.METRICS:
        payload["metrics"].append({"id": key, "label": LABELS[key], "value": current["values"][key],
            "unit": UNITS[key], "detail": "마지막 측정 행의 값 · " + meta["last_sample_at"]})
    clean = engine._frame.reset_index().copy()
    clean["Timestamp"] = clean["Timestamp"].map(lambda stamp: stamp.isoformat())
    csv_bytes = clean.to_csv(index=False).encode("utf-8-sig")
    payload["provenance"]["normalized_sha256"] = hashlib.sha256(csv_bytes).hexdigest()
    first = pd.Timestamp(meta["first_sample_at"])
    last = pd.Timestamp(meta["last_sample_at"])
    dates = [(first.date().isoformat(), "기록의 시작"), (last.date().isoformat(), "마지막으로 기록한 날")]
    spread = engine._frame["Temperature"].resample("D").agg(["min", "max", "count"])
    spread = spread.loc[spread["count"] >= 2]
    if not spread.empty:
        maximum_day = (spread["max"] - spread["min"]).idxmax().date().isoformat()
        dates.insert(1, (maximum_day, "하루 온도 범위가 가장 컸던 날"))
    seen = set()
    for day, title in dates:
        if day in seen:
            continue
        seen.add(day)
        summary = engine.summarize_period(day)
        facts = [{"label": LABELS[key] + " 관측 평균", "value": summary["metrics"][key]["mean"], "unit": UNITS[key]}
                 for key in engine.METRICS]
        fraction = summary["coverage"]["observed_support_fraction"]
        payload["spotlights"].append({"id": day, "title": title, "period": day, "start": summary["start"],
            "end": summary["end"], "summary": f"이 날짜의 관측 {summary['row_count']:,}개. 짧은 측정 간격이 뒷받침하는 시간 비율 {fraction:.1%}. 원인이나 식물 건강을 진단한 결과가 아닙니다.",
            "facts": facts})
    all_days = (last.normalize() - first.normalize()).days + 1
    chart_days = min(all_days, 3660)
    envelope = engine.chart_envelope(days=chart_days, max_bins=min(240, max(1, meta["row_count"])))
    payload["overview"].update(chart_days=chart_days, chart_scope="전체 기록" if all_days <= 3660 else "마지막 기록일부터 최근 3660일")
    for key in engine.METRICS:
        points = []
        for row in envelope.loc[envelope["metric"] == key].to_dict("records"):
            points.append({"t": _iso(row["Timestamp"]),
                **{name: None if pd.isna(row[name]) else float(row[name]) for name in ("mean", "min", "max")},
                "min_at": _iso(row["min_at"]), "max_at": _iso(row["max_at"]), "count": int(row["count"]),
                "break_before": bool(row["break_before"]), "contains_gap": bool(row["contains_gap"] or row["contains_missing"])})
        payload["charts"].append({"id": key, "label": LABELS[key], "unit": UNITS[key], "points": points})
    tools = PlantTools(engine, knowledge)
    questions = ["마지막 측정 상태는 어때?", "오늘 평균 온도", "지난주와 이번 주 비교",
                 "최근 14일 추세", "최근 14일 수집 공백",
                 "공기 습도와 보정하지 않은 토양 센서값을 같은 퍼센트로 읽어도 될까?",
                 "바질은 어떤 온도가 적정해?", "바질은 햇빛을 얼마나 좋아해?"]
    for index, question in enumerate(questions, 1):
        answer = local_answer(question, tools)
        sources = []
        for evidence in answer.evidence:
            if evidence["tool"] == "consult_basil_guide":
                sources.extend(match["source"] for match in evidence["result"].get("matches", []))
            else:
                sources.append("저장된 센서 기록 · " + evidence["tool"])
        payload["guided_qa"].append({"id": f"q{index}", "question": question,
            "answer": "기준일: " + last.date().isoformat() + " (마지막 기록일)\n\n" + answer.text,
            "sources": list(dict.fromkeys(sources)) or ["프로젝트 기본 안내"]})
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return payload, csv_bytes


def render_html(payload):
    template = (ROOT / "plant_ai/offline_template.html").read_text(encoding="utf-8")
    if template.count("__PLANT_PAYLOAD__") != 1:
        raise ValueError("오프라인 템플릿의 데이터 삽입 위치를 확인해 주세요.")
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return template.replace("__PLANT_PAYLOAD__", encoded)


def _output_path(directory, name):
    if name not in PACKAGE_FILES:
        raise ValueError("허용되지 않은 패키지 파일입니다.")
    path = directory / name
    if path.is_symlink() or path.resolve().parent != directory:
        raise ValueError("출력 경로가 전시 폴더 밖으로 나갑니다.")
    return path


def finalize_bundle(directory, *, zip_path=None, _rebuilding=False):
    from scripts.verify_exhibition import REQUIRED_FILES, validate_data_files, verify
    directory = Path(directory).resolve()
    required = REQUIRED_FILES - {"exhibit-files.json"}
    if any(not _output_path(directory, name).is_file() for name in required):
        raise ValueError("필수 전시 파일이 없습니다. 패키지를 다시 만들어 주세요.")
    if validate_data_files(directory):
        raise ValueError("출처 표시와 센서 기록이 일치하지 않습니다.")
    marker = _output_path(directory, "package-manifest.json")
    if marker.exists() and not _rebuilding and verify(directory):
        raise ValueError("기존 패키지 파일이 변경되거나 손상되었습니다. 원본 기록으로 다시 만들어 주세요.")
    files = sorted(name for name in PACKAGE_FILES - {"package-manifest.json", "exhibit-files.json"}
                   if _output_path(directory, name).is_file())
    manifest_path = _output_path(directory, "exhibit-files.json")
    manifest_path.write_text(json.dumps({"files": [name for name in files if name in HTTP_FILES]}, indent=2)+"\n", encoding="utf-8")
    files.append("exhibit-files.json")
    checksums = {name: hashlib.sha256(_output_path(directory, name).read_bytes()).hexdigest() for name in files}
    manifest = {"package": "talking-basil-exhibition", "schema_version": SCHEMA_VERSION,
                "files": checksums, "contains_api_keys": False}
    _output_path(directory, "package-manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    if zip_path:
        destination = Path(zip_path)
        if destination.resolve() == directory or destination.resolve().is_relative_to(directory):
            raise ValueError("ZIP은 전시 폴더 밖에 저장해 주세요.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(files + ["package-manifest.json"]):
                archive.write(_output_path(directory, name), arcname="plant-exhibition/"+name)
    return manifest


def write_bundle(directory, payload, csv_bytes=None, *, zip_path=None):
    kind = payload.get("provenance", {}).get("kind")
    if kind not in {"actual", "synthetic", "none"}:
        raise ValueError("자료 출처 유형을 확인해 주세요.")
    if (csv_bytes is None) != (kind == "none"):
        raise ValueError("출처 표시와 센서 기록 파일이 일치하지 않습니다.")
    if csv_bytes is not None and hashlib.sha256(csv_bytes).hexdigest() != payload["provenance"].get("normalized_sha256"):
        raise ValueError("열람본과 정제된 센서 기록이 일치하지 않습니다.")
    requested = Path(directory)
    if requested.is_symlink():
        raise ValueError("심볼릭 링크를 출력 폴더로 사용할 수 없습니다.")
    directory = requested.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        marker = _output_path(directory, "package-manifest.json")
        if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("package") != "talking-basil-exhibition":
            raise ValueError("출력 폴더에 다른 파일이 있습니다. 새 전시 폴더를 지정해 주세요.")
    # Rebuild only our known artifacts; never copy the project or its secrets.
    for obsolete in ("offline-guide.pdf", "source-records.csv"):
        path = _output_path(directory, obsolete)
        if path.exists():
            path.unlink()
    tablet = render_html(payload)
    assets = {"tablet.html": tablet, "data.json": json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
              "icon.svg": ICON, "manifest.json": json.dumps({"name": "식물의 시간을 읽다", "short_name": "식물의 시간",
                  "start_url": "./index.html", "scope": "./", "display": "standalone", "background_color": "#f5f5ef",
                  "theme_color": "#143e32", "icons": [{"src": "./icon.svg", "sizes": "any", "type": "image/svg+xml"}]}, ensure_ascii=False)}
    cache_id = hashlib.sha256(assets["data.json"].encode("utf-8")).hexdigest()[:16]
    assets["sw.js"] = f"""const PREFIX='plant-exhibit-'+encodeURIComponent(self.registration.scope)+'-';
const CACHE=PREFIX+'{cache_id}';
const FILES=['./','./index.html','./tablet.html','./manifest.json','./icon.svg'];
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(FILES)).then(()=>self.skipWaiting())));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith(PREFIX)&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',e=>{{if(e.request.method==='GET'&&new URL(e.request.url).origin===self.location.origin)e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request)));}});
"""
    pwa = """<link rel="manifest" href="./manifest.json"><script>
if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1')) {
 navigator.serviceWorker.register('./sw.js').catch(()=>{});
}
</script>"""
    assets["index.html"] = tablet.replace("</head>", pwa + "</head>", 1)
    assets["README.txt"] = ("식물의 시간을 읽다 - 저장 기록 전시 패키지\n\n"
        + payload["provenance"]["label"] + "\n" + payload["provenance"]["notice"] + "\n"
        + "마지막 측정: " + str(payload["provenance"]["last_record_at"] or "기록 없음") + "\n\n"
        + "1. 태블릿·PC: tablet.html은 외부 자원 없는 단일 파일입니다. JavaScript를 실행하는 브라우저에서 열어 주세요.\n"
        + "2. iPad 파일 미리보기에서 버튼이 동작하지 않으면 offline-guide.pdf를 파일 앱에 저장해 읽으세요. PDF는 별도 생성 시 포함됩니다.\n"
        + "3. 전시 PC: Python이 설치되어 있으면 start-exhibit.cmd를 실행합니다. 같은 Wi-Fi 태블릿은 start-exhibit.cmd --lan 사용 후 표시된 주소에 접속합니다.\n"
        + "4. LAN 열람은 노트북과 Wi-Fi가 켜져 있어야 합니다. 인터넷 연결은 필요 없습니다.\n"
        + "5. 완전한 오프라인 웹앱 설치는 HTTPS 주소에서 미리 열고 캐시 설치 완료 후 비행기 모드 재실행을 확인해야 합니다. 이 패키지는 외부에 자동 게시되지 않습니다.\n"
        + "6. 질문 버튼은 미리 계산한 기본 안내입니다. 자유 생성형 AI 대화나 새로운 센서 수집은 제공하지 않습니다.\n"
        + "7. source-records.csv는 존재하는 경우 시각·온도·공기 습도·토양 원시값만 담은 정제 기록입니다.\n"
        + "8. verify-package.py로 패키지 파일의 SHA-256을 확인할 수 있습니다.\n")
    guide = ROOT / "docs/exhibition-guide.md"
    if guide.is_file():
        assets["README.txt"] += "\n\n" + guide.read_text(encoding="utf-8")
    for name, content in assets.items():
        _output_path(directory, name).write_text(content, encoding="utf-8")
    for source, name in [(ROOT/"scripts/serve_exhibit.py", "serve_exhibit.py"),
                         (ROOT/"start-exhibit.cmd", "start-exhibit.cmd"),
                         (ROOT/"scripts/verify_exhibition.py", "verify-package.py")]:
        if source.exists():
            shutil.copyfile(source, _output_path(directory, name))
    if csv_bytes is not None:
        _output_path(directory, "source-records.csv").write_bytes(csv_bytes)
    return finalize_bundle(directory, zip_path=zip_path, _rebuilding=True)
