"""Verify a local exhibition package. Uses only Python's standard library."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re

REQUIRED_FILES = {"index.html", "tablet.html", "data.json", "serve_exhibit.py",
                  "start-exhibit.cmd", "README.txt", "manifest.json", "sw.js",
                  "icon.svg", "verify-package.py", "exhibit-files.json"}
ALLOWED_FILES = REQUIRED_FILES | {"source-records.csv", "offline-guide.pdf"}


def validate_data_files(root):
    """Ensure the source label and the optional canonical CSV agree."""
    try:
        data = json.loads((root / "data.json").read_text(encoding="utf-8"))
        provenance = data["provenance"]
        kind = provenance["kind"]
        count = data["overview"]["record_count"]
        csv_path = root / "source-records.csv"
        if kind not in {"actual", "synthetic", "none"}:
            return ["invalid_provenance"]
        if csv_path.is_symlink() or csv_path.resolve().parent != root:
            return ["invalid_path"]
        if kind == "none":
            if csv_path.exists() or count != 0 or any(data.get(key) for key in ("metrics", "charts", "spotlights")):
                return ["invalid_provenance"]
        elif (not isinstance(count, int) or isinstance(count, bool) or count <= 0 or
              not csv_path.is_file() or hashlib.sha256(csv_path.read_bytes()).hexdigest() != provenance.get("normalized_sha256")):
            return ["source-records.csv"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return ["data.json"]
    return []


def verify(directory):
    root = Path(directory).resolve()
    marker = root / "package-manifest.json"
    if marker.is_symlink() or marker.resolve().parent != root:
        return ["invalid_path"]
    try:
        manifest = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["package-manifest.json"]
    if (not isinstance(manifest, dict) or manifest.get("package") != "talking-basil-exhibition"
            or not isinstance(manifest.get("files"), dict)):
        return ["invalid_manifest"]
    failures = []
    tracked = manifest["files"]
    if not REQUIRED_FILES.issubset(tracked):
        failures.append("missing_required_files")
    for name, expected in tracked.items():
        if not isinstance(name, str) or name not in ALLOWED_FILES:
            failures.append("invalid_path")
            continue
        path = root / name
        if path.is_symlink() or path.resolve().parent != root:
            failures.append("invalid_path")
            continue
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            actual = None
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or actual != expected:
            failures.append(name)
    if not failures:
        failures.extend(validate_data_files(root))
        if (root / "source-records.csv").exists() and "source-records.csv" not in tracked:
            failures.append("untracked_source_records")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    failures = verify(args.directory)
    print(json.dumps({"ok": not failures, "failed_files": failures}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
