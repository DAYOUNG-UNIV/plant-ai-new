"""Checks for an honest, reproducible benchmark contract (not timing thresholds)."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from plant_ai.analytics import SensorAnalytics
from scripts.benchmark_queries import (
    CUTOFF, METRICS, SEED, generate_data, legacy_full_scan,
    main, query_windows, run_benchmark, verify_equivalence,
)


def test_synthetic_generation_is_reproducible_and_physically_valid():
    frame = generate_data(100)
    pd.testing.assert_frame_equal(frame, generate_data(100, seed=SEED))
    assert len(frame) == 100
    assert frame["Timestamp"].iloc[-1] == pd.Timestamp(CUTOFF) - pd.DateOffset(minutes=2)
    assert frame["Timestamp"].diff().dropna().dt.total_seconds().eq(120).all()
    assert frame["Humidity"].between(0, 100).all()
    assert frame["Temperature"].between(-50, 100).all()
    assert np.isfinite(frame[list(METRICS.values())].to_numpy()).all()


def test_twenty_four_half_open_windows_have_balanced_horizons():
    windows = query_windows()
    assert len(windows) == 24
    assert len(set(windows)) == 24
    spans = [(end - start).days for start, end in windows]
    for horizon in (1, 7, 30, 90):
        assert spans.count(horizon) == 6
    assert all(start.hour == end.hour == 0 for start, end in windows)
    assert all(end <= pd.Timestamp(CUTOFF) for _, end in windows)


def test_baseline_and_indexed_api_compare_all_statistics_including_empty_windows():
    frame = generate_data(1000)
    engine = SensorAnalytics(frame, now=CUTOFF, source="synthetic")
    windows = query_windows()
    old = [legacy_full_scan(frame, start, end) for start, end in windows]
    new = [engine.summarize_range(start, end) for start, end in windows]
    verify_equivalence(old, new)
    assert any(result["row_count"] == 0 for result in old)
    assert any(result["row_count"] > 0 for result in old)
    for result in old:
        assert set(result["metrics"]) == set(METRICS)
        for metric in METRICS:
            assert set(result["metrics"][metric]) == {"count", "mean", "min", "max"}


@pytest.mark.parametrize("statistic", ["count", "mean", "min", "max"])
def test_equivalence_check_detects_every_statistic_regression(statistic):
    frame = generate_data(1000)
    start, end = query_windows()[0]
    expected = [legacy_full_scan(frame, start, end)]
    changed = copy.deepcopy(expected)
    changed[0]["metrics"]["soil_moisture"][statistic] += 1
    with pytest.raises(AssertionError):
        verify_equivalence(expected, changed)


def test_equivalence_check_detects_row_counts_and_missing_values():
    frame = generate_data(1000)
    start, end = query_windows()[0]
    expected = [legacy_full_scan(frame, start, end)]
    changed = copy.deepcopy(expected)
    changed[0]["row_count"] += 1
    with pytest.raises(AssertionError):
        verify_equivalence(expected, changed)
    changed = copy.deepcopy(expected)
    changed[0]["metrics"]["humidity"]["mean"] = None
    with pytest.raises(AssertionError, match="null mismatch"):
        verify_equivalence(expected, changed)
    with pytest.raises(AssertionError):
        verify_equivalence(expected, [])


def test_report_labels_synthetic_data_and_separates_build_from_queries():
    report = run_benchmark(rows=1000, repeats=2)
    assert report["source"] == "synthetic"
    assert report["rows"] == 1000
    assert report["query_count"] == 24
    assert report["seed"] == SEED
    assert report["equivalence_verified"] is True
    assert report["build_ms"] >= 0
    assert report["repeats"] == 2
    assert len(report["old"]["runs_ms"]) == len(report["new"]["runs_ms"]) == 2
    assert report["old"]["median_ms"] > 0
    assert report["new"]["median_ms"] > 0
    assert report["speedup"] > 0  # No assertion that a machine must be faster.
    assert "engine construction" in report["excluded_from_query_timing"]
    json.dumps(report, allow_nan=False)


def test_cli_can_be_run_from_another_directory_and_writes_optional_json(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_queries.py"
    output = tmp_path / "results" / "benchmark.json"
    completed = subprocess.run(
        [sys.executable, str(script), "--rows", "500", "--repeats", "1", "--output", str(output)],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    printed = json.loads(completed.stdout)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert printed == saved
    assert saved["source"] == "synthetic"
    assert saved["equivalence_verified"] is True


@pytest.mark.parametrize("argv", [["--rows", "0"], ["--rows", "-10"], ["--repeats", "0"]])
def test_cli_rejects_invalid_benchmark_sizes(argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
