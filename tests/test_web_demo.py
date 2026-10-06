"""Keep the public preview independent from credentials and live data clients."""
import ast
import json
from pathlib import Path
import re
from unittest.mock import Mock

import pytest
import streamlit as st
from google import genai
from streamlit.testing.v1 import AppTest

from plant_ai import data
from scripts import build_web_demo as builder

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "demo_app.py"
DEMO = ROOT / "demo" / "index.html"
NOW = "2026-10-06T12:00:00+09:00"


@pytest.fixture(autouse=True)
def no_external_ai_or_sheet_calls(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-configured-key-never-use")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-second-key-never-use")
    monkeypatch.setenv("SHEET_ID", "fake-sheet-never-contact")
    client = Mock(side_effect=AssertionError("Preview attempted an AI call"))
    connection = Mock(side_effect=AssertionError("Preview attempted a Sheets call"))
    monkeypatch.setattr(genai, "Client", client)
    monkeypatch.setattr(data, "urlopen", connection)
    yield
    client.assert_not_called()
    connection.assert_not_called()


def parse_payload(html):
    return json.loads(re.search(r'<script id="plant-payload" type="application/json">(.*?)</script>', html, re.S).group(1))


def test_no_implicit_source_or_synthetic_fallback(capsys):
    with pytest.raises(ValueError, match="하나"):
        builder.build_payload()
    with pytest.raises(ValueError, match="하나"):
        builder.build_payload(empty=True, synthetic=True)
    with pytest.raises(SystemExit) as error:
        builder.main([])
    assert error.value.code == 2
    assert "required" in capsys.readouterr().err


def test_empty_mode_never_generates_readings(monkeypatch):
    generator = Mock(side_effect=AssertionError("Empty preview generated readings"))
    monkeypatch.setattr(builder, "make_demo_frame", generator)
    payload = builder.build_payload(empty=True, now=NOW)
    assert payload["provenance"]["kind"] == "none"
    assert payload["overview"]["record_count"] == 0
    assert payload["metrics"] == payload["charts"] == payload["spotlights"] == []
    assert len(payload["missions"]) == 3
    generator.assert_not_called()


def test_explicit_synthetic_period_is_fixed_but_export_clock_is_real():
    payload = builder.build_payload(synthetic=True, now=NOW)
    assert payload["provenance"]["kind"] == "synthetic"
    assert "가상 시연 기간" in payload["provenance"]["source_name"]
    assert "합성" in payload["provenance"]["label"]
    assert "실제 식물에서 측정" in payload["provenance"]["notice"]
    assert payload["provenance"]["last_record_at"] == builder.SYNTHETIC_REFERENCE
    assert payload["overview"]["analysis_reference"]["date"] == "2025-02-14"
    assert payload["overview"]["record_count"] == 14 * 720
    assert payload["exported_at"] == NOW
    assert all("2025" in point["t"] for chart in payload["charts"] for point in chart["points"])


def test_actual_known_aliases_work_without_guessing_unknown_soil_channels(tmp_path, monkeypatch):
    csv = tmp_path / "known-and-unknown.csv"
    csv.write_text("시간,온도,습도,capa,grd,private_notes\n2025-02-01 10:00,20,50,700,800,PRIVATE_VALUE\n2025-02-01 10:02,22,55,710,810,PRIVATE_VALUE\n", encoding="utf-8")
    generator = Mock(side_effect=AssertionError("Actual CSV triggered synthetic data"))
    monkeypatch.setattr(builder, "make_demo_frame", generator)
    payload = builder.build_payload(csv_path=csv, source_name="사용자 제공 기록", now=NOW)
    assert payload["provenance"]["kind"] == "actual"
    assert payload["provenance"]["source_name"] == "사용자 제공 기록"
    assert payload["provenance"]["last_record_at"].startswith("2025-02-01T10:02")
    assert payload["exported_at"] == NOW
    metrics = {item["id"]: item for item in payload["metrics"]}
    assert metrics["temperature"]["value"] == 22
    assert metrics["humidity"]["value"] == 55
    assert metrics["soil_moisture"]["value"] is None
    assert metrics["soil_moisture"]["unit"] == "원시값"
    assert "PRIVATE_VALUE" not in json.dumps(payload, ensure_ascii=False)
    generator.assert_not_called()


