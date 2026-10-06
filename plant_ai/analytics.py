"""Bounded, evidence-first sensor queries over one normalized time-series snapshot.

All ranges are half-open [start, end). Naive times refer to the configured local
timezone. Soil readings stay in raw sensor units; they are never labelled as %.
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd


class SensorAnalytics:
    METRICS = ("temperature", "humidity", "soil_moisture")
    COLUMNS = {"temperature": "Temperature", "humidity": "Humidity", "soil_moisture": "Soil Moisture"}
    UNITS = {"temperature": "°C", "humidity": "%RH", "soil_moisture": "raw sensor units"}
    BLOCK_SIZE = 512
    FRESHNESS_SECONDS = 600
    SUPPORT_GAP_CAP_SECONDS = 600.0

    def __init__(self, df: pd.DataFrame, now=None, source="live", timezone="Asia/Seoul", reference_mode="calendar"):
        if reference_mode not in {"calendar", "latest"}:
            raise ValueError("reference_mode must be 'calendar' or 'latest'.")
        self.reference_mode = reference_mode
        self.timezone = timezone
        self.source = str(source)
        self._fixed_now = self._timestamp(now) if now is not None else None
        self.created_at = self._now()
        self._schema_error = None
        raw = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
        names = {}
        for column in raw.columns:
            normalized = re.sub(r"[\W_]+", "", str(column)).lower()
            if "soil" in normalized or "토양" in normalized:
                target = "Soil Moisture"
            elif "temperature" in normalized or normalized in {"temp", "tempc", "온도", "온도c"}:
                target = "Temperature"
            elif "humidity" in normalized or normalized in {"rh", "습도"}:
                target = "Humidity"
            elif "timestamp" in normalized or normalized in {"time", "datetime", "date", "시간", "측정시간", "기록시간", "일시", "날짜", "타임스탬프"}:
                target = "Timestamp"
            else:
                continue
            names.setdefault(target, column)
        self.quality = {
            "input_rows": int(len(raw)), "invalid_timestamp_rows": 0,
            "future_rows": 0, "duplicate_timestamp_rows": 0,
            "missing_columns": [c for c in ["Timestamp", *self.COLUMNS.values()] if c not in names],
            "invalid_numeric_values": {}, "missing_numeric_values": {},
        }
        frame = pd.DataFrame(index=raw.index)
        if "Timestamp" not in names:
            self._schema_error = "Timestamp/시간 열이 없습니다."
            times = pd.Series(pd.NaT, index=raw.index, dtype=f"datetime64[ns, {timezone}]")
        else:
            times = self._parse_times(raw[names["Timestamp"]])
        frame["Timestamp"] = times
        for key, column in self.COLUMNS.items():
            original = raw[names[column]] if column in names else pd.Series(np.nan, index=raw.index)
            numeric = pd.to_numeric(original, errors="coerce").astype(float)
            valid = np.isfinite(numeric)
            if key == "humidity":
                valid &= numeric.between(0, 100)
            elif key == "temperature":
                valid &= numeric.between(-50, 100)
            self.quality["invalid_numeric_values"][key] = int((original.notna() & ~valid).sum())
            self.quality["missing_numeric_values"][key] = int(original.isna().sum())
            frame[column] = numeric.where(valid, np.nan)
        self.quality["invalid_timestamp_rows"] = int(times.isna().sum())
        future = times.notna() & (times > self.created_at)
        self.quality["future_rows"] = int(future.sum())
        frame = frame.loc[times.notna() & ~future].sort_values("Timestamp", kind="stable")
        # At the same timestamp, retain the last received record, consistently.
        self.quality["duplicate_timestamp_rows"] = int(frame["Timestamp"].duplicated(keep="last").sum())
        frame = frame.drop_duplicates("Timestamp", keep="last").set_index("Timestamp")
        self._frame = frame
        self._index = pd.DatetimeIndex(frame.index)
        self.quality["retained_rows"] = int(len(frame))
        self._values = frame[list(self.COLUMNS.values())].to_numpy(dtype=float)
        finite = np.isfinite(self._values)
        self._sums = np.vstack([np.zeros((1, 3)), np.cumsum(np.where(finite, self._values, 0), axis=0)])
        self._counts = np.vstack([np.zeros((1, 3), dtype=np.int64), np.cumsum(finite, axis=0)])
        positions = np.arange(len(frame))[:, None]
        self._last_valid = np.maximum.accumulate(np.where(finite, positions, -1), axis=0) if len(frame) else np.empty((0, 3), dtype=int)
        # Range minima/maxima inspect at most two 512-row edges plus block summaries.
        self._block_min = []
        self._block_max = []
        for start in range(0, len(frame), self.BLOCK_SIZE):
            block = self._values[start:start + self.BLOCK_SIZE]
            self._block_min.append(np.min(np.where(np.isfinite(block), block, np.inf), axis=0))
            self._block_max.append(np.max(np.where(np.isfinite(block), block, -np.inf), axis=0))
        self._block_min = np.array(self._block_min).reshape(-1, 3)
        self._block_max = np.array(self._block_max).reshape(-1, 3)
        gaps = self._index.to_series().diff().dt.total_seconds().to_numpy()
        self._cadence = float(np.median(gaps[1:])) if len(gaps) > 1 else None
        self._gap_threshold = max(600.0, 3 * self._cadence) if self._cadence else 600.0
        self._build_support_index(finite, gaps[1:])
        self._events = self._build_events(gaps)


    def _build_support_index(self, finite, intervals):
        """Index bounded adjacent intervals; never extrapolate the final row.

        The fixed cap does not expand when a device is usually off. Metric
        support requires finite readings at both ends. Accepted intervals use
        a left-hand step approximation, explicitly separate from row means.
        """
        self._intervals = np.asarray(intervals, dtype=float)
        self._supported = self._intervals <= self.SUPPORT_GAP_CAP_SECONDS
        support = np.where(self._supported, self._intervals, 0.0)
        self._metric_supported = finite[:-1] & finite[1:] & self._supported[:, None]
        durations = np.where(self._metric_supported, self._intervals[:, None], 0.0)
        weighted = np.where(self._metric_supported, self._values[:-1], 0.0) * durations
        self._support_prefix = np.r_[0.0, np.cumsum(support)]
        self._duration_prefix = np.vstack([np.zeros((1, 3)), np.cumsum(durations, axis=0)])
        self._weighted_prefix = np.vstack([np.zeros((1, 3)), np.cumsum(weighted, axis=0)])
        self._interval_prefix = np.r_[0.0, np.cumsum(self._intervals)]
        self._gap_prefix = np.r_[0, np.cumsum(~self._supported)]
        self._interval_block_min = np.array([self._intervals[i:i+self.BLOCK_SIZE].min()
                                            for i in range(0, len(self._intervals), self.BLOCK_SIZE)])
        self._interval_block_max = np.array([self._intervals[i:i+self.BLOCK_SIZE].max()
                                            for i in range(0, len(self._intervals), self.BLOCK_SIZE)])

    def _interval_extrema(self, left, right):
        b = self.BLOCK_SIZE
        first_full, after_full = (left + b - 1) // b, right // b
        if first_full >= after_full:
            return float(self._intervals[left:right].min()), float(self._intervals[left:right].max())
        edges = np.r_[self._intervals[left:first_full*b], self._intervals[after_full*b:right]]
        lows, highs = self._interval_block_min[first_full:after_full], self._interval_block_max[first_full:after_full]
        return (float(min(lows.min(), edges.min())) if len(edges) else float(lows.min()),
                float(max(highs.max(), edges.max())) if len(edges) else float(highs.max()))

    def _support_summary(self, start, end):
        requested = float((end-start).total_seconds())
        left = max(0, int(self._index.searchsorted(start, side="right"))-1)
        right = min(len(self._intervals), int(self._index.searchsorted(end, side="left")))
        support, durations, weighted = 0.0, np.zeros(3), np.zeros(3)
        sampling = {"typical_interval_seconds": self._cadence, "typical_interval_scope": "full_snapshot",
                    "interval_count": max(0, right-left), "mean_interval_seconds": None,
                    "min_interval_seconds": None, "max_interval_seconds": None,
                    "gap_count": 0, "max_gap_seconds": 0.0,
                    "gap_threshold_seconds": self.SUPPORT_GAP_CAP_SECONDS,
                    "interval_scope": "adjacent intervals overlapping the requested half-open range"}
        if right > left:
            support = float(self._support_prefix[right]-self._support_prefix[left])
            durations = self._duration_prefix[right]-self._duration_prefix[left]
            weighted = self._weighted_prefix[right]-self._weighted_prefix[left]
            # Clip both edges, even when both lie within the same interval.
            for pos, clipped_seconds in [(left, max(0.0, (start-self._index[left]).total_seconds())),
                                         (right-1, max(0.0, (self._index[right]-end).total_seconds()))]:
                if self._supported[pos]:
                    support -= clipped_seconds
                valid = self._metric_supported[pos]
                durations -= valid * clipped_seconds
                weighted -= np.where(valid, self._values[pos], 0.0) * clipped_seconds
            low, high = self._interval_extrema(left, right)
            sampling.update(mean_interval_seconds=float((self._interval_prefix[right]-self._interval_prefix[left])/(right-left)),
                            min_interval_seconds=low, max_interval_seconds=high,
                            gap_count=int(self._gap_prefix[right]-self._gap_prefix[left]),
                            max_gap_seconds=high if high > self.SUPPORT_GAP_CAP_SECONDS else 0.0)
        support = min(requested, max(0.0, support))
        durations = np.maximum(durations, 0.0)
        coverage = {"requested_seconds": requested, "observed_support_seconds": support,
                    "observed_support_fraction": support/requested, "unsupported_seconds": requested-support,
                    "support_gap_cap_seconds": self.SUPPORT_GAP_CAP_SECONDS,
                    "method": "Adjacent timestamps at most 600s apart support the clipped interval; longer intervals are entirely unsupported. Metric support requires finite endpoints. Time-weighted means use the left reading within supported intervals only; no interpolation or terminal extrapolation.",
                    "support_is_estimated": True, "terminal_extrapolation": False}
        metric_support = {key: {"supported_seconds": float(durations[j]),
                               "coverage_fraction": float(durations[j]/requested),
                               "time_weighted_mean": self._number(weighted[j]/durations[j]) if durations[j] > 0 else None}
                          for j, key in enumerate(self.METRICS)}
        return sampling, coverage, metric_support

    def _timestamp(self, value):
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            raise ValueError("시간이 비어 있거나 유효하지 않습니다.")
        return ts.tz_localize(self.timezone) if ts.tzinfo is None else ts.tz_convert(self.timezone)

    def _now(self):
        return self._fixed_now if self._fixed_now is not None else pd.Timestamp.now(tz=self.timezone)

    def _parse_times(self, values):
        # Uniform timestamp columns use vectorized parsing; only mixed timezone
        # representations need the slower per-record normalization fallback.
        try:
            parsed = pd.to_datetime(values, errors="coerce", format="mixed")
            if isinstance(parsed.dtype, pd.DatetimeTZDtype):
                return parsed.dt.tz_convert(self.timezone)
            if pd.api.types.is_datetime64_dtype(parsed.dtype):
                return parsed.dt.tz_localize(self.timezone, ambiguous="NaT", nonexistent="NaT")
        except (ValueError, TypeError):
            pass
        def parse(value):
            try:
                return self._timestamp(value)
            except (ValueError, TypeError, OverflowError):
                return pd.NaT
        return pd.Series(pd.DatetimeIndex([parse(value) for value in values]), index=values.index, dtype=f"datetime64[ns, {self.timezone}]")

    @staticmethod
    def _number(value):
        return float(value) if value is not None and np.isfinite(value) else None

    def _freshness(self):
        latest = self._index[-1] if len(self._index) else None
        age = max(0.0, float((self._now() - latest).total_seconds())) if latest is not None else None
        stale = age is None or age > self.FRESHNESS_SECONDS
        return {"checked_at": self._now().isoformat(), "last_sample_at": latest.isoformat() if latest is not None else None,
                "age_seconds": age, "threshold_seconds": self.FRESHNESS_SECONDS, "stale": stale,
                "is_live": self.source == "live" and not stale,
                "status": "no_data" if latest is None else ("stale" if stale else ("fresh" if self.source == "live" else "sample"))}

    def _base(self):
        return {"ok": True, "source": self.source, "timezone": self.timezone,
                "units": dict(self.UNITS), "quality": self.quality, "freshness": self._freshness(),
                "analysis_reference": self._analysis_reference()}

    def _reference_time(self):
        """Analysis may follow the last observation; freshness always uses now."""
        if self.reference_mode == "latest" and len(self._index):
            return self._index[-1]
        return self._now()

    def _analysis_reference(self):
        reference = self._reference_time() if self.reference_mode == "calendar" or len(self._index) else None
        return {"mode": self.reference_mode,
                "timestamp": reference.isoformat() if reference is not None else None,
                "date": reference.date().isoformat() if reference is not None else None}

    def _error(self, code, message):
        return {**self._base(), "ok": False, "error": {"code": code, "message": message}}

    def _bounds(self, start, end):
        return int(self._index.searchsorted(start, side="left")), int(self._index.searchsorted(end, side="left"))

    def _range_extrema(self, left, right):
        b = self.BLOCK_SIZE
        first_full = (left + b - 1) // b
        after_full = right // b
        if first_full >= after_full:
            edges = self._values[left:right]
            return (np.min(np.where(np.isfinite(edges), edges, np.inf), axis=0),
                    np.max(np.where(np.isfinite(edges), edges, -np.inf), axis=0))
        edges = np.vstack([self._values[left:first_full*b], self._values[after_full*b:right]])
        lows = self._block_min[first_full:after_full]
        highs = self._block_max[first_full:after_full]
        if len(edges):
            lows = np.vstack([lows, np.where(np.isfinite(edges), edges, np.inf)])
            highs = np.vstack([highs, np.where(np.isfinite(edges), edges, -np.inf)])
        return lows.min(axis=0), highs.max(axis=0)

    def _summary(self, start, end):
        left, right = self._bounds(start, end)
        sampling, coverage, metric_support = self._support_summary(start, end)
        result = {"start": start.isoformat(), "end": end.isoformat(), "end_exclusive": True,
                  "row_count": right - left, "metrics": {}, "aggregation": "mean of valid readings (row-weighted, not time-weighted)",
                  "sampling": sampling, "coverage": coverage}
        if right == left:
            for key in self.METRICS:
                result["metrics"][key] = {"count": 0, "mean": None, "min": None, "max": None, "last": None, "last_at": None, "unit": self.UNITS[key], **metric_support[key]}
            result["first_sample_at"] = result["last_sample_at"] = None
            return result
        counts = self._counts[right] - self._counts[left]
        sums = self._sums[right] - self._sums[left]
        lows, highs = self._range_extrema(left, right)
        for j, key in enumerate(self.METRICS):
            pos = int(self._last_valid[right - 1, j])
            result["metrics"][key] = {"count": int(counts[j]), "mean": self._number(sums[j] / counts[j]) if counts[j] else None,
                "min": self._number(lows[j]), "max": self._number(highs[j]),
                "last": self._number(self._values[pos, j]) if pos >= left else None,
                "last_at": self._index[pos].isoformat() if pos >= left else None, "unit": self.UNITS[key], **metric_support[key]}
        result["first_sample_at"] = self._index[left].isoformat()
        result["last_sample_at"] = self._index[right - 1].isoformat()
        return result

    def metadata(self):
        return {**self._base(), "row_count": len(self._frame),
                "first_sample_at": self._index[0].isoformat() if len(self._index) else None,
                "last_sample_at": self._index[-1].isoformat() if len(self._index) else None,
                "typical_interval_seconds": self._cadence,
                "support_gap_cap_seconds": self.SUPPORT_GAP_CAP_SECONDS,
                "soil_note": "토양수분은 보정되지 않은 센서 원시값입니다. 백분율이나 건조/젖음 방향을 가정하지 않습니다.",
                "schema_error": self._schema_error}

    def current_status(self):
        if self._frame.empty:
            return self._error("no_data", self._schema_error or "사용 가능한 센서 데이터가 없습니다.")
        values = {key: self._number(self._values[-1, j]) for j, key in enumerate(self.METRICS)}
        return {**self._base(), "timestamp": self._index[-1].isoformat(), "row_count": 1, "values": values,
                "evidence": "마지막 측정값입니다. 현재 상태로 설명하기 전에 freshness를 확인하세요. 토양 원시값만으로 물주기 필요성을 단정할 수 없습니다."}

    def _period(self, period):
        if not isinstance(period, str):
            raise ValueError("기간은 문자열로 지정해야 합니다.")
        value = re.sub(r"\s+", "", period.strip().lower())
        aliases = {"오늘": "today", "어제": "yesterday", "이번주": "this_week", "금주": "this_week", "지난주": "last_week", "저번주": "last_week",
                   "이번달": "this_month", "이달": "this_month", "지난달": "last_month", "저번달": "last_month"}
        value = aliases.get(value, value)
        value = {"thisweek": "this_week", "lastweek": "last_week", "thismonth": "this_month", "lastmonth": "last_month"}.get(value, value)
        today = self._reference_time().normalize()
        if value == "today":
            return today, today + pd.DateOffset(days=1)
        if value == "yesterday":
            return today - pd.DateOffset(days=1), today
        if value in {"this_week", "last_week"}:
            monday = today - pd.DateOffset(days=today.weekday())
            return (monday, monday + pd.DateOffset(days=7)) if value == "this_week" else (monday - pd.DateOffset(days=7), monday)
        if value in {"this_month", "last_month"}:
            month = today.replace(day=1)
            return (month, month + pd.DateOffset(months=1)) if value == "this_month" else (month - pd.DateOffset(months=1), month)
        date_match = re.fullmatch(r"(\d{4})년(\d{1,2})월(?:(\d{1,2})일)?", value)
        if date_match:
            year, month, day = date_match.groups()
            value = f"{int(year):04d}-{int(month):02d}" + (f"-{int(day):02d}" if day else "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            start = self._timestamp(value)
            return start, start + pd.DateOffset(days=1)
        if re.fullmatch(r"\d{4}-\d{2}", value):
            start = self._timestamp(value + "-01")
            return start, start + pd.DateOffset(months=1)
        season = re.fullmatch(r"(\d{4})년?[_-]?(spring|summer|autumn|fall|winter|봄|여름|가을|겨울)", value)
        if season:
            year, name = season.groups()
            month = {"spring":3,"봄":3,"summer":6,"여름":6,"autumn":9,"fall":9,"가을":9,"winter":12,"겨울":12}[name]
            start = self._timestamp(f"{int(year):04d}-{month:02d}-01")
            return start, start + pd.DateOffset(months=3)
        raise ValueError("지원하지 않는 기간입니다. today/yesterday/this_week/last_week/this_month/last_month, YYYY-MM-DD, YYYY-MM, '2025 summer' 또는 '2025년 겨울'을 사용하세요. 겨울은 해당 연도 12월부터 다음 해 3월 전까지입니다.")

    def summarize_range(self, start: str, end: str):
        try:
            start_ts, end_ts = self._timestamp(start), self._timestamp(end)
            if end_ts <= start_ts:
                raise ValueError("종료 시각은 시작 시각보다 뒤여야 합니다. 종료 시각은 포함하지 않습니다.")
        except (TypeError, ValueError, OverflowError) as exc:
            return self._error("invalid_range", str(exc))
        result = {**self._base(), **self._summary(start_ts, end_ts)}
        if not result["row_count"]:
            result.update(ok=False, error={"code": "no_data", "message": "지정한 기간에 사용 가능한 관측이 없습니다."})
        return result

    def summarize_period(self, period: str):
        try:
            start, end = self._period(period)
        except (TypeError, ValueError, OverflowError) as exc:
            return self._error("invalid_period", str(exc))
        return {**self.summarize_range(start, end), "period": period}

    def compare_periods(self, period_a: str, period_b: str):
        a, b = self.summarize_period(period_a), self.summarize_period(period_b)
        result = {**self._base(), "period_a": a, "period_b": b, "difference_direction": "period_b minus period_a", "differences": {}}
        if not a["ok"] or not b["ok"]:
            result.update(ok=False, error={"code": "comparison_unavailable", "message": "두 기간 모두 유효한 관측이 있어야 비교할 수 있습니다."})
            return result
        for key in self.METRICS:
            av, bv = a["metrics"][key]["mean"], b["metrics"][key]["mean"]
            result["differences"][key] = {"mean_difference": bv - av if av is not None and bv is not None else None, "unit": self.UNITS[key]}
        return result

    def _days(self, days, maximum=90):
        if isinstance(days, bool) or not isinstance(days, (int, np.integer)) or not 1 <= days <= maximum:
            raise ValueError(f"days는 1~{maximum}의 정수여야 합니다.")
        end = self._reference_time().normalize() + pd.DateOffset(days=1)
        return end - pd.DateOffset(days=int(days)), end

    def daily_trend(self, days: int = 7):
        try:
            start, end = self._days(days)
        except ValueError as exc:
            return self._error("invalid_days", str(exc))
        boundaries = pd.date_range(start, end, freq="D")
        daily = [self._summary(a, b) for a, b in zip(boundaries[:-1], boundaries[1:])]
        return {**self._base(), "start": start.isoformat(), "end": end.isoformat(), "end_exclusive": True,
                "row_count": sum(day["row_count"] for day in daily), "days": daily,
                "evidence": "analysis_reference 기준일을 포함한 날짜별 관측 통계입니다. latest는 마지막 관측일, calendar는 실제 오늘 기준입니다. 관측 없는 날은 null이며 변화의 원인을 추정하지 않습니다."}

    def _build_events(self, gaps):
        events = []
        jumps = {"temperature": 5.0, "humidity": 20.0, "soil_moisture": 150.0}
        for pos in np.flatnonzero(np.isfinite(gaps) & (gaps > self._gap_threshold)):
            events.append({"timestamp": self._index[pos].isoformat(), "kind": "measurement_gap",
                "previous_timestamp": self._index[pos-1].isoformat(), "gap_seconds": float(gaps[pos])})
        if len(self._values) > 1:
            changes = np.diff(self._values, axis=0)
            for j, key in enumerate(self.METRICS):
                for previous in np.flatnonzero(np.isfinite(changes[:, j]) & (np.abs(changes[:, j]) >= jumps[key])):
                    pos = int(previous) + 1
                    events.append({"timestamp": self._index[pos].isoformat(), "previous_timestamp": self._index[pos-1].isoformat(),
                        "kind": "sensor_jump", "metric": key, "change": float(changes[previous, j]), "unit": self.UNITS[key],
                        "interval_seconds": float(gaps[pos])})
        events.sort(key=lambda event: event["timestamp"])
        self._event_times = pd.DatetimeIndex([event["timestamp"] for event in events]) if events else pd.DatetimeIndex([], tz=self.timezone)
        return events

    def detect_events(self, days: int = 7):
        try:
            start, end = self._days(days)
        except ValueError as exc:
            return self._error("invalid_days", str(exc))
        left, right = int(self._event_times.searchsorted(start)), int(self._event_times.searchsorted(end))
        rows_left, rows_right = self._bounds(start, end)
        return {**self._base(), "start": start.isoformat(), "end": end.isoformat(), "end_exclusive": True,
            "row_count": rows_right - rows_left, "event_count": right-left,
            "events": self._events[max(left, right-50):right], "truncated": right-left > 50,
            "heuristics": {"gap_seconds_above": self._gap_threshold, "absolute_change_at_least": {"temperature": 5, "humidity": 20, "soil_moisture": 150}, "maximum_returned": 50},
            "evidence": "규칙으로 찾은 기록 공백/큰 값 변화입니다. 물주기, 고장, 식물 스트레스 등의 원인 진단이 아닙니다. 토양값은 원시 단위입니다."}

    def chart_data(self, days: int = 7, max_points: int = 240) -> pd.DataFrame:
        """Return original display column names, bounded evenly spaced observations.

        Invalid arguments return an empty frame carrying an ``error`` in attrs,
        preserving this method's DataFrame contract. Sampling is for display and
        may omit peaks; summaries always use every valid observation.
        """
        empty = self._frame.iloc[:0].reset_index()
        try:
            start, end = self._days(days, maximum=3660)
            if isinstance(max_points, bool) or not isinstance(max_points, (int, np.integer)) or not 2 <= max_points <= 2000:
                raise ValueError("max_points는 2~2000의 정수여야 합니다.")
        except ValueError as exc:
            empty.attrs["error"] = str(exc)
            return empty
        left, right = self._bounds(start, end)
        positions = np.linspace(left, right - 1, min(right-left, int(max_points)), dtype=int) if right > left else []
        result = self._frame.iloc[positions].reset_index().copy()
        result.attrs.update({"source": self.source, "timezone": self.timezone, "units": dict(self.UNITS),
                             "analysis_reference": self._analysis_reference(), "sampled": right-left > max_points})
        return result

    def _first_extreme_at(self, left, right, metric_index, value, minimum):
        """Find the first true extreme without scanning the complete range."""
        if not np.isfinite(value):
            return pd.NaT
        b = self.BLOCK_SIZE
        first_full, after_full = (left+b-1)//b, right//b
        spans = [(left, right)]
        if first_full < after_full:
            spans = [(left, first_full*b)]
            blocks = self._block_min if minimum else self._block_max
            candidates = np.flatnonzero(blocks[first_full:after_full, metric_index] == value)
            if len(candidates):
                block = (first_full+int(candidates[0]))*b
                spans.append((block, block+b))
            spans.append((after_full*b, right))
        for lo, hi in spans:
            positions = np.flatnonzero(self._values[lo:hi, metric_index] == value)
            if len(positions):
                return self._index[lo+int(positions[0])]
        return pd.NaT

    def chart_envelope(self, days: int = 7, max_bins: int = 240) -> pd.DataFrame:
        """Bounded long-form min/max/mean bins with true extreme timestamps.

        Every requested bin is returned for each metric, including empty bins.
        Min/max retain actual observations that simple point sampling can omit.
        Plot line_value grouped by metric AND segment_id, never bridge NaN.
        A bin containing an outage or missing metric has no line_value; its
        observed extrema remain available for scatter or interval displays.
        """
        columns = ["bin_start", "bin_end", "Timestamp", "metric", "unit", "row_count",
                   "count", "mean", "min", "max", "min_at", "max_at", "supported_seconds",
                   "contains_gap", "contains_missing", "break_before", "segment_id", "line_value"]
        empty = pd.DataFrame(columns=columns)
        try:
            start, end = self._days(days, maximum=3660)
            if isinstance(max_bins, bool) or not isinstance(max_bins, (int, np.integer)) or not 1 <= max_bins <= 1000:
                raise ValueError("max_bins는 1~1000의 정수여야 합니다.")
        except ValueError as exc:
            empty.attrs["error"] = str(exc)
            return empty
        boundaries = pd.date_range(start=start, end=end, periods=int(max_bins)+1)
        rows, previous, segments = [], {}, {key: -1 for key in self.METRICS}
        for a, b in zip(boundaries[:-1], boundaries[1:]):
            left, right = self._bounds(a, b)
            summary = self._summary(a, b)
            has_gap = summary["sampling"]["gap_count"] > 0
            for j, key in enumerate(self.METRICS):
                stat = summary["metrics"][key]
                missing = stat["count"] < summary["row_count"]
                unsafe_line = has_gap or missing or stat["count"] == 0
                break_before = key not in previous or unsafe_line or previous[key]
                if break_before:
                    segments[key] += 1
                previous[key] = unsafe_line
                low, high = stat["min"], stat["max"]
                rows.append({"bin_start": a, "bin_end": b, "Timestamp": a+(b-a)/2,
                    "metric": key, "unit": self.UNITS[key], "row_count": summary["row_count"],
                    "count": stat["count"], "mean": stat["mean"] if stat["count"] else np.nan,
                    "min": low if low is not None else np.nan, "max": high if high is not None else np.nan,
                    "min_at": self._first_extreme_at(left, right, j, low, True) if low is not None else pd.NaT,
                    "max_at": self._first_extreme_at(left, right, j, high, False) if high is not None else pd.NaT,
                    "supported_seconds": stat["supported_seconds"], "contains_gap": has_gap,
                    "contains_missing": missing, "break_before": break_before, "segment_id": segments[key],
                    "line_value": np.nan if unsafe_line else stat["mean"]})
        result = pd.DataFrame(rows, columns=columns)
        result.attrs.update({"source": self.source, "timezone": self.timezone, "units": dict(self.UNITS),
            "analysis_reference": self._analysis_reference(), "start": start.isoformat(), "end": end.isoformat(),
            "end_exclusive": True, "bins": int(max_bins), "support_gap_cap_seconds": self.SUPPORT_GAP_CAP_SECONDS,
            "aggregation": "mean/min/max of all valid readings in each bin; min_at/max_at are actual sample timestamps",
            "line_policy": "Plot line_value separately by metric and segment_id; never connect across NaN. Bins with long gaps or missing metric readings suppress their line value, while retaining observed extrema."})
        return result
