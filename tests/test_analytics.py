"""Behavioral checks for grounding, dates, sensor quality, and bounded queries."""
import json

import numpy as np
import pandas as pd
import pytest

from plant_ai.analytics import SensorAnalytics


NOW = "2026-10-04 12:00:00+09:00"


def sensor_data(times, temperatures=None, humidity=None, soil=None):
    size = len(times)
    return pd.DataFrame({"Timestamp": times,
        "Temperature": temperatures if temperatures is not None else [24] * size,
        "Humidity": humidity if humidity is not None else [60] * size,
        "Soil Moisture": soil if soil is not None else [520] * size})


def test_current_status_is_latest_sorted_reading_not_last_source_row():
    frame = sensor_data(["2026-10-04 11:58", "2026-10-04 11:54", "2026-10-04 11:56"], [22, 20, 21])
    analytics = SensorAnalytics(frame, now=NOW)
    status = analytics.current_status()
    assert status["values"]["temperature"] == 22
    assert status["timestamp"] == "2026-10-04T11:58:00+09:00"
    assert status["freshness"]["age_seconds"] == 120
    assert status["freshness"]["is_live"] is True


def test_freshness_uses_real_now_not_last_record_calendar_date():
    analytics = SensorAnalytics(sensor_data(["2025-01-01 12:00"]), now=NOW)
    assert analytics.current_status()["freshness"]["stale"] is True
    assert analytics.summarize_period("today")["row_count"] == 0
    assert analytics.summarize_period("2025-01-01")["row_count"] == 1


def test_demo_readings_are_never_claimed_live():
    status = SensorAnalytics(sensor_data(["2026-10-04 11:59"]), now=NOW, source="demo").current_status()
    assert status["freshness"]["stale"] is False
    assert status["freshness"]["is_live"] is False
    assert status["source"] == "demo"


def test_freshness_boundary_is_ten_minutes():
    assert not SensorAnalytics(sensor_data(["2026-10-04 11:50"]), now=NOW).current_status()["freshness"]["stale"]
    assert SensorAnalytics(sensor_data(["2026-10-04 11:49:59"]), now=NOW).current_status()["freshness"]["stale"]


def test_timestamp_quality_drops_bad_and_future_and_keeps_last_duplicate():
    frame = sensor_data(["bad", "2026-10-04 11:58", "2026-10-05", "2026-10-04 11:58", None], [1, 20, 30, 25, 99])
    analytics = SensorAnalytics(frame, now=NOW)
    quality = analytics.metadata()["quality"]
    assert quality["invalid_timestamp_rows"] == 2
    assert quality["future_rows"] == 1
    assert quality["duplicate_timestamp_rows"] == 1
    assert quality["retained_rows"] == 1
    assert analytics.current_status()["values"]["temperature"] == 25


def test_partial_bad_readings_do_not_remove_other_valid_sensor_values():
    frame = sensor_data(["2026-10-04 11:54", "2026-10-04 11:56", "2026-10-04 11:58"],
                        ["bad", 200, 22], [101, -1, 70], [1023, -40, "bad"])
    analytics = SensorAnalytics(frame, now=NOW)
    summary = analytics.summarize_period("오늘")
    assert summary["row_count"] == 3
    assert summary["metrics"]["temperature"]["count"] == 1
    assert summary["metrics"]["humidity"]["count"] == 1
    assert summary["metrics"]["soil_moisture"]["count"] == 2
    assert summary["metrics"]["soil_moisture"]["max"] == 1023
    assert "raw" in summary["units"]["soil_moisture"]
    assert analytics.current_status()["values"]["soil_moisture"] is None


def test_missing_numeric_columns_return_null_and_valid_json():
    analytics = SensorAnalytics(pd.DataFrame({"시간": ["2026-10-04 11:59"], "온도": [25]}), now=NOW)
    result = analytics.summarize_period("오늘")
    assert result["metrics"]["humidity"]["mean"] is None
    assert result["metrics"]["soil_moisture"]["last"] is None
    assert analytics.current_status()["values"]["humidity"] is None
    json.dumps(result, allow_nan=False, ensure_ascii=False)


def test_common_headers_and_mixed_timezones_normalize_to_kst():
    frame = pd.DataFrame({"datetime": ["2026-10-04 11:56", "2026-10-04T02:58:00Z"],
                          "Temperature (°C)": [20, 21], "Humidity (%)": [50, 60], "soil_moisture": [600, 590]})
    analytics = SensorAnalytics(frame, now=NOW)
    assert analytics.current_status()["timestamp"] == "2026-10-04T11:58:00+09:00"
    assert analytics.summarize_range("2026-10-04T02:55Z", "2026-10-04T03:00Z")["row_count"] == 2


def test_range_is_end_exclusive():
    frame = sensor_data(["2026-10-02 23:59:59", "2026-10-03 00:00", "2026-10-03 23:59:59", "2026-10-04 00:00"])
    analytics = SensorAnalytics(frame, now=NOW)
    result = analytics.summarize_range("2026-10-03", "2026-10-04")
    assert result["row_count"] == 2
    assert result["end_exclusive"] is True
    assert analytics.summarize_period("어제")["row_count"] == 2


@pytest.mark.parametrize("period", ["nonsense", "next_year", "2026-13", "2026-02-30", "summer", 7, None])
def test_unknown_or_invalid_period_never_falls_back_to_today(period):
    analytics = SensorAnalytics(sensor_data(["2026-10-04 11:59"]), now=NOW)
    result = analytics.summarize_period(period)
    assert not result["ok"]
    assert result["error"]["code"] == "invalid_period"


def test_month_week_korean_aliases_and_leap_year_boundaries():
    analytics = SensorAnalytics(sensor_data(["2024-02-29 23:59", "2024-03-01 00:00", "2026-09-28 00:00"]), now=NOW)
    assert analytics.summarize_period("2024-02")["row_count"] == 1
    assert analytics.summarize_period("2024년 2월")["row_count"] == 1
    assert analytics.summarize_period("2024년 2월 29일")["row_count"] == 1
    assert analytics.summarize_period("이번 주")["start"] == "2026-09-28T00:00:00+09:00"
    assert analytics.summarize_period("지난주")["end"] == "2026-09-28T00:00:00+09:00"
    assert analytics.summarize_period("지난 달")["end"] == "2026-10-01T00:00:00+09:00"


def test_winter_crosses_year_and_includes_leap_day():
    frame = sensor_data(["2023-11-30 23:59", "2023-12-01", "2024-01-31", "2024-02-29 23:59", "2024-03-01"])
    analytics = SensorAnalytics(frame, now=NOW)
    english = analytics.summarize_period("2023 winter")
    korean = analytics.summarize_period("2023년 겨울")
    assert english["row_count"] == korean["row_count"] == 3
    assert english["start"] == "2023-12-01T00:00:00+09:00"
    assert english["end"] == "2024-03-01T00:00:00+09:00"


def test_row_weighted_means_do_not_average_day_means():
    frame = sensor_data(["2026-10-01 10:00", "2026-10-01 10:02", "2026-10-01 10:04", "2026-10-02 10:00"], [10, 10, 10, 30])
    result = SensorAnalytics(frame, now=NOW).summarize_period("this_month")
    assert result["metrics"]["temperature"]["mean"] == 15
    assert result["metrics"]["temperature"]["count"] == 4


def test_comparison_labels_direction_and_missing_period():
    analytics = SensorAnalytics(sensor_data(["2026-10-03 12:00", "2026-10-04 11:00"], [20, 24]), now=NOW)
    result = analytics.compare_periods("yesterday", "today")
    assert result["differences"]["temperature"]["mean_difference"] == 4
    assert result["difference_direction"] == "period_b minus period_a"
    assert not analytics.compare_periods("2020-01", "today")["ok"]


def test_empty_missing_schema_and_out_of_range_are_explicit():
    analytics = SensorAnalytics(pd.DataFrame(), now=NOW)
    assert not analytics.current_status()["ok"]
    assert analytics.metadata()["row_count"] == 0
    assert analytics.chart_data().empty
    assert analytics.summarize_period("today")["error"]["code"] == "no_data"
    assert analytics.summarize_range("2026-01-02", "2026-01-01")["error"]["code"] == "invalid_range"
    assert analytics.summarize_range("2026-01-01", "2026-01-01")["error"]["code"] == "invalid_range"
    populated = SensorAnalytics(sensor_data(["2026-10-04 11:59"]), now=NOW)
    assert populated.summarize_period("2020-01")["error"]["code"] == "no_data"


def test_range_aggregates_match_observations_across_precomputed_block_edges():
    size = 4097
    times = pd.date_range("2026-09-20", periods=size, freq="2min")
    rng = np.random.default_rng(8)
    temperatures = rng.uniform(10, 30, size)
    temperatures[[0, 3, 511, 512, 2100, 4096]] = np.nan
    frame = sensor_data(times, temperatures)
    analytics = SensorAnalytics(frame, now=NOW)
    for left, right in [(0, size), (1, 511), (511, 1024), (512, 1024), (513, 4096), (0, 1), (4096, 4097), (2100, 2101)]:
        end = times[right] if right < size else times[-1] + pd.Timedelta(minutes=2)
        result = analytics.summarize_range(times[left], end)["metrics"]["temperature"]
        valid = temperatures[left:right][np.isfinite(temperatures[left:right])]
        assert result["count"] == len(valid)
        if len(valid):
            assert result["mean"] == pytest.approx(float(valid.mean()))
            assert result["min"] == float(valid.min())
            assert result["max"] == float(valid.max())
            assert result["last"] == float(valid[-1])
        else:
            assert result["mean"] is result["min"] is result["max"] is result["last"] is None


def test_daily_trend_preserves_missing_days_and_bounds_payload():
    analytics = SensorAnalytics(sensor_data(["2026-10-02 12:00", "2026-10-04 11:59"], [20, 24]), now=NOW)
    result = analytics.daily_trend(3)
    assert [day["row_count"] for day in result["days"]] == [1, 0, 1]
    assert result["days"][1]["metrics"]["temperature"]["mean"] is None
    assert not analytics.daily_trend(91)["ok"]
    assert not analytics.daily_trend(True)["ok"]
    assert not analytics.daily_trend(2.5)["ok"]


def test_events_report_transparent_changes_without_causal_claims():
    frame = sensor_data(["2026-10-04 10:00", "2026-10-04 10:02", "2026-10-04 10:04", "2026-10-04 11:58"],
                        [20, 21, 28, 28], [50, 50, 80, 80], [500, 500, 700, 700])
    result = SensorAnalytics(frame, now=NOW).detect_events(1)
    assert result["event_count"] == 4
    assert {event["kind"] for event in result["events"]} == {"sensor_jump", "measurement_gap"}
    assert result["heuristics"]["absolute_change_at_least"]["soil_moisture"] == 150
    assert all("cause" not in event for event in result["events"])
    json.dumps(result, allow_nan=False)


def test_chart_and_event_payloads_remain_bounded():
    size = 2000
    times = pd.date_range("2026-10-01", periods=size, freq="2min")
    analytics = SensorAnalytics(sensor_data(times, np.tile([20, 30], size//2)), now=NOW)
    chart = analytics.chart_data(days=7, max_points=100)
    assert len(chart) == 100
    assert list(chart.columns) == ["Timestamp", "Temperature", "Humidity", "Soil Moisture"]
    assert str(chart["Timestamp"].dtype) == "datetime64[ns, Asia/Seoul]"
    assert chart.attrs["sampled"] is True
    events = analytics.detect_events(7)
    assert len(events["events"]) == 50
    assert events["truncated"] is True
    assert analytics.chart_data(days=0).attrs["error"]
    assert analytics.chart_data(max_points=100000).attrs["error"]


def test_all_public_dictionaries_are_strict_json_serializable():
    analytics = SensorAnalytics(sensor_data(["2026-10-03 12:00", "2026-10-04 11:59"], [np.inf, np.nan], [np.nan, 50]), now=NOW)
    outputs = [analytics.metadata(), analytics.current_status(), analytics.summarize_period("today"),
               analytics.summarize_range("2026-10-01", "2026-10-05"), analytics.compare_periods("yesterday", "today"),
               analytics.daily_trend(2), analytics.detect_events(2)]
    for output in outputs:
        json.dumps(output, allow_nan=False, ensure_ascii=False)


def test_latest_reference_restores_offline_sensor_analysis_without_claiming_live():
    frame = sensor_data(["2026-09-02 11:56", "2026-09-02 11:58"], [20, 28])
    calendar = SensorAnalytics(frame, now=NOW, reference_mode="calendar")
    latest = SensorAnalytics(frame, now=NOW, reference_mode="latest")
    assert calendar.summarize_period("today")["row_count"] == 0
    result = latest.summarize_period("today")
    assert result["row_count"] == 2
    assert result["start"] == "2026-09-02T00:00:00+09:00"
    assert result["analysis_reference"] == {"mode": "latest", "timestamp": "2026-09-02T11:58:00+09:00", "date": "2026-09-02"}
    assert latest.summarize_period("this_month")["start"] == "2026-09-01T00:00:00+09:00"
    assert latest.summarize_period("this_week")["start"] == "2026-08-31T00:00:00+09:00"
    assert latest.daily_trend(1)["row_count"] == 2
    assert len(latest.chart_data(1)) == 2
    assert len(calendar.chart_data(1)) == 0
    assert latest.detect_events(1)["event_count"] == 1
    assert calendar.detect_events(1)["event_count"] == 0
    for analytics in [calendar, latest]:
        status = analytics.current_status()
        assert status["freshness"]["stale"] is True
        assert status["freshness"]["is_live"] is False
        assert status["freshness"]["checked_at"] == "2026-10-04T12:00:00+09:00"
    assert latest.summarize_period("2026-10-04")["row_count"] == 0


def test_latest_empty_reference_is_explicit_and_source_is_independent():
    empty = SensorAnalytics(pd.DataFrame(), now=NOW, reference_mode="latest")
    assert empty.metadata()["analysis_reference"] == {"mode": "latest", "timestamp": None, "date": None}
    assert empty.summarize_period("today")["error"]["code"] == "no_data"
    assert isinstance(empty.current_status()["error"], dict)
    demo = SensorAnalytics(sensor_data(["2026-10-04 11:59"]), now=NOW, source="demo", reference_mode="latest")
    assert demo.current_status()["freshness"]["is_live"] is False
    assert demo.metadata()["analysis_reference"]["mode"] == "latest"
    with pytest.raises(ValueError, match="reference_mode"):
        SensorAnalytics(pd.DataFrame(), now=NOW, reference_mode="invalid")