def test_cli_writes_self_contained_empty_preview_without_credentials(tmp_path, capsys):
    output = tmp_path / "preview.html"
    assert builder.main(["--empty", "--output", str(output)]) == 0
    html = output.read_text(encoding="utf-8")
    assert parse_payload(html)["provenance"]["kind"] == "none"
    assert "__PLANT_PAYLOAD__" not in html
    assert "fake-configured-key" not in html
    assert "fake-sheet" not in html
    assert json.loads(capsys.readouterr().out)["record_count"] == 0


def test_runtime_entry_imports_only_stdlib_streamlit_and_does_not_read_secrets():
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module.split(".")[0])
        if isinstance(node, ast.Attribute):
            assert node.attr != "secrets"
    assert imports <= {"pathlib", "streamlit"}


@pytest.mark.parametrize("configured", [False, True])
def test_streamlit_preview_ignores_keys_and_renders_local_iframe(monkeypatch, configured):
    app = AppTest.from_file(str(APP), default_timeout=20)
    if configured:
        app.secrets = {"GOOGLE_API_KEY": "fake-secret-key-never-use", "SHEET_ID": "fake-secret-sheet"}
    else:
        for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "SHEET_ID", "SHEET_GID"):
            monkeypatch.delenv(name, raising=False)
        app.secrets = {"TEST_ISOLATION": True}
    app.run()
    assert not app.exception
    assert "실시간 센서에 연결하거나 AI가 새 답변을 생성하지 않습니다" in app.info[0].value
    frames = app.get("iframe")
    assert len(frames) == 1
    assert frames[0].proto.scrolling is True
    assert parse_payload(frames[0].proto.srcdoc)["schema_version"] == "1.0"
    assert "fake-secret" not in frames[0].proto.srcdoc
    assert len(app.get("download_button")) == 1
    assert app.get("link_button")[0].proto.url == "https://chatty-plant.streamlit.app/"


def test_missing_preview_is_friendly_and_needs_no_secrets(monkeypatch):
    read_text = Path.read_text
    def missing(path, *args, **kwargs):
        if path == DEMO:
            raise FileNotFoundError("private path should never be shown")
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", missing)
    app = AppTest.from_file(str(APP), default_timeout=20)
    app.secrets = {"TEST_ISOLATION": True}
    app.run()
    assert not app.exception
    assert "미리보기 자료를 아직 준비" in app.warning[0].value
    assert not app.get("iframe")
    assert "private path" not in app.warning[0].value

def test_compact_preview_preserves_all_payload_values_and_executable_text():
    payload = builder.build_payload(empty=True, now=NOW)
    payload["provenance"]["source_name"] = '문자열의  두 칸과 </script><script> & 기호는 보존'
    original = builder.render_html(payload)
    compact = builder.render_demo_html(payload)
    pattern = builder._PAYLOAD_SCRIPT
    before, after = pattern.search(original), pattern.search(compact)
    assert json.loads(before.group(2)) == json.loads(after.group(2)) == payload
    assert original[:before.start(2)] == compact[:after.start(2)]
    assert original[before.end(2):] == compact[after.end(2):]
    assert len(compact.encode("utf-8")) < len(original.encode("utf-8"))
    assert "문자열의  두 칸" in compact
    assert '</script><script> &' not in after.group(2)
    assert builder.compact_demo_html(compact) == compact


def test_compacting_existing_page_preserves_record_and_export_dates():
    original = DEMO.read_text(encoding="utf-8")
    before = parse_payload(original)
    after = parse_payload(builder.compact_demo_html(original))
    assert after == before
    assert [len(c["points"]) for c in after["charts"]] == [len(c["points"]) for c in before["charts"]]
    assert after["exported_at"] == before["exported_at"]
    assert after["provenance"] == before["provenance"]