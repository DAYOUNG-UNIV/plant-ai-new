"""Irregular sampling, bounded support, and gap-aware display contracts."""

import json

import numpy as np
import pandas as pd
import pytest

from plant_ai.analytics import SensorAnalytics


NOW = "2026-10-04T23:59:59+09:00"
BASE = pd.Timestamp("2026-10-04T00:00:00+09:00").as_unit("ns")


def timestamp(seconds):
    return pd.Timestamp(BASE.value + int(seconds*1_000_000_000), tz="UTC").tz_convert("Asia/Seoul")


def make_frame(seconds, temperatures=None, humidity=None):
    return pd.DataFrame({
        "Timestamp": [timestamp(s) for s in seconds],
        "Temperature": temperatures if temperatures is not None else [20] * len(seconds),
        "Humidity": humidity if humidity is not None else [60] * len(seconds),
        "Soil Moisture": [500] * len(seconds),
    })


def query(analytics, start, end):
    return analytics.summarize_range(timestamp(start), timestamp(end))


def test_irregular_row_mean_and_time_mean_are_distinct():
    result = query(SensorAnalytics(make_frame([0, 120, 480], [10, 20, 30]), now=NOW), 0, 600)
    metric = result["metrics"]["temperature"]
    assert metric["mean"] == 20
    assert metric["time_weighted_mean"] == 17.5
    assert metric["supported_seconds"] == 480
    assert metric["coverage_fraction"] == 0.8
    assert result["coverage"]["observed_support_seconds"] == 480
    assert result["coverage"]["unsupported_seconds"] == 120
    assert result["sampling"]["mean_interval_seconds"] == 240
    assert result["sampling"]["min_interval_seconds"] == 120
    assert result["sampling"]["max_interval_seconds"] == 360
    assert result["sampling"]["gap_count"] == 0
    assert not result["coverage"]["terminal_extrapolation"]


@pytest.mark.parametrize("start,end,support,weighted,count", [
    (60, 300, 240, 17.5, 1),  # Both edge intervals clipped.
    (120, 480, 360, 20, 1),  # Right endpoint contributes support, never a row.
    (300, 360, 60, 20, 0),  # Support estimate may bracket an interval with no row.
    (480, 600, 0, None, 1),  # No extrapolation after the final observation.
    (-120, 60, 60, 10, 1),   # No extrapolation before the first observation.
    (-120, -60, 0, None, 0),
    (600, 660, 0, None, 0),
])
def test_partial_ranges_are_clipped_and_rows_remain_half_open(start, end, support, weighted, count):
    result = query(SensorAnalytics(make_frame([0, 120, 480], [10, 20, 30]), now=NOW), start, end)
    assert result["row_count"] == count
    assert result["metrics"]["temperature"]["supported_seconds"] == support
    assert result["metrics"]["temperature"]["time_weighted_mean"] == weighted
    assert result["coverage"]["observed_support_seconds"] == support
    assert result["end_exclusive"]


def test_long_outage_is_fully_excluded_not_capped_or_interpolated():
    analytics = SensorAnalytics(make_frame([0, 120, 3600, 3720], [10, 20, 30, 40]), now=NOW)
    result = query(analytics, 0, 3840)
    assert result["coverage"]["observed_support_seconds"] == 240
    assert result["sampling"]["gap_count"] == 1
    assert result["sampling"]["max_gap_seconds"] == 3480
    assert result["metrics"]["temperature"]["time_weighted_mean"] == 20
    inside_gap = query(analytics, 600, 1800)
    assert inside_gap["coverage"]["observed_support_seconds"] == 0
    assert inside_gap["metrics"]["temperature"]["time_weighted_mean"] is None
    assert inside_gap["sampling"]["gap_count"] == 1
    assert inside_gap["sampling"]["max_gap_seconds"] == 3480


def test_gap_cap_is_fixed_and_does_not_expand_with_device_downtime():
    analytics = SensorAnalytics(make_frame([0, 600, 1201]), now=NOW)
    result = query(analytics, 0, 1201)
    assert result["sampling"]["typical_interval_seconds"] == 600.5
    assert result["sampling"]["gap_threshold_seconds"] == 600
    assert result["sampling"]["gap_count"] == 1
    assert result["coverage"]["observed_support_seconds"] == 600


def test_missing_metric_excludes_both_adjacent_intervals_but_keeps_other_sensor():
    analytics = SensorAnalytics(make_frame([0, 120, 240], [10, np.nan, 30]), now=NOW)
    result = query(analytics, 0, 300)
    assert result["coverage"]["observed_support_seconds"] == 240
    assert result["metrics"]["temperature"]["mean"] == 20
    assert result["metrics"]["temperature"]["supported_seconds"] == 0
    assert result["metrics"]["temperature"]["time_weighted_mean"] is None
    assert result["metrics"]["humidity"]["supported_seconds"] == 240
    assert result["metrics"]["humidity"]["time_weighted_mean"] == 60
    json.dumps(result, allow_nan=False)


def test_empty_and_single_record_have_no_duration_support():
    for frame in [make_frame([]), make_frame([0])]:
        result = query(SensorAnalytics(frame, now=NOW), 0, 600)
        assert result["coverage"]["observed_support_fraction"] == 0
        assert result["sampling"]["interval_count"] == 0
        assert result["metrics"]["temperature"]["time_weighted_mean"] is None


def test_old_latest_reference_preserves_historical_support_and_real_staleness():
    frame = make_frame([0, 120, 240])
    frame["Timestamp"] = frame["Timestamp"].map(lambda stamp: stamp.replace(year=2025))
    analytics = SensorAnalytics(frame, now=NOW, reference_mode="latest")
    result = analytics.summarize_period("today")
    assert result["analysis_reference"]["date"] == "2025-10-04"
    assert result["freshness"]["stale"]
    assert result["coverage"]["observed_support_seconds"] == 240
    assert result["coverage"]["observed_support_fraction"] == pytest.approx(240/86400)


