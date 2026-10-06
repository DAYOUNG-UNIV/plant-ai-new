"""Configuration, bounded CSV loading, and explicitly requested demo data.

There are no network calls at import time and no automatic demo fallbacks.
"""

from __future__ import annotations

import csv
import io
import os
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

import numpy as np
import pandas as pd


MAX_CSV_BYTES = 25 * 1024 * 1024
DEFAULT_MODEL = "gemini-3.8-flash"
KST = timezone(timedelta(hours=9))


class DataLoadError(ValueError):
    """A safe, user-facing data loading error without upstream response text."""


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)
    model: str = DEFAULT_MODEL
    sheet_id: str = ""
    sheet_gid: str = "0"
    demo_site_url: str = ""


def get_settings(
    secrets: Mapping | None = None, environ: Mapping | None = None
) -> Settings:
    """Read environment first, then Streamlit secrets, then defaults.

    Within each source, GOOGLE_API_KEY (the original app's name) precedes
    GEMINI_API_KEY. Empty strings and None are treated as missing values.
    Keys are preserved exactly: malformed keys are not silently repaired.
    Other settings use GEMINI_MODEL, SHEET_ID, and SHEET_GID.
    """
    sources = (os.environ if environ is None else environ, secrets or {})

    def read(*names: str, default: str = "") -> str:
        for source in sources:
            for name in names:
                value = source.get(name)
                if value is not None and str(value) != "":
                    return str(value)
        return default

    demo_url = read("DEMO_SITE_URL").strip()
    try:
        parsed = urlsplit(demo_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or any(char.isspace() for char in demo_url)):
            demo_url = ""
    except ValueError:
        demo_url = ""
    return Settings(
        api_key=read("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        model=read("GEMINI_MODEL", default=DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        sheet_id=read("SHEET_ID").strip(),
        sheet_gid=read("SHEET_GID", default="0").strip() or "0",
        demo_site_url=demo_url,
    )


def load_csv_bytes(content: bytes) -> pd.DataFrame:
    """Read a comma-delimited UTF-8/BOM or Korean CP949 sensor CSV.

    Column aliases, numeric types, timestamps, and sensor validity are handled
    by the analytics layer. This function preserves the supplied headers.
    """
    if not isinstance(content, bytes):
        raise DataLoadError("CSV 파일을 바이트 형식으로 전달해 주세요.")
    if len(content) > MAX_CSV_BYTES:
        raise DataLoadError("CSV 파일은 25MB 이하로 준비해 주세요.")
    if not content or not content.strip():
        raise DataLoadError("CSV 파일이 비어 있습니다. 센서 기록이 있는 파일을 선택해 주세요.")

    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            decoded = content.decode("cp949")
        except UnicodeDecodeError:
            raise DataLoadError("CSV 인코딩을 읽을 수 없습니다. UTF-8 또는 CP949로 저장해 주세요.") from None

    prefix = decoded.lstrip()[:1024].lower()
    if prefix.startswith(("<", "{", "[")) or "<html" in prefix or "\x00" in decoded:
        raise DataLoadError("CSV 대신 웹 페이지나 다른 파일 형식을 받았습니다. CSV 내보내기와 공유 설정을 확인해 주세요.")

    # Validate row widths first: pandas can otherwise interpret extra fields
    # as an implicit index, silently shifting the sensor readings.
    stream = io.StringIO(decoded)
    header = None
    row_count = 0
    try:
        for row in csv.reader(stream, strict=True):
            if not row or not any(value.strip() for value in row):
                continue
            if header is None:
                header = row
                names = [name.strip() for name in header]
                if len(names) < 2 or any(not name for name in names) or len(set(names)) != len(names):
                    raise DataLoadError("CSV 첫 줄에 중복 없는 열 이름과 쉼표 구분이 필요합니다.")
            elif len(row) != len(header):
                raise DataLoadError("CSV 행마다 열 개수가 다릅니다. 파일의 쉼표와 따옴표를 확인해 주세요.")
            else:
                row_count += 1
    except csv.Error:
        raise DataLoadError("CSV 형식을 읽을 수 없습니다. 파일의 쉼표와 따옴표를 확인해 주세요.") from None
    if header is None or row_count == 0:
        raise DataLoadError("CSV에 센서 기록이 없습니다. 열 이름 아래에 데이터가 필요합니다.")
    try:
        stream.seek(0)
        frame = pd.read_csv(stream, on_bad_lines="error")
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError):
        raise DataLoadError("CSV 형식을 읽을 수 없습니다. 센서 기록 파일을 다시 내보내 주세요.") from None
    if frame.empty:
        raise DataLoadError("CSV에 센서 기록이 없습니다.")
    return frame


