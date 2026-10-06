"""Check data provenance, bounded offline exports, and independent package integrity."""
import json
from pathlib import Path
import zipfile
import pandas as pd
import pytest
from plant_ai.exhibition import payload_from_frame, render_html, write_bundle, finalize_bundle, normalize_local_timestamps
from scripts.verify_exhibition import verify

NOW = "2026-10-04T12:00:00+09:00"

def frame():
    return pd.DataFrame({"Timestamp":["2025-02-01 10:00", "2025-02-01 10:02",
                                      "2025-02-05 10:00", "2025-02-05 10:02"],
                         "Temperature":[20,22,27,29], "Humidity":[50,52,60,62],
                         "Soil Moisture":[600,590,500,490], "private_notes":["do-not-export"]*4})

def actual():
    return payload_from_frame(frame(),kind="actual",source_name="records.csv",now=NOW)

def test_actual_export_preserves_record_dates_and_never_claims_live():
    payload, raw = actual()
    assert payload["provenance"]["kind"] == "actual"
    assert payload["provenance"]["last_record_at"].startswith("2025-02-05")
    assert payload["exported_at"].startswith("2026-10-04")
    assert payload["overview"]["analysis_reference"]["date"] == "2025-02-05"
    assert payload["metrics"][0]["value"] == 29
    assert "실시간 상태가 아닙니다" in payload["provenance"]["notice"]
    assert b"private_notes" not in raw and b"do-not-export" not in raw
    assert payload["guided_qa"]

def test_empty_draft_contains_no_invented_sensor_data():
    payload, raw = payload_from_frame(now=NOW)
    assert raw is None
    assert payload["provenance"]["kind"] == "none"
    assert payload["overview"]["record_count"] == 0
    assert payload["charts"] == payload["metrics"] == payload["spotlights"] == []
    assert len(payload["missions"]) == 3

@pytest.mark.parametrize("kind,values", [("actual",None),("synthetic",None),("none",frame()),("unknown",None)])
def test_source_and_data_must_agree(kind,values):
    with pytest.raises(ValueError):
        payload_from_frame(values,kind=kind,now=NOW)

def test_explicit_synthetic_input_is_prominently_labelled():
    payload,_=payload_from_frame(frame(),kind="synthetic",now=NOW)
    assert "합성" in payload["provenance"]["label"]
    assert payload["provenance"]["kind"] == "synthetic"

def test_normalization_retains_only_known_sensor_columns():
    payload,raw=actual()
    normalized=pd.read_csv(__import__("io").BytesIO(raw))
    assert list(normalized.columns) == ["Timestamp","Temperature","Humidity","Soil Moisture"]
    assert len(normalized)==4

def test_korean_google_sheet_times_am_pm_and_invalid_hour():
    raw=pd.DataFrame({"time":["2023. 12. 4 오후 12:08:23","2023. 12. 5 오전 12:01:00","2023. 12. 5 오후 13:00:00"],
                      "온도":[19,20,21],"습도":[60,61,62]})
    normalized=normalize_local_timestamps(raw)
    assert normalized.time.iloc[0]=="2023-12-04T12:08:23"
    assert normalized.time.iloc[1]=="2023-12-05T00:01:00"
    assert normalized.time.iloc[2]==raw.time.iloc[2]
    payload,_=payload_from_frame(raw.iloc[:2],kind="actual",now=NOW)
    assert payload["metrics"][2]["value"] is None
    assert payload["overview"]["record_count"]==2

def test_invalid_dates_do_not_silently_become_demo():
    raw=frame().assign(Timestamp="invalid")
    with pytest.raises(ValueError,match="측정 시각"):
        payload_from_frame(raw,kind="actual",now=NOW)

def test_completely_non_numeric_readings_are_rejected():
    raw=frame().assign(Temperature="bad",Humidity="bad",**{"Soil Moisture":"bad"})
    with pytest.raises(ValueError,match="센서 수치"):
        payload_from_frame(raw,kind="actual",now=NOW)

def test_extrema_and_gaps_survive_payload():
    payload,_=actual()
    temp=payload["charts"][0]["points"]
    assert max(p["max"] for p in temp if p["max"] is not None)==29
    assert min(p["min"] for p in temp if p["min"] is not None)==20
    assert any(p["contains_gap"] for p in temp)
    assert all(len(chart["points"])<=240 for chart in payload["charts"])
    assert len({c["period"] for c in payload["spotlights"]})==len(payload["spotlights"])

def test_html_json_cannot_close_script_element():
    payload,_=payload_from_frame(now=NOW)
    payload["provenance"]["source_name"]='</script><script>alert("x")</script>&'
    html=render_html(payload)
    assert '</script><script>alert("x")</script>' not in html
    assert "\\u003c/script\\u003e" in html
    assert "__PLANT_PAYLOAD__" not in html

