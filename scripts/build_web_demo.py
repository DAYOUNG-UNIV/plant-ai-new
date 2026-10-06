"""Build one self-contained preview page from an explicitly selected source.

Examples:
    python scripts/build_web_demo.py --empty
    python scripts/build_web_demo.py --synthetic
    python scripts/build_web_demo.py --csv sensor-records.csv

Only --synthetic creates example readings. --csv uses the existing known column
aliases; unknown channels are never relabelled as soil or another sensor.
This build tool reuses the local exhibition calculation; no API is contacted.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plant_ai.data import MAX_CSV_BYTES, load_csv_bytes, make_demo_frame
from plant_ai.exhibition import payload_from_frame, render_html

SYNTHETIC_REFERENCE = "2025-02-14T12:00:00+09:00"
SYNTHETIC_DAYS = 14


def build_payload(*, empty=False, synthetic=False, csv_path=None, source_name="", now=None):
    """Require exactly one explicit mode; preserve the actual export clock."""
    if not isinstance(empty, bool) or not isinstance(synthetic, bool):
        raise ValueError("empty와 synthetic은 참 또는 거짓이어야 합니다.")
    if sum((empty, synthetic, csv_path is not None)) != 1:
        raise ValueError("--empty, --synthetic, --csv 중 하나를 명시해 주세요.")
    if empty:
        payload, _ = payload_from_frame(kind="none", now=now)
    elif synthetic:
        frame = make_demo_frame(now=SYNTHETIC_REFERENCE, days=SYNTHETIC_DAYS)
        payload, _ = payload_from_frame(
            frame, kind="synthetic", now=now,
            source_name="가상 시연 기간 (합성 예시)",
        )
        start = payload["overview"]["period_start"][:10]
        end = payload["overview"]["period_end"][:10]
        payload["provenance"]["source_name"] = f"가상 시연 기간 · {start}~{end} (합성 예시)"
        payload["provenance"]["notice"] = (
            "고정된 가상 시연 기간의 합성 데이터입니다. 실제 식물에서 측정하거나 "
            "오늘 수집한 기록이 아닙니다. 실제 측정 및 연구 결과로 사용할 수 없습니다."
        )
    else:
        path = Path(csv_path)
        if path.stat().st_size > MAX_CSV_BYTES:
            raise ValueError("CSV 파일은 25MB 이하로 준비해 주세요.")
        frame = load_csv_bytes(path.read_bytes())
        payload, _ = payload_from_frame(
            frame, kind="actual", source_name=source_name or path.name, now=now,
        )
    return payload



_PAYLOAD_SCRIPT = re.compile(
    r'(<script id="plant-payload" type="application/json">)(.*?)(</script>)', re.S,
)


def compact_demo_html(html):
    """Remove only JSON separator whitespace; leave markup and JS untouched.

    This also accepts an existing generated page, so publishing a smaller copy
    never requires reloading its CSV or changing its original export timestamp.
    """
    matches = list(_PAYLOAD_SCRIPT.finditer(html))
    if len(matches) != 1:
        raise ValueError("미리보기 데이터 삽입 위치를 확인해 주세요.")
    match = matches[0]
    payload = json.loads(match.group(2))
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    # Keep the same script-element escaping contract as exhibition.render_html.
    encoded = encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return html[:match.start(2)] + encoded + html[match.end(2):]


def render_demo_html(payload):
    """Use the common exhibition page with smaller, human-readable JSON data."""
    return compact_demo_html(render_html(payload))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--empty", action="store_true", help="No sensor data; show local observation missions.")
    mode.add_argument("--synthetic", action="store_true", help="Explicitly create labelled readings for a fixed fictional period.")
    mode.add_argument("--csv", type=Path, help="Use a supplied actual-record CSV without guessing unknown columns.")
    parser.add_argument("--source-name", default="", help="Display name for the supplied actual-record CSV.")
    parser.add_argument("--output", type=Path, default=ROOT / "demo" / "index.html")
    args = parser.parse_args(argv)
    try:
        payload = build_payload(empty=args.empty, synthetic=args.synthetic,
                                csv_path=args.csv, source_name=args.source_name)
        if args.output.suffix.lower() != ".html":
            raise ValueError("출력 파일 이름은 .html로 끝나야 합니다.")
        if args.output.is_symlink():
            raise ValueError("심볼릭 링크를 미리보기 출력 파일로 사용할 수 없습니다.")
        html = render_demo_html(payload)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(html, encoding="utf-8")
        print(json.dumps({"output": str(args.output), "kind": payload["provenance"]["kind"],
                          "record_count": payload["overview"]["record_count"],
                          "last_record_at": payload["provenance"]["last_record_at"]}, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as error:
        print("미리보기 생성 실패: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())