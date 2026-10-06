"""Reproducible synthetic comparison of legacy full scans and indexed queries.

Run from the repository root:
    python scripts/benchmark_queries.py
    python scripts/benchmark_queries.py --output artifacts/benchmark.json

The same 24 date windows and all three sensors' count/mean/min/max are compared.
Data generation and engine construction are excluded from query timings.
This benchmark never reads a live sheet or claims production performance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Callable

# Permit direct invocation from any working directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from plant_ai.analytics import SensorAnalytics


SEED = 20261001
CUTOFF = "2026-10-01T00:00:00+09:00"
METRICS = {
    "temperature": "Temperature",
    "humidity": "Humidity",
    "soil_moisture": "Soil Moisture",
}
STATISTICS = ("count", "mean", "min", "max")
RTOL = 1e-9
ATOL = 1e-8


def generate_data(rows: int = 200_000, seed: int = SEED) -> pd.DataFrame:
    """Generate one reproducible, clean sensor history at a two-minute cadence."""
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
        raise ValueError("rows must be a positive integer")
    rng = np.random.default_rng(seed)
    indices = np.arange(rows, dtype=float)
    day_cycle = np.sin(2 * np.pi * indices / 720)
    times = pd.date_range(
        end=pd.Timestamp(CUTOFF) - pd.DateOffset(minutes=2),
        periods=rows,
        freq="2min",
    ).tz_convert("Asia/Seoul")
    return pd.DataFrame({
        "Timestamp": times,
        "Temperature": 23.0 + 4.0 * day_cycle + rng.normal(0, 0.4, rows),
        "Humidity": np.clip(60.0 - 8.0 * day_cycle + rng.normal(0, 1.0, rows), 0, 100),
        "Soil Moisture": 520.0 + 40.0 * np.sin(indices / 6000) + rng.normal(0, 2.0, rows),
    })


def query_windows() -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """24 midnight-aligned, half-open windows that fit the default history."""
    cutoff = pd.Timestamp(CUTOFF)
    return [
        (cutoff - pd.DateOffset(days=offset + horizon), cutoff - pd.DateOffset(days=offset))
        for offset in (0, 7, 30, 60, 90, 150)
        for horizon in (1, 7, 30, 90)
    ]


def legacy_full_scan(
    frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> dict:
    """Baseline: regenerate dt.date and scan the full Timestamp column per query."""
    dates = frame["Timestamp"].dt.date
    selected = frame.loc[(dates >= start.date()) & (dates < end.date())]
    metrics = {}
    for key, column in METRICS.items():
        values = selected[column]
        count = int(values.count())
        metrics[key] = {
            "count": count,
            "mean": float(values.mean()) if count else None,
            "min": float(values.min()) if count else None,
            "max": float(values.max()) if count else None,
        }
    return {"row_count": int(len(selected)), "metrics": metrics}


def verify_equivalence(expected: list[dict], actual: list[dict]) -> None:
    """Raise on any count/mean/min/max mismatch, including null or empty windows."""
    if len(expected) != len(actual):
        raise AssertionError("Query result counts differ")
    for query_index, (old, new) in enumerate(zip(expected, actual)):
        if old["row_count"] != new["row_count"]:
            raise AssertionError(f"Query {query_index}: row count mismatch")
        for metric in METRICS:
            for statistic in STATISTICS:
                a = old["metrics"][metric][statistic]
                b = new["metrics"][metric][statistic]
                label = f"query {query_index}, {metric}, {statistic}"
                if statistic == "count":
                    if a != b:
                        raise AssertionError(f"{label}: {a} != {b}")
                elif a is None or b is None:
                    if a is not None or b is not None:
                        raise AssertionError(f"{label}: null mismatch ({a}, {b})")
                elif not np.isclose(a, b, rtol=RTOL, atol=ATOL):
                    raise AssertionError(f"{label}: {a} != {b}")


def _measure(function: Callable[[], list[dict]]) -> tuple[float, list[dict]]:
    started = time.perf_counter_ns()
    result = function()
    return (time.perf_counter_ns() - started) / 1_000_000, result


def run_benchmark(rows: int = 200_000, repeats: int = 3) -> dict:
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
        raise ValueError("repeats must be a positive integer")
    frame = generate_data(rows)
    windows = query_windows()
    started = time.perf_counter_ns()
    engine = SensorAnalytics(
        frame, now=CUTOFF, source="synthetic", reference_mode="calendar"
    )
    build_ms = (time.perf_counter_ns() - started) / 1_000_000

    def old_queries():
        return [legacy_full_scan(frame, start, end) for start, end in windows]

    def new_queries():
        # Include the new API's metadata/freshness/evidence overhead in timing.
        return [engine.summarize_range(start, end) for start, end in windows]

    # Untimed correctness pass also warms both paths in the same process.
    verify_equivalence(old_queries(), new_queries())
    old_times, new_times = [], []
    for iteration in range(repeats):
        # Alternate execution order to reduce consistent first-run bias.
        if iteration % 2 == 0:
            old_ms, old_results = _measure(old_queries)
            new_ms, new_results = _measure(new_queries)
        else:
            new_ms, new_results = _measure(new_queries)
            old_ms, old_results = _measure(old_queries)
        # Verification is outside both timers; include every measured pass.
        verify_equivalence(old_results, new_results)
        old_times.append(old_ms)
        new_times.append(new_ms)

    old_median = statistics.median(old_times)
    new_median = statistics.median(new_times)
    return {
        "source": "synthetic",
        "description": "Fixed synthetic sensor history; not measurements from the live plant or sheet.",
        "rows": rows,
        "seed": SEED,
        "cadence_seconds": 120,
        "cutoff_exclusive": CUTOFF,
        "timezone": "Asia/Seoul",
        "reference_mode": "calendar",
        "query_count": len(windows),
        "horizons_days": [1, 7, 30, 90],
        "end_offsets_days": [0, 7, 30, 60, 90, 150],
        "repeats": repeats,
        "build_ms": round(build_ms, 6),
        "timing_unit": "milliseconds for the complete 24-query set",
        "excluded_from_query_timing": ["synthetic data generation", "engine construction", "equivalence verification"],
        "old": {
            "method": "Timestamp.dt.date full-frame mask per query; all sensor count/mean/min/max",
            "median_ms": round(old_median, 6),
            "runs_ms": [round(value, 6) for value in old_times],
        },
        "new": {
            "method": "SensorAnalytics.summarize_range including common response metadata",
            "median_ms": round(new_median, 6),
            "runs_ms": [round(value, 6) for value in new_times],
        },
        "speedup": round(old_median / new_median, 3) if new_median else None,
        "equivalence_verified": True,
        "verified_statistics": ["row_count", "temperature/count/mean/min/max",
                                "humidity/count/mean/min/max", "soil_moisture/count/mean/min/max"],
        "tolerance": {"rtol": RTOL, "atol": ATOL},
        "versions": {"python": sys.version.split()[0], "pandas": pd.__version__, "numpy": np.__version__},
        "limitations": "Local synthetic benchmark. Clean, uniform-cadence data; no network, sheet download, model calls, or production concurrency.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=200_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, help="Optional JSON result path; relative to the current directory.")
    args = parser.parse_args(argv)
    if args.rows < 1 or args.repeats < 1:
        parser.error("--rows and --repeats must be positive integers")
    report = run_benchmark(rows=args.rows, repeats=args.repeats)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