def test_single_html_has_no_external_script_or_style_dependencies():
    payload,_=actual()
    html=render_html(payload)
    assert "<script src=" not in html
    assert 'rel="stylesheet"' not in html
    assert "@import" not in html

def test_bundle_is_portable_and_contains_no_credentials(tmp_path):
    payload,raw=actual()
    out=tmp_path/"exhibition"
    archive=tmp_path/"exhibition.zip"
    write_bundle(out,payload,raw,zip_path=archive)
    assert verify(out)==[]
    assert (out/"serve_exhibit.py").is_file()
    with zipfile.ZipFile(archive) as zipped:
        names=zipped.namelist()
        assert "plant-exhibition/tablet.html" in names
        assert not any("secrets" in name or ".venv" in name or ".git" in name for name in names)
        assert b"do-not-export" not in zipped.read("plant-exhibition/source-records.csv")
    files=json.loads((out/"exhibit-files.json").read_text())["files"]
    assert "serve_exhibit.py" not in files
    assert "start-exhibit.cmd" not in files

def test_rebuild_empty_removes_old_record_and_old_pdf(tmp_path):
    payload,raw=actual()
    out=tmp_path/"exhibition"
    write_bundle(out,payload,raw)
    (out/"offline-guide.pdf").write_bytes(b"old pdf")
    draft,_=payload_from_frame(now=NOW)
    write_bundle(out,draft)
    assert not (out/"source-records.csv").exists()
    assert not (out/"offline-guide.pdf").exists()
    assert verify(out)==[]

def test_rejects_non_package_directory_without_mutating_it(tmp_path):
    (tmp_path/"index.html").write_text("keep")
    draft,_=payload_from_frame(now=NOW)
    with pytest.raises(ValueError,match="다른 파일"):
        write_bundle(tmp_path,draft)
    assert (tmp_path/"index.html").read_text()=="keep"

def test_csv_hash_mismatch_rejected_before_output_creation(tmp_path):
    payload,raw=actual()
    with pytest.raises(ValueError,match="일치"):
        write_bundle(tmp_path/"bad",payload,raw+b"changed")
    assert not (tmp_path/"bad").exists()

def test_hash_checker_detects_changes_and_missing_files(tmp_path):
    payload,raw=actual()
    write_bundle(tmp_path/"pack",payload,raw)
    (tmp_path/"pack"/"tablet.html").write_text("changed")
    (tmp_path/"pack"/"data.json").unlink()
    assert set(verify(tmp_path/"pack"))=={"tablet.html","data.json"}

def test_hash_checker_never_follows_manifest_parent_path(tmp_path):
    outside=tmp_path/"outside.txt"
    outside.write_text("private")
    pack=tmp_path/"pack"
    pack.mkdir()
    (pack/"package-manifest.json").write_text(json.dumps({"package":"talking-basil-exhibition","files":{"../outside.txt":"x"}}))
    assert "invalid_path" in verify(pack)

def test_finalize_adds_pdf_to_zip_and_serving_allowlist(tmp_path):
    payload,raw=actual()
    pack=tmp_path/"pack"
    write_bundle(pack,payload,raw)
    (pack/"offline-guide.pdf").write_bytes(b"%PDF-1.4")
    finalize_bundle(pack,zip_path=tmp_path/"pack.zip")
    assert verify(pack)==[]
    assert "offline-guide.pdf" in json.loads((pack/"exhibit-files.json").read_text())["files"]
    with zipfile.ZipFile(tmp_path/"pack.zip") as zipped:
        assert "plant-exhibition/offline-guide.pdf" in zipped.namelist()


@pytest.mark.parametrize("missing", ["tablet.html", "index.html", "data.json", "serve_exhibit.py"])
def test_finalize_refuses_missing_core_files(tmp_path, missing):
    payload, raw = actual()
    pack = tmp_path / "pack"
    write_bundle(pack, payload, raw)
    (pack / missing).unlink()
    with pytest.raises(ValueError, match="필수 전시 파일"):
        finalize_bundle(pack)


def test_verifier_rejects_empty_manifest(tmp_path):
    (tmp_path / "package-manifest.json").write_text(json.dumps({"package": "talking-basil-exhibition", "files": {}}))
    assert "missing_required_files" in verify(tmp_path)


def test_finalize_does_not_resign_changed_html(tmp_path):
    payload, raw = actual()
    pack = tmp_path / "pack"
    write_bundle(pack, payload, raw)
    (pack / "tablet.html").write_text("changed")
    with pytest.raises(ValueError, match="손상"):
        finalize_bundle(pack)


def test_finalize_rejects_inconsistent_source_label(tmp_path):
    payload, raw = actual()
    pack = tmp_path / "pack"
    write_bundle(pack, payload, raw)
    payload["provenance"]["kind"] = "none"
    (pack / "data.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="출처"):
        finalize_bundle(pack)
