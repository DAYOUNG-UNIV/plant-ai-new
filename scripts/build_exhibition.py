"""Build an offline exhibition from a user-supplied sensor CSV."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plant_ai.data import load_csv_bytes, MAX_CSV_BYTES
from plant_ai.exhibition import payload_from_frame, write_bundle, finalize_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", type=Path, help="User-supplied sensor records; no automatic sample substitution.")
    source.add_argument("--empty", action="store_true", help="Create a clearly labelled data-not-connected draft.")
    source.add_argument("--finalize", action="store_true", help="Refresh checksums and ZIP after adding the PDF.")
    parser.add_argument("--kind", choices=["actual", "synthetic"], default="actual")
    parser.add_argument("--source-name", default="")
    parser.add_argument("--output", type=Path, default=ROOT/"artifacts/exhibition")
    parser.add_argument("--zip", type=Path, default=ROOT/"artifacts/plant-exhibition.zip")
    args = parser.parse_args()
    try:
        if args.finalize:
            if not (args.output/"package-manifest.json").is_file():
                raise ValueError("기존 전시 패키지를 찾을 수 없습니다.")
            manifest = finalize_bundle(args.output, zip_path=args.zip)
        else:
            frame = None
            if args.csv:
                if args.csv.stat().st_size > MAX_CSV_BYTES:
                    raise ValueError("CSV 파일은 25MB 이하로 준비해 주세요.")
                frame = load_csv_bytes(args.csv.read_bytes())
            payload, records = payload_from_frame(frame, kind=args.kind if args.csv else "none",
                source_name=args.source_name or (args.csv.name if args.csv else ""))
            manifest = write_bundle(args.output, payload, records, zip_path=args.zip)
        print(json.dumps({"output":str(args.output), "zip":str(args.zip), "files":len(manifest["files"])},ensure_ascii=False))
        return 0
    except (ValueError, OSError) as error:
        print("전시 패키지 생성 실패: " + str(error), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
