"""PDF report generation for completed factor backtests."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont

from stock_common.backtest.pipeline import describe_plan
from stock_common.backtest.schemas import ChartArtifact, PipelineResult


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = ROOT / "output" / "pdf"
DEFAULT_ASSET_DIR = ROOT / "tmp" / "pdfs" / "backtest_reports"

FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path(r"C:\Windows\Fonts\simsun.ttc"),
)


class BacktestReportError(RuntimeError):
    """Raised when a backtest PDF report cannot be generated."""


@dataclass(frozen=True)
class BacktestReport:
    """Generated report metadata returned to web clients."""

    path: Path
    url: str
    filename: str
    title: str
    created_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "url": self.url,
            "filename": self.filename,
            "title": self.title,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class BacktestReportGenerator:
    """Create a compact PDF report from a completed PipelineResult."""

    output_dir: Path = DEFAULT_OUTPUT_DIR
    asset_dir: Path = DEFAULT_ASSET_DIR
    public_url_prefix: str = "/reports"
    max_chart_count: int = 4

    def generate(self, result: PipelineResult, *, task_text: str = "") -> BacktestReport:
        if result.plan is None or result.facts is None or result.metrics is None:
            raise BacktestReportError("backtest result is incomplete")

        try:
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_CENTER
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.lib.units import cm
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.platypus import (
                Image as PdfImage,
                PageBreak,
                Paragraph,
                SimpleDocTemplate,
                Spacer,
                Table,
                TableStyle,
            )
        except Exception as exc:  # pragma: no cover - depends on optional runtime package.
            raise BacktestReportError("reportlab is required to generate PDF reports") from exc

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.asset_dir.mkdir(parents=True, exist_ok=True)

        report_id = _safe_identifier(
            str(result.facts.metadata.get("run_id") or result.plan.plan_id)
        )
        filename = f"backtest_{report_id}.pdf"
        pdf_path = self.output_dir / filename
        title = "回测结果报告"
        created_at = datetime.now().replace(microsecond=0).isoformat()

        font_name = _register_pdf_font(pdfmetrics, UnicodeCIDFont, TTFont)
        styles = _pdf_styles(getSampleStyleSheet(), ParagraphStyle, font_name, colors, TA_CENTER)
        doc = SimpleDocTemplate(
            str(pdf_path),
            pagesize=A4,
            rightMargin=1.6 * cm,
            leftMargin=1.6 * cm,
            topMargin=1.35 * cm,
            bottomMargin=1.35 * cm,
            title=title,
            author="Finance Router",
        )

        story = [
            Paragraph(title, styles["title"]),
            Paragraph(_html(describe_plan(result.plan)), styles["body"]),
            Spacer(1, 0.25 * cm),
        ]
        if task_text:
            story.extend(
                [
                    Paragraph("用户请求", styles["heading"]),
                    Paragraph(_html(task_text), styles["body"]),
                    Spacer(1, 0.2 * cm),
                ]
            )

        story.extend(
            [
                Paragraph("核心指标", styles["heading"]),
                _metric_table(result.metrics.summary, styles, Paragraph, Table, TableStyle, colors),
                Spacer(1, 0.25 * cm),
                Paragraph("执行与数据", styles["heading"]),
                _metadata_table(result, styles, Paragraph, Table, TableStyle, colors),
                Spacer(1, 0.25 * cm),
            ]
        )

        chart_paths = _render_chart_images(
            result,
            output_dir=self.asset_dir / report_id,
            max_chart_count=self.max_chart_count,
        )
        if chart_paths:
            story.append(Paragraph("图表", styles["heading"]))
            page_width = A4[0] - doc.leftMargin - doc.rightMargin
            for index, chart_path in enumerate(chart_paths, start=1):
                image = PdfImage(str(chart_path))
                image.drawWidth = page_width
                image.drawHeight = min(page_width * 0.40, 8.3 * cm)
                story.append(image)
                story.append(Paragraph(f"图 {index}：{_html(chart_path.stem)}", styles["caption"]))
                story.append(Spacer(1, 0.18 * cm))
                if index == 2 and len(chart_paths) > 2:
                    story.append(PageBreak())

        warnings = tuple(result.facts.warnings) + tuple(result.metrics.warnings)
        if warnings:
            story.append(Paragraph("提示与风险", styles["heading"]))
            for warning in warnings[:8]:
                message = str(warning.get("message") or warning.get("code") or "")
                if message:
                    story.append(Paragraph("• " + _html(message), styles["small"]))
            story.append(Spacer(1, 0.2 * cm))
        story.append(
            Paragraph(
                "本报告由本地回测流程自动生成，仅用于策略研究和系统验证，不构成投资建议。",
                styles["small"],
            )
        )

        doc.build(story)
        return BacktestReport(
            path=pdf_path,
            url=f"{self.public_url_prefix}/{filename}",
            filename=filename,
            title=title,
            created_at=created_at,
        )


def _metric_table(
    summary: dict[str, Any],
    styles: dict[str, Any],
    Paragraph: Any,
    Table: Any,
    TableStyle: Any,
    colors: Any,
) -> Any:
    performance = summary.get("performance", {})
    risk = summary.get("risk", {})
    trading = summary.get("trading", {})
    benchmark = summary.get("benchmark", {})
    rows = [
        ("指标", "数值", "说明"),
        ("年化收益", _percent(performance.get("annual_return")), "策略净值年化收益"),
        ("年化超额", _percent(performance.get("annual_excess_return")), "相对基准年化收益差"),
        ("最大回撤", _percent(risk.get("max_drawdown")), "策略净值峰谷跌幅"),
        ("Sharpe", _number(risk.get("sharpe_ratio")), "年化风险调整收益"),
        ("信息比率", _number(benchmark.get("information_ratio")), "超额收益与跟踪误差之比"),
        ("年换手率", _percent(trading.get("annual_turnover")), "按日换手折算的年化值"),
        ("交易笔数", _integer(trading.get("total_transactions")), "成交记录数量"),
    ]
    return _styled_table(rows, styles, Paragraph, Table, TableStyle, colors)


def _metadata_table(
    result: PipelineResult,
    styles: dict[str, Any],
    Paragraph: Any,
    Table: Any,
    TableStyle: Any,
    colors: Any,
) -> Any:
    assert result.plan is not None
    assert result.facts is not None
    metadata = result.facts.metadata
    quality = result.facts.data_quality
    rows = [
        ("项目", "值"),
        ("计划 ID", result.plan.plan_id),
        ("策略 Hash", result.plan.strategy_hash[:16]),
        ("回测区间", f"{metadata.get('start_date')} 至 {metadata.get('end_date')}"),
        ("基准", str(metadata.get("benchmark_code", result.plan.backtest.benchmark))),
        ("股票池", result.plan.universe.display_name or result.plan.universe.name),
        ("调仓频率", result.plan.rebalance.frequency.value),
        ("实际交易日", str(quality.get("actual_trade_days", ""))),
        ("数据源", str(quality.get("data_source", metadata.get("data_version", "")))),
    ]
    return _styled_table(rows, styles, Paragraph, Table, TableStyle, colors)


def _styled_table(
    rows: list[tuple[Any, ...]],
    styles: dict[str, Any],
    Paragraph: Any,
    Table: Any,
    TableStyle: Any,
    colors: Any,
) -> Any:
    data = [[Paragraph(_html(str(cell)), styles["small"]) for cell in row] for row in rows]
    table = Table(data, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e0f2fe")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _render_chart_images(
    result: PipelineResult,
    *,
    output_dir: Path,
    max_chart_count: int,
) -> list[Path]:
    if result.charts is None:
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    preferred_ids = (
        "nav_curve",
        "drawdown_curve",
        "monthly_return_heatmap",
        "annual_return_comparison",
    )
    chart_by_id = {chart.chart_id: chart for chart in result.charts.charts}
    paths = []
    for chart_id in preferred_ids:
        chart = chart_by_id.get(chart_id)
        if chart is None or chart.status == "empty" or not chart.data:
            continue
        path = output_dir / f"{len(paths) + 1:02d}_{_safe_identifier(chart.title or chart.chart_id)}.png"
        _render_chart(chart, path)
        paths.append(path)
        if len(paths) >= max_chart_count:
            break
    return paths


def _render_chart(chart: ChartArtifact, path: Path) -> None:
    if chart.chart_id in {"nav_curve", "drawdown_curve"}:
        fields = (
            ("strategy_nav", "策略净值", "#0b7f63"),
            ("benchmark_nav", "基准净值", "#2563eb"),
            ("excess_nav", "超额净值", "#7c3aed"),
        )
        if chart.chart_id == "drawdown_curve":
            fields = (
                ("strategy_drawdown", "策略回撤", "#b42318"),
                ("benchmark_drawdown", "基准回撤", "#2563eb"),
            )
        _render_line_chart(chart, path, fields)
        return
    if chart.chart_id == "monthly_return_heatmap":
        _render_heatmap(chart, path)
        return
    if chart.chart_id == "annual_return_comparison":
        _render_annual_bars(chart, path)
        return
    _render_empty_chart(chart, path)


def _base_chart(title: str, width: int = 1500, height: int = 560) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width - 1, height - 1), outline="#d9e2ec", width=2)
    draw.text((38, 24), title, fill="#14213d", font=_pil_font(34))
    return image, draw


def _render_line_chart(
    chart: ChartArtifact,
    path: Path,
    fields: tuple[tuple[str, str, str], ...],
) -> None:
    rows = chart.data
    image, draw = _base_chart(chart.title)
    width, height = image.size
    pad = (78, 92, 44, 78)
    series = []
    for field, label, color in fields:
        points = [
            (index, float(row[field]))
            for index, row in enumerate(rows)
            if isinstance(row.get(field), (int, float)) and math.isfinite(float(row[field]))
        ]
        if len(points) > 1:
            series.append((field, label, color, points))
    if not series:
        return _render_empty_chart(chart, path)
    values = [value for *_unused, points in series for _index, value in points]
    min_v = min(values)
    max_v = max(values)
    span = max(max_v - min_v, abs(max_v), 1e-9)
    x1, y1, x2, y2 = pad[0], pad[1], width - pad[2], height - pad[3]
    draw.line((x1, y2, x2, y2), fill="#cbd5e1", width=2)
    draw.line((x1, y1, x1, y2), fill="#cbd5e1", width=2)
    max_index = max(len(rows) - 1, 1)
    for _field, label, color, points in series:
        xy = [
            (
                x1 + index / max_index * (x2 - x1),
                y1 + (max_v - value) / span * (y2 - y1),
            )
            for index, value in points
        ]
        draw.line(xy, fill=color, width=4, joint="curve")
    _draw_legend(draw, [(label, color) for _field, label, color, _points in series], x1, height - 48)
    draw.text((18, y1 - 8), _compact(max_v), fill="#64748b", font=_pil_font(22))
    draw.text((18, y2 - 14), _compact(min_v), fill="#64748b", font=_pil_font(22))
    image.save(path)


def _render_heatmap(chart: ChartArtifact, path: Path) -> None:
    rows = [row for row in chart.data if isinstance(row.get("monthly_return"), (int, float))]
    image, draw = _base_chart(chart.title)
    if not rows:
        return _render_empty_chart(chart, path)
    years = sorted({int(row["year"]) for row in rows})
    lookup = {(int(row["year"]), int(row["month"])): float(row["monthly_return"]) for row in rows}
    max_abs = max(abs(value) for value in lookup.values()) or 0.01
    font = _pil_font(22)
    left, top, cell = 160, 120, 64
    for month in range(1, 13):
        draw.text((left + (month - 1) * cell + 20, top - 34), str(month), fill="#64748b", font=font)
    for row_index, year in enumerate(years):
        y = top + row_index * cell
        draw.text((54, y + 18), str(year), fill="#334155", font=font)
        for month in range(1, 13):
            value = lookup.get((year, month))
            x = left + (month - 1) * cell
            draw.rounded_rectangle(
                (x, y, x + cell - 8, y + cell - 8),
                radius=8,
                fill=_heat_color(value, max_abs),
                outline="#ffffff",
                width=2,
            )
    image.save(path)


def _render_annual_bars(chart: ChartArtifact, path: Path) -> None:
    rows = [
        row for row in chart.data if isinstance(row.get("strategy_annual_return"), (int, float))
    ]
    image, draw = _base_chart(chart.title)
    if not rows:
        return _render_empty_chart(chart, path)
    width, height = image.size
    x1, y1, x2, y2 = 92, 100, width - 54, height - 92
    values = []
    for row in rows:
        values.append(float(row.get("strategy_annual_return") or 0))
        values.append(float(row.get("benchmark_annual_return") or 0))
    min_v = min(min(values), 0)
    max_v = max(max(values), 0)
    span = max(max_v - min_v, 1e-9)
    base_y = y1 + (max_v - 0) / span * (y2 - y1)
    draw.line((x1, base_y, x2, base_y), fill="#94a3b8", width=2)
    band = (x2 - x1) / max(len(rows), 1)
    bar_w = min(28, band / 4)
    for index, row in enumerate(rows):
        cx = x1 + band * index + band / 2
        for offset, field, color in (
            (-bar_w - 3, "strategy_annual_return", "#0b7f63"),
            (3, "benchmark_annual_return", "#2563eb"),
        ):
            value = float(row.get(field) or 0)
            y = y1 + (max_v - value) / span * (y2 - y1)
            draw.rectangle((cx + offset, min(y, base_y), cx + offset + bar_w, max(y, base_y)), fill=color)
        draw.text((cx - 24, y2 + 14), str(row.get("year", "")), fill="#64748b", font=_pil_font(20))
    _draw_legend(draw, [("策略", "#0b7f63"), ("基准", "#2563eb")], x1, height - 44)
    image.save(path)


def _render_empty_chart(chart: ChartArtifact, path: Path) -> None:
    image, draw = _base_chart(chart.title)
    draw.text((60, 250), "无可展示数据", fill="#64748b", font=_pil_font(30))
    image.save(path)


def _draw_legend(
    draw: ImageDraw.ImageDraw,
    items: list[tuple[str, str]],
    x: int,
    y: int,
) -> None:
    font = _pil_font(22)
    cursor = x
    for label, color in items:
        draw.ellipse((cursor, y, cursor + 18, y + 18), fill=color)
        draw.text((cursor + 26, y - 4), label, fill="#475569", font=font)
        cursor += 150


def _register_pdf_font(pdfmetrics: Any, UnicodeCIDFont: Any, TTFont: Any) -> str:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    for candidate in FONT_CANDIDATES:
        if not candidate.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont("BacktestChinese", str(candidate)))
            return "BacktestChinese"
        except Exception:
            continue
    return "STSong-Light"


def _pdf_styles(
    base_styles: Any,
    ParagraphStyle: Any,
    font_name: str,
    colors: Any,
    TA_CENTER: Any,
) -> dict[str, Any]:
    return {
        "title": ParagraphStyle(
            "title",
            parent=base_styles["Title"],
            fontName=font_name,
            fontSize=22,
            leading=30,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#14213d"),
            spaceAfter=12,
        ),
        "heading": ParagraphStyle(
            "heading",
            parent=base_styles["Heading2"],
            fontName=font_name,
            fontSize=14,
            leading=20,
            textColor=colors.HexColor("#1e3a8a"),
            spaceBefore=5,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base_styles["BodyText"],
            fontName=font_name,
            fontSize=10,
            leading=16,
            textColor=colors.HexColor("#172033"),
            spaceAfter=7,
        ),
        "small": ParagraphStyle(
            "small",
            parent=base_styles["BodyText"],
            fontName=font_name,
            fontSize=8.8,
            leading=13,
            textColor=colors.HexColor("#475569"),
            spaceAfter=4,
        ),
        "caption": ParagraphStyle(
            "caption",
            parent=base_styles["BodyText"],
            fontName=font_name,
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor("#64748b"),
            alignment=TA_CENTER,
            spaceAfter=5,
        ),
    }


def _pil_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def _heat_color(value: float | None, max_abs: float) -> str:
    if value is None:
        return "#eef2f7"
    intensity = min(abs(value) / max_abs, 1.0)
    if value >= 0:
        return (
            f"rgb({round(230 - intensity * 116)},"
            f"{round(206 - intensity * 76)},"
            f"{round(214 - intensity * 118)})"
        )
    return (
        f"rgb({round(245 - intensity * 66)},"
        f"{round(216 - intensity * 102)},"
        f"{round(210 - intensity * 96)})"
    )


def _safe_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return cleaned or "report"


def _html(value: str) -> str:
    return escape(value).replace("\n", "<br/>")


def _percent(value: object) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "N/A"
    return f"{float(value) * 100:.2f}%"


def _number(value: object) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "N/A"
    return f"{float(value):.2f}"


def _integer(value: object) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "N/A"
    return str(int(value))


def _compact(value: float) -> str:
    if abs(value) < 1:
        return _percent(value)
    return _number(value)