def fetch_sheet(sheet_id: str, gid: str = "0", timeout: float = 15) -> pd.DataFrame:
    """Fetch only a Google Sheets CSV export, with bounded time and size.

    This reader uses an unauthenticated export URL. Private sheets requiring
    authentication must be exported locally and uploaded as CSV instead.
    """
    if not isinstance(sheet_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", sheet_id):
        raise DataLoadError("SHEET_ID에는 Google Sheets 주소 전체가 아닌 문서 ID만 입력해 주세요.")
    if not isinstance(gid, str) or not re.fullmatch(r"[0-9]{1,20}", gid):
        raise DataLoadError("SHEET_GID에는 시트 탭의 숫자 ID를 입력해 주세요.")
    if not isinstance(timeout, (int, float)) or not np.isfinite(timeout) or timeout <= 0:
        raise DataLoadError("데이터 연결 제한 시간은 0보다 큰 초 단위 숫자여야 합니다.")
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    request = Request(url, headers={"Accept": "text/csv", "User-Agent": "TalkingBasilPot/2.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" in content_type or "application/json" in content_type:
                raise DataLoadError("시트에서 CSV 대신 로그인 페이지를 받았습니다. 공유 설정을 확인하거나 CSV 파일을 업로드해 주세요.")
            try:
                declared_size = int(response.headers.get("Content-Length", "0"))
            except (TypeError, ValueError):
                declared_size = 0
            if declared_size > MAX_CSV_BYTES:
                raise DataLoadError("시트 내보내기가 25MB를 넘습니다. 필요한 기간만 CSV로 내보내 주세요.")
            content = response.read(MAX_CSV_BYTES + 1)
    except HTTPError as error:
        if error.code in (401, 403):
            message = "시트를 읽을 권한이 없습니다. 공유 설정을 확인하거나 CSV 파일을 업로드해 주세요."
        elif error.code == 404:
            message = "시트를 찾을 수 없습니다. 문서 ID와 탭 ID, 공유 설정을 확인해 주세요."
        elif error.code == 429:
            message = "시트 요청이 일시적으로 제한되었습니다. 잠시 후 갱신하거나 CSV 파일을 사용해 주세요."
        else:
            message = "Google Sheets에서 데이터를 받지 못했습니다. 잠시 후 갱신하거나 CSV 파일을 사용해 주세요."
        raise DataLoadError(message) from None
    except (URLError, TimeoutError, socket.timeout, OSError):
        raise DataLoadError("시트 연결에 실패했거나 시간이 초과되었습니다. 인터넷 연결을 확인하거나 CSV 파일을 사용해 주세요.") from None
    return load_csv_bytes(content)


def make_demo_frame(now=None, days: int = 14) -> pd.DataFrame:
    """Build reproducible synthetic readings, only when explicitly requested.

    There are 720 readings per day. Soil Moisture is an uncalibrated example
    ADC value, not a percentage or evidence of an actual watering event.
    Callers must label this data as synthetic wherever it is displayed.
    """
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
        raise ValueError("시연 데이터 기간은 1~365일의 정수여야 합니다.")
    end = pd.Timestamp(datetime.now(KST) if now is None else now).floor("2min")
    if pd.isna(end):
        raise ValueError("시연 데이터의 기준 시각이 올바르지 않습니다.")
    count = days * 720
    indexes = np.arange(count)
    daily_phase = 2 * np.pi * indexes / 720
    return pd.DataFrame(
        {
            "Timestamp": pd.date_range(end=end, periods=count, freq="2min"),
            "Temperature": np.round(24 + 3 * np.sin(daily_phase - np.pi / 2), 2),
            "Humidity": np.round(58 - 9 * np.sin(daily_phase - np.pi / 2), 2),
            "Soil Moisture": np.round(320 + 400 * (indexes % 1440) / 1440 + 8 * np.sin(daily_phase)).astype(int),
        }
    )
