"""Render the read-only exhibition payload as a Korean tablet PDF.

Uses only the supplied JSON; never fetches or fabricates live measurements.
Run with the Codex bundled Python (reportlab) or another reportlab installation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import html
import json
import math
import re
from pathlib import Path
import shutil

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = landscape(A4)
MARGIN = 42
CONTENT = WIDTH - 2 * MARGIN
INK = colors.HexColor("#173B2B")
MUTED = colors.HexColor("#62736B")
GREEN = colors.HexColor("#23734C")
PALE = colors.HexColor("#E8EFE7")
PAPER = colors.HexColor("#F6F5F0")
BORDER = colors.HexColor("#D8E1D7")
ORANGE = colors.HexColor("#976323")
WHITE = colors.white
SERIES_COLORS = [colors.HexColor("#32816B"), colors.HexColor("#387D9B"), colors.HexColor("#B28743")]
KST = timezone(timedelta(hours=9))


def clean(value):
    text = str(value if value is not None else "").replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")
    # Malgun does not contain emoji; omit decoration instead of printing tofu boxes.
    return re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]", "", text)


def source_label(value):
    def replace(match):
        parts = re.split(r"[:/#]", match.group(0))
        topics = {"sunlight": "햇빛", "temperature": "온도", "watering": "물주기",
                  "humidity": "습도", "soil": "토양"}
        label = next((label for topic, label in topics.items() if topic in parts), "관리 방법")
        return "등록된 바질 재배 안내 - " + label
    return re.sub(r"basil_knowledge\.json(?:#|::)[\w/:.-]+", replace, clean(value))


def compact_answer(value):
    # Local answers use chat line breaks. Keep every word, but use PDF paragraphs.
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", source_label(value), flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return " ".join(text.split())


def number(value):
    if value is None:
        return "자료 없음"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            return "자료 없음"
        return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"
    return clean(value)


def stamp(value, include_time=False):
    if not value:
        return "확인되지 않음"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(KST)
        return parsed.strftime("%Y.%m.%d %H:%M" if include_time else "%Y.%m.%d")
    except (ValueError, TypeError):
        return clean(value)


def register_fonts(font_dir):
    directory = Path(font_dir)
    for name, filename in (("Malgun", "malgun.ttf"), ("MalgunBold", "malgunbd.ttf")):
        path = directory / filename
        if not path.is_file():
            raise FileNotFoundError(f"Korean font is required: {path}")
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path)))
    pdfmetrics.registerFontFamily("Malgun", normal="Malgun", bold="MalgunBold")


class Guide:
    def __init__(self, payload, path):
        self.data = payload
        self.provenance = payload.get("provenance", {})
        self.kind = self.provenance.get("kind", "none")
        if self.kind not in {"actual", "synthetic", "none"}:
            raise ValueError("provenance.kind must be actual, synthetic, or none")
        if self.kind == "actual" and not self.provenance.get("source_name"):
            raise ValueError("Actual records require an explicitly named source")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.pdf = canvas.Canvas(str(self.path), pagesize=(WIDTH, HEIGHT), pageCompression=1)
        self.pdf.setTitle("쫑알쫑알 화분 - 오프라인 관찰 안내")
        self.pdf.setAuthor("Talking Basil Pot")
        self.pdf.setSubject("과거 센서 기록과 실제 식물 관찰을 구분하는 전시 안내")
        self.page = 0
        questions = payload.get("guided_qa", [])
        # The tablet PDF is a selection. Long trend reports stay in the full HTML.
        selected = sorted(range(len(questions)), key=lambda i: len(compact_answer(questions[i].get("answer"))))[:4]
        self.faq_items = [questions[i] for i in sorted(selected)]
        self.total_pages = 5 if len(self.faq_items) <= 2 else 6
        self.badge = {"actual": "실제 과거 기록", "synthetic": "합성 예시 기록", "none": "실제 기록 미확정"}[self.kind]
        self.warning = {
            "actual": "저장된 과거 기록입니다. 지금 눈앞의 화분 상태를 뜻하지 않습니다.",
            "synthetic": "합성 예시입니다. 실제 화분에서 측정한 기록이 아닙니다.",
            "none": "실제 기록이 확정되지 않아 측정 수치와 그래프를 제공하지 않습니다.",
        }[self.kind]

    def paragraph(self, text, x, top, width, size=13, *, bold=False, color=INK,
                  max_height=None, min_size=11, leading=None):
        escaped = html.escape(clean(text)).replace("\n", "<br/>")
        chosen = size
        while True:
            style = ParagraphStyle("text", fontName="MalgunBold" if bold else "Malgun",
                                   fontSize=chosen, leading=leading or chosen * 1.42,
                                   textColor=color, wordWrap="CJK", alignment=TA_LEFT,
                                   spaceAfter=0, spaceBefore=0)
            para = Paragraph(escaped, style)
            _, height = para.wrap(width, HEIGHT)
            if max_height is None or height <= max_height:
                break
            chosen -= 0.35
            if chosen < min_size:
                raise ValueError(f"Text does not fit page {self.page}: {clean(text)[:100]}")
        para.drawOn(self.pdf, x, HEIGHT - top - height)
        return top + height

    def card(self, x, top, width, height, fill=WHITE):
        self.pdf.setFillColor(fill)
        self.pdf.setStrokeColor(BORDER)
        self.pdf.setLineWidth(0.7)
        self.pdf.roundRect(x, HEIGHT - top - height, width, height, 13, fill=1, stroke=1)

    def new_page(self, title=None, subtitle=None):
        if self.page:
            self.pdf.showPage()
        self.page += 1
        self.pdf.setFillColor(PAPER)
        self.pdf.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)
        self.paragraph("TALKING BASIL POT  /  FIELD GUIDE", MARGIN, 21, 410, 9, bold=True, color=GREEN)
        pill_width = 143
        self.card(WIDTH - MARGIN - pill_width, 18, pill_width, 25, PALE)
        self.paragraph(self.badge, WIDTH - MARGIN - pill_width + 13, 22, pill_width - 20, 10.2, bold=True, color=GREEN)
        if title:
            self.paragraph(title, MARGIN, 62, CONTENT, 25, bold=True)
        if subtitle:
            self.paragraph(subtitle, MARGIN, 100, CONTENT, 11, color=MUTED, max_height=33)
        self.pdf.setStrokeColor(BORDER)
        self.pdf.line(MARGIN, 43, WIDTH - MARGIN, 43)
        self.paragraph(self.warning, MARGIN, HEIGHT - 34, CONTENT - 48, 9.3, color=MUTED)
        self.paragraph(f"{self.page:02d} / {self.total_pages:02d}", WIDTH - MARGIN - 45, HEIGHT - 34, 45, 9.3, color=MUTED)

    def cover(self):
        self.new_page()
        self.paragraph("쫑알쫑알\n화분", MARGIN, 77, 350, 43, bold=True, leading=53)
        self.paragraph("센서 기록을 읽고,\n눈앞의 식물을 관찰해요.", MARGIN, 205, 450, 21, color=GREEN, leading=31)
        self.paragraph("오프라인 태블릿 안내서", MARGIN, 289, 430, 12.5, bold=True, color=MUTED)
        self.plant_mark(630, 164)
        self.card(MARGIN, 335, CONTENT, 91, PALE)
        self.paragraph(self.warning, MARGIN + 20, 350, CONTENT - 40, 15, bold=True, max_height=45)
        self.paragraph(self.provenance.get("notice") or "관측 사실과 해석을 구분하고, 기록되지 않은 시간의 상태는 추측하지 않습니다.",
                       MARGIN + 20, 389, CONTENT - 40, 10.6, color=MUTED, max_height=28, min_size=10.3)
        overview = self.data.get("overview", {})
        count = number(overview.get("record_count")) if self.kind != "none" else "미확정"
        period = (f"{stamp(overview.get('period_start'))} - {stamp(overview.get('period_end'))}"
                  if self.kind != "none" else "사용자 확인 후 제공")
        fields = [("수록한 측정 기록", count + ("개" if isinstance(overview.get("record_count"), (int, float)) and self.kind != "none" else "")),
                  ("기록 기간", period),
                  ("마지막 측정", stamp(self.provenance.get("last_record_at"), True) if self.kind != "none" else "확인되지 않음")]
        widths = [205, 287, CONTENT - 516]
        x = MARGIN
        for (label, value), width in zip(fields, widths):
            self.paragraph(label, x, 450, width, 10.8, color=MUTED)
            self.paragraph(value, x, 474, width, 16 if label != "기록 기간" else 14, bold=True, max_height=49, min_size=12)
            x += width + 12
        source = self.provenance.get("source_name") or "실제 데이터 출처 미확정"
        self.paragraph(f"자료 출처: {source}   |   안내서 생성: {stamp(self.data.get('exported_at'), True)} KST",
                       MARGIN, 533, CONTENT, 9.1, color=MUTED, max_height=18, min_size=8.5)

    def plant_mark(self, x, top):
        c = self.pdf
        cy = HEIGHT - top
        c.setFillColor(PALE)
        c.circle(x, cy - 5, 81, fill=1, stroke=0)
        c.setStrokeColor(GREEN)
        c.setLineWidth(4)
        c.line(x, cy - 46, x + 2, cy + 49)
        for dx, dy, flip in [(-34, 14, 1), (32, 38, -1), (-24, 56, 1)]:
            p = c.beginPath()
            p.moveTo(x, cy + dy - 15)
            p.curveTo(x + dx * 0.1, cy + dy + 23, x + dx * 1.5, cy + dy + 25, x + dx, cy + dy)
            p.curveTo(x + dx * 0.8, cy + dy - 20, x + dx * 0.2, cy + dy - 20, x, cy + dy - 15)
            c.setFillColor(GREEN if flip > 0 else colors.HexColor("#62996C"))
            c.drawPath(p, fill=1, stroke=0)
        p = c.beginPath()
        p.moveTo(x - 36, cy - 35)
        p.lineTo(x + 36, cy - 35)
        p.lineTo(x + 25, cy - 88)
        p.lineTo(x - 25, cy - 88)
        p.close()
        c.setFillColor(colors.HexColor("#C8A57F"))
        c.drawPath(p, fill=1, stroke=0)
        self.paragraph("TALKING BASIL", x - 67, top + 112, 150, 11.5, bold=True, color=GREEN)

    def charts(self):
        self.new_page("세 가지 센서, 서로 다른 이야기",
                      "선: 구간의 관측값 평균 / 옅은 세로선: 최솟값-최댓값 / 공백 구간은 연결하지 않아요.")
        charts = self.data.get("charts", []) if self.kind != "none" else []
        for index in range(3):
            top = 145 + index * 127
            chart = charts[index] if index < len(charts) else {"label": ["온도", "공기 습도", "토양 센서"][index]}
            self.chart(chart, top, SERIES_COLORS[index])
        self.paragraph("토양 센서값은 보정 전 원시값입니다. 공기 습도와 같은 퍼센트로 읽거나 물주기 필요를 단정하지 않아요.",
                       MARGIN, 532, CONTENT, 10.2, color=MUTED, max_height=18)

    def chart(self, chart, top, color):
        self.card(MARGIN, top, CONTENT, 117)
        label = clean(chart.get("label") or chart.get("id") or "센서")
        unit = clean(chart.get("unit") or "")
        if "토양" in label or "soil" in clean(chart.get("id")).lower():
            unit = "원시값"
        self.paragraph(label + (f" ({unit})" if unit else ""), MARGIN + 15, top + 10, 210, 12.7, bold=True, color=color)
        points = []
        for point in chart.get("points", []):
            try:
                value = float(point["mean"])
                at = datetime.fromisoformat(str(point["t"]).replace("Z", "+00:00"))
                at = at.replace(tzinfo=KST) if at.tzinfo is None else at.astimezone(KST)
                if not math.isfinite(value):
                    continue
                low = value if point.get("min") is None else float(point["min"])
                high = value if point.get("max") is None else float(point["max"])
                if not math.isfinite(low) or not math.isfinite(high):
                    continue
                points.append((at.timestamp(), value, low, high, point))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        points.sort(key=lambda item: item[0])
        if not points:
            self.paragraph("확인된 기록이 없어 그래프를 만들지 않았습니다.", MARGIN + 220, top + 49, CONTENT - 250, 12.5, color=MUTED)
            return
        left, right = MARGIN + 236, WIDTH - MARGIN - 18
        bottom, plot_top = HEIGHT - top - 88, HEIGHT - top - 18
        xmin, xmax = points[0][0], points[-1][0]
        ymin, ymax = min(point[2] for point in points), max(point[3] for point in points)
        pad = max((ymax - ymin) * 0.12, 0.5)
        ymin, ymax = ymin - pad, ymax + pad
        def px(value):
            return left + (value - xmin) / (xmax - xmin) * (right-left) if xmax != xmin else (left+right)/2
        def py(value):
            return bottom + (value - ymin) / (ymax - ymin) * (plot_top-bottom)
        c = self.pdf
        for fraction in (0, 0.5, 1):
            y = bottom + fraction * (plot_top-bottom)
            c.setStrokeColor(BORDER)
            c.setLineWidth(0.4)
            c.line(left, y, right, y)
            self.paragraph(number(ymin + fraction * (ymax-ymin)), left - 54, HEIGHT-y-5, 47, 8.8, color=MUTED)
        previous = None
        c.setLineWidth(1.5)
        for at, value, low, high, point in points:
            x = px(at)
            c.setStrokeColor(colors.Color(color.red, color.green, color.blue, alpha=0.24))
            c.setLineWidth(1)
            c.line(x, py(low), x, py(high))
            connected = previous is not None and not point.get("break_before") and not point.get("contains_gap") and not previous[4].get("contains_gap")
            if connected:
                c.setStrokeColor(color)
                c.setLineWidth(1.35)
                c.line(px(previous[0]), py(previous[1]), x, py(value))
            if len(points) < 100 or not connected:
                c.setFillColor(color)
                c.circle(x, py(value), 1.6, fill=1, stroke=0)
            previous = (at, value, low, high, point)
        for value, x in [(xmin, left), ((xmin+xmax)/2, (left+right)/2), (xmax, right)]:
            text = datetime.fromtimestamp(value, KST).strftime("%y.%m.%d")
            self.paragraph(text, x - 24, top + 93, 55, 8.8, color=MUTED)
        gap_count = sum(bool(point[4].get("contains_gap") or point[4].get("break_before")) for point in points)
        self.paragraph(f"요약점 {len(points):,}개" + (f"\n공백 표시 {gap_count:,}개" if gap_count else ""),
                       MARGIN + 15, top + 43, 145, 10.8, color=MUTED, max_height=52)

    def spotlights(self):
        self.new_page("대표 기간에서 발견한 기록", "날짜 범위와 측정 단위를 먼저 확인하고, 관측 사실과 원인 추정을 구분해요.")
        items = self.data.get("spotlights", [])[:3] if self.kind != "none" else []
        if not items:
            self.card(MARGIN, 153, CONTENT, 326)
            self.paragraph("비교할 실제 기록이 아직 확정되지 않았어요.", MARGIN + 26, 184, CONTENT - 52, 23, bold=True)
            self.paragraph("확인되지 않은 숫자를 채워 넣지 않았습니다. 뒤쪽 탐구 미션에서 실제 화분을 관찰하고, 기록의 시각과 센서 단위를 읽는 방법을 먼저 살펴보세요.",
                           MARGIN + 26, 244, CONTENT - 90, 16, max_height=120)
            return
        for index, item in enumerate(items):
            top = 145 + index * 129
            self.card(MARGIN, top, CONTENT, 119)
            self.paragraph(item.get("title") or f"기록 {index+1}", MARGIN + 17, top + 12, 490, 16, bold=True, max_height=29)
            self.paragraph(item.get("period") or "기간은 원자료를 확인해 주세요.", MARGIN + 17, top + 40, 485, 10, color=MUTED, max_height=24, min_size=9.4)
            self.paragraph(item.get("summary") or "이 기간의 요약이 제공되지 않았습니다.", MARGIN + 17, top + 64, 485, 12, max_height=44, min_size=10.4)
            facts = item.get("facts", [])[:3]
            for j, fact in enumerate(facts):
                self.paragraph(fact.get("label") or "기록", MARGIN + 533, top + 12 + j*33, CONTENT - 550, 9.6, color=MUTED, max_height=15)
                value = number(fact.get("value")) + (" " + clean(fact.get("unit")) if fact.get("unit") else "")
                self.paragraph(value, MARGIN + 533, top + 26 + j*33, CONTENT - 550, 13, bold=True, max_height=20, min_size=10.2)
        self.paragraph("요약은 저장된 측정값을 설명합니다. 물주기, 고장, 생장 변화의 원인이 확인되었다는 뜻은 아닙니다.",
                       MARGIN, 537, CONTENT, 9.8, color=MUTED, max_height=18)

    def missions(self):
        self.new_page("화면을 잠깐 내려놓고, 직접 관찰해요", "관찰한 모습과 센서가 남긴 숫자가 어떻게 다른지 함께 이야기해 보세요.")
        missions = self.data.get("missions", [])[:3]
        width = (CONTENT - 26) / 3
        for index in range(3):
            x = MARGIN + index * (width + 13)
            top = 147
            self.card(x, top, width, 372)
            item = missions[index] if index < len(missions) else {}
            self.paragraph(f"MISSION 0{index+1}", x + 16, top + 16, width - 32, 10, bold=True, color=GREEN)
            title = re.sub(r"^\d+\.\s*", "", clean(item.get("title") or ["바질을 관찰해요", "기록의 시간을 찾아요", "센서 단위를 읽어요"][index]))
            title = title.replace(" 구분해요", "\n구분해요")
            self.paragraph(title, x + 16, top + 42, width - 32, 17, bold=True, max_height=67, min_size=15)
            self.paragraph("직접 해 보기", x + 16, top + 118, width - 32, 10.6, bold=True, color=GREEN)
            self.paragraph(item.get("observe_prompt") or "화분과 기록에서 실제로 확인할 수 있는 사실을 찾아보세요.",
                           x + 16, top + 142, width - 32, 12.5, max_height=101, min_size=11.5)
            self.paragraph("함께 생각할 질문", x + 16, top + 259, width - 32, 10.6, bold=True, color=GREEN)
            self.paragraph(item.get("question") or "지금 확인한 사실과 아직 모르는 것을 구분할 수 있을까요?",
                           x + 16, top + 283, width - 32, 13, max_height=78, min_size=11.8)
        self.paragraph("눈으로 본 모습은 관찰 사실이에요. 그것만으로 식물의 감정이나 건강 상태를 확정하지 않아요.",
                       MARGIN, 534, CONTENT, 10.2, color=MUTED, max_height=20)

    def faqs(self):
        items = self.faq_items
        if not items:
            items = [
                {"question": "이 안내에서 지금의 식물 상태를 알 수 있나요?",
                 "answer": "이 문서는 저장된 기록을 읽는 오프라인 안내입니다. 현재 상태를 알려면 새 센서 측정이나 눈앞의 화분 관찰이 필요합니다. 실제 기록이 확정되지 않은 경우에는 수치와 그래프를 제공하지 않습니다.", "sources": ["전시 안내 원칙"]},
                {"question": "기록이 없는 시간은 어떻게 읽으면 될까요?",
                 "answer": "기록 공백의 상태를 이어 붙이거나 추측하지 않습니다. 공기 습도와 보정 전 토양 센서값은 서로 다른 측정 대상과 단위입니다. 숫자와 함께 측정 시각과 출처를 확인해 주세요.", "sources": ["전시 안내 원칙"]},
            ]
        for offset in range(0, len(items), 2):
            self.new_page("기록을 읽으며 묻는 대표 질문" + ("  /  이어서" if offset else ""),
                          "저장된 안내 중 대표 질문을 골랐어요. 오프라인에서는 새 AI 대화나 새 측정이 실행되지 않아요.")
            for index, item in enumerate(items[offset:offset+2]):
                top = 147 + index * 187
                self.card(MARGIN, top, CONTENT, 174)
                question_bottom = self.paragraph("Q. " + clean(item.get("question")), MARGIN + 20, top + 14, CONTENT - 40, 16, bold=True, max_height=44, min_size=14)
                sources = []
                for source in item.get("sources", [])[:3]:
                    if isinstance(source, dict):
                        source = source.get("label") or source.get("title") or source.get("source") or source.get("tool") or source.get("id")
                    if source:
                        label = source_label(source)
                        for name, friendly in {
                            "get_current_status": "마지막 측정",
                            "get_period_summary": "기간 요약",
                            "get_range_summary": "날짜 범위 요약",
                            "compare_periods": "기간 비교",
                            "get_daily_trend": "일별 기록",
                            "get_weekly_trend": "주별 기록",
                            "detect_events": "공백과 변화 확인",
                            "consult_basil_guide": "바질 재배 안내",
                        }.items():
                            label = label.replace(name, friendly)
                        sources.append(label)
                source_top = top + 147
                self.paragraph(compact_answer(item.get("answer")) or "저장된 답변이 없습니다.", MARGIN + 20, question_bottom + 10,
                               CONTENT - 40, 12.6, max_height=source_top - question_bottom - 16, min_size=10.6)
                if sources:
                    self.paragraph("근거: " + " / ".join(sources), MARGIN + 20, source_top, CONTENT - 40, 9.6, color=MUTED, max_height=20, min_size=8.8)

    def render(self):
        self.cover()
        self.charts()
        self.spotlights()
        self.missions()
        self.faqs()
        self.pdf.save()
        return self.page


def render_payload(payload_path, output_path, copy_path=None, font_dir="C:/Windows/Fonts"):
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8-sig"))
    if payload.get("schema_version") != "1.0":
        raise ValueError("Expected exhibition payload schema_version 1.0")
    register_fonts(font_dir)
    guide = Guide(payload, output_path)
    pages = guide.render()
    if copy_path:
        copy_path = Path(copy_path)
        copy_path.parent.mkdir(parents=True, exist_ok=True)
        if Path(output_path).resolve() != copy_path.resolve():
            shutil.copy2(output_path, copy_path)
    return {"pdf": str(Path(output_path).resolve()), "copy": str(Path(copy_path).resolve()) if copy_path else None,
            "pages": pages, "provenance_kind": guide.kind, "input": str(Path(payload_path).resolve())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "artifacts/exhibition/data.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/exhibition/offline-guide.pdf")
    parser.add_argument("--copy-output", type=Path, default=ROOT / "output/pdf/plant-offline-guide.pdf")
    parser.add_argument("--font-dir", default="C:/Windows/Fonts")
    args = parser.parse_args(argv)
    report = render_payload(args.input, args.output, args.copy_output, args.font_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