def test_prefix_summaries_match_direct_clipped_integrals_across_blocks():
    rng = np.random.default_rng(123)
    intervals = rng.choice([1, 2, 4, 12, 900], size=3074, p=[.30, .30, .25, .145, .005])
    seconds = np.r_[0, intervals.cumsum()]
    temperature = 25 + np.sin(np.arange(len(seconds))/13)
    temperature[100::177] = np.nan
    analytics = SensorAnalytics(make_frame(seconds, temperature), now=NOW)
    for start, end in [(1, 450), (1023, 4500), (100, int(seconds[-1])-3), (513, 514)]:
        durations = np.maximum(0, np.minimum(seconds[1:], end)-np.maximum(seconds[:-1], start))
        accepted = (intervals <= 600) & np.isfinite(temperature[:-1]) & np.isfinite(temperature[1:])
        supported = float(durations[accepted].sum())
        integral = float((durations[accepted]*temperature[:-1][accepted]).sum())
        result = query(analytics, start, end)
        metric = result["metrics"]["temperature"]
        assert metric["supported_seconds"] == supported
        if supported:
            assert metric["time_weighted_mean"] == pytest.approx(integral/supported)
        else:
            assert metric["time_weighted_mean"] is None
        overlapping = (seconds[:-1] < end) & (seconds[1:] > start)
        actual = intervals[overlapping]
        assert result["sampling"]["max_interval_seconds"] == float(actual.max())


def test_envelope_keeps_extreme_values_and_their_true_chronological_positions():
    frame = make_frame([60, 120, 180, 240], [20, 80, 5, 20])
    analytics = SensorAnalytics(frame, now=NOW, reference_mode="latest")
    envelope = analytics.chart_envelope(days=1, max_bins=1)
    temp = envelope[envelope.metric == "temperature"].iloc[0]
    assert temp["min"] == 5
    assert temp["max"] == 80
    assert temp["mean"] == 31.25
    assert temp["count"] == 4
    assert temp["max_at"] == timestamp(120)
    assert temp["min_at"] == timestamp(180)
    assert temp["max_at"] < temp["min_at"]
    assert len(envelope) == 3
    assert envelope.attrs["end_exclusive"]


def test_envelope_preserves_empty_bins_and_marks_long_gap_segments():
    analytics = SensorAnalytics(make_frame([60, 180, 6*3600+60, 6*3600+180], [20, 21, 30, 31]), now=NOW)
    envelope = analytics.chart_envelope(days=1, max_bins=24)
    temp = envelope[envelope.metric == "temperature"].reset_index(drop=True)
    assert len(temp) == 24
    assert temp.iloc[2]["count"] == 0
    assert pd.isna(temp.iloc[2]["mean"])
    assert temp.iloc[2]["contains_gap"]
    assert pd.isna(temp.iloc[2]["line_value"])
    assert temp.iloc[0]["count"] == 2
    assert temp.iloc[0]["max"] == 21
    assert temp.iloc[0]["contains_gap"]
    assert pd.isna(temp.iloc[0]["line_value"])
    assert temp.iloc[6]["break_before"]
    assert temp.iloc[6]["segment_id"] != temp.iloc[0]["segment_id"]


def test_envelope_missing_metric_does_not_connect_across_it():
    analytics = SensorAnalytics(make_frame([60, 180, 300], [20, np.nan, 22]), now=NOW)
    envelope = analytics.chart_envelope(days=1, max_bins=24)
    temp = envelope[envelope.metric == "temperature"].iloc[0]
    humidity = envelope[envelope.metric == "humidity"].iloc[0]
    assert temp["contains_missing"]
    assert pd.isna(temp["line_value"])
    assert temp["min"] == 20 and temp["max"] == 22
    assert not humidity["contains_missing"]
    assert humidity["line_value"] == 60


def test_identical_extrema_choose_first_real_timestamp_across_many_blocks():
    seconds = np.arange(2048)*2
    analytics = SensorAnalytics(make_frame(seconds), now=NOW)
    envelope = analytics.chart_envelope(days=1, max_bins=1)
    for row in envelope.itertuples():
        assert row.min_at == BASE
        assert row.max_at == BASE
        assert row.min == row.max == row.mean


@pytest.mark.parametrize("days,max_bins", [(0, 24), (1, 0), (1, 1001), (1, True), (1, 2.5)])
def test_envelope_invalid_bounds_preserve_dataframe_contract(days, max_bins):
    envelope = SensorAnalytics(make_frame([0, 120]), now=NOW).chart_envelope(days, max_bins)
    assert envelope.empty
    assert "error" in envelope.attrs



def test_envelope_extreme_times_match_all_original_values_across_block_edges():
    seconds = np.arange(4000)*20
    temperature = 25+np.sin(np.arange(4000)/7)
    temperature[[700, 1200, 1700]] = [80, 90, 95]
    temperature[[1000, 2000]] = [5, 6]
    frame = make_frame(seconds, temperature)
    analytics = SensorAnalytics(frame, now=NOW)
    envelope = analytics.chart_envelope(days=1, max_bins=5)
    for row in envelope[envelope.metric == "temperature"].itertuples():
        original = frame[(frame.Timestamp >= row.bin_start) & (frame.Timestamp < row.bin_end)]
        assert row.count == len(original)
        assert row.min == original.Temperature.min()
        assert row.max == original.Temperature.max()
        assert row.min_at == original.loc[original.Temperature.idxmin(), "Timestamp"]
        assert row.max_at == original.loc[original.Temperature.idxmax(), "Timestamp"]
