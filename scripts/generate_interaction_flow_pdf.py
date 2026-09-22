"""Generate a Chinese PDF document for the Finance Router interaction flow."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image as PdfImage,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "pdf"
TMP_DIR = ROOT / "tmp" / "pdfs" / "finance_router_interaction_flow_assets"
PDF_PATH = OUT_DIR / "finance_router_interaction_flow.pdf"

FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path(r"C:\Windows\Fonts\simsun.ttc"),
)


@dataclass(frozen=True)
class Node:
    key: str
    label: str
    x: float
    y: float
    w: float
    h: float
    kind: str = "process"


@dataclass(frozen=True)
class Edge:
    start: str
    end: str
    label: str = ""


@dataclass(frozen=True)
class Diagram:
    slug: str
    title: str
    caption: str
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    notes: tuple[str, ...] = ()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    register_pdf_fonts()
    image_paths = render_diagrams(build_diagrams())
    build_pdf(image_paths)
    print(PDF_PATH)


def register_pdf_fonts() -> None:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    for candidate in FONT_CANDIDATES:
        if not candidate.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont("DocChinese", str(candidate)))
            return
        except Exception:
            continue


def pdf_font() -> str:
    return "DocChinese" if "DocChinese" in pdfmetrics.getRegisteredFontNames() else "STSong-Light"


def pil_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def build_diagrams() -> tuple[Diagram, ...]:
    return (
        Diagram(
            slug="01_system_overview",
            title="图 1：系统总览",
            caption=(
                "该图展示从浏览器到本地 HTTP 服务、Agent 编排器、聊天/搜索/因子回测三类执行分支，"
                "以及响应回到前端渲染的整体闭环。"
            ),
            nodes=(
                n("user", "用户", 0.04, 0.36, 0.11, 0.16, "actor"),
                n("fe", "浏览器前端\nindex.html / app.js", 0.19, 0.32, 0.15, 0.23, "ui"),
                n("http", "本地 HTTP 服务\nFinanceRouterRequestHandler", 0.39, 0.32, 0.16, 0.23),
                n("agent", "FinanceAgent\n路由 + 选模型 + 任务派发", 0.60, 0.32, 0.17, 0.23),
                n("chat", "Chat Handler\nLLM / Web Search", 0.82, 0.10, 0.14, 0.17, "branch"),
                n("factor", "Factor Pipeline\n解析/编译/回测/图表", 0.82, 0.37, 0.14, 0.19, "branch"),
                n("session", "SessionStore\n内存会话", 0.38, 0.64, 0.16, 0.15, "data"),
                n("ui", "前端渲染\n消息 + 指标卡 + SVG", 0.62, 0.65, 0.18, 0.15, "ui"),
            ),
            edges=(
                e("user", "fe"),
                e("fe", "http", "GET/POST"),
                e("http", "agent", "/api/chat"),
                e("http", "session", "读取/重置"),
                e("agent", "chat", "chat"),
                e("agent", "factor", "factor_research"),
                e("agent", "ui", "JSON"),
                e("ui", "fe"),
            ),
            notes=("入口 run_web.py 只负责加载 src 并调用 server.main()。",),
        ),
        Diagram(
            slug="02_startup",
            title="图 2：启动流程",
            caption=(
                "启动阶段会构造 WebAppState，并在 build_agent() 中把股票解析器、策略解析器、"
                "SQLite 回测引擎和各类任务处理器全部装配好。"
            ),
            nodes=vertical_nodes(
                (
                    "python run_web.py",
                    "加入 ROOT/src 到 sys.path",
                    "调用 stock_common.web.server.main()",
                    "解析 --host / --port",
                    "create_server() 创建 WebAppState",
                    "build_agent() 装配 FinanceAgent",
                    "ThreadingHTTPServer.serve_forever()",
                )
            ),
            edges=chain_edges(7),
        ),
        Diagram(
            slug="03_frontend",
            title="图 3：前端交互流程",
            caption=(
                "前端维护 sessionId、联网搜索开关和 pending 状态；提交问题时先展示用户消息和 typing，"
                "收到响应后再渲染 assistant 气泡和回测图表。"
            ),
            nodes=(
                n("load", "页面加载\ninit()", 0.05, 0.10, 0.14, 0.14, "ui"),
                n("health", "健康检查\nGET /api/health", 0.28, 0.08, 0.16, 0.15),
                n("submit", "发送消息\nsubmitMessage()", 0.28, 0.33, 0.16, 0.15, "branch"),
                n("reset", "新会话\nresetSession()", 0.28, 0.60, 0.16, 0.15, "branch"),
                n("post", "POST /api/chat\nsession_id/message/web_search", 0.55, 0.31, 0.19, 0.18),
                n("render", "渲染响应\nMarkdown + artifacts", 0.79, 0.28, 0.16, 0.21, "ui"),
                n("storage", "localStorage\nsession + webSearch", 0.55, 0.62, 0.20, 0.15, "data"),
            ),
            edges=(
                e("load", "health"),
                e("load", "submit"),
                e("load", "reset"),
                e("submit", "post"),
                e("post", "render"),
                e("render", "storage"),
                e("reset", "storage"),
            ),
        ),
        Diagram(
            slug="04_http",
            title="图 4：HTTP 接口分发",
            caption=(
                "HTTP 层是零依赖本地服务：GET 负责健康检查和静态文件，POST 负责聊天和会话重置，"
                "核心业务全部交给 FinanceAgent。"
            ),
            nodes=(
                n("req", "HTTP 请求", 0.05, 0.34, 0.13, 0.14, "actor"),
                n("method", "方法判断\nGET / POST", 0.25, 0.32, 0.15, 0.18, "decision"),
                n("static", "静态资源\nindex/css/js", 0.50, 0.10, 0.15, 0.16),
                n("health", "健康检查\nsessions count", 0.50, 0.32, 0.15, 0.16),
                n("chat", "聊天接口\n/api/chat", 0.50, 0.54, 0.15, 0.16, "branch"),
                n("agent", "FinanceAgent\nhandle_user_message()", 0.75, 0.47, 0.18, 0.19),
                n("reset", "重置会话\n/api/session/reset", 0.75, 0.17, 0.18, 0.16, "data"),
            ),
            edges=(
                e("req", "method"),
                e("method", "static", "GET /"),
                e("method", "health", "GET health"),
                e("method", "chat", "POST chat"),
                e("chat", "agent"),
                e("method", "reset", "POST reset"),
            ),
        ),
        Diagram(
            slug="05_agent_turn",
            title="图 5：Agent 单轮编排",
            caption=(
                "一轮用户消息会先写入会话，再经过路由、模型选择、任务构建和逐任务 dispatch，"
                "最后聚合为 AgentResponse 并回写 previous_intent。"
            ),
            nodes=horizontal_nodes(
                (
                    "记录用户\n消息",
                    "路由判断",
                    "模型选择",
                    "构建任务",
                    "任务派发",
                    "聚合响应",
                    "记录助手\n消息",
                )
            ),
            edges=chain_edges(7),
        ),
        Diagram(
            slug="06_router",
            title="图 6：Router 决策",
            caption=(
                "Router 优先使用强规则命中；否则走关键词分类器和置信门控。低置信、冲突、mixed、unknown "
                "都会进入保守兜底，并标记 requires_llm_parser。"
            ),
            nodes=(
                n("text", "输入文本 + 上下文", 0.05, 0.36, 0.15, 0.15, "actor"),
                n("rule", "RuleRouter\n强规则", 0.27, 0.12, 0.15, 0.16),
                n("clf", "KeywordBaselineClassifier", 0.27, 0.40, 0.15, 0.16),
                n("gate", "ConfidenceGate\n阈值/边际", 0.49, 0.40, 0.15, 0.16, "decision"),
                n("fallback", "FallbackRouter\n保守兜底", 0.49, 0.12, 0.15, 0.16, "branch"),
                n("decision", "RouteDecision\nlabel + confidence + metadata", 0.73, 0.27, 0.20, 0.20, "data"),
            ),
            edges=(
                e("text", "rule"),
                e("text", "clf"),
                e("rule", "decision", "strong"),
                e("clf", "gate"),
                e("gate", "decision", "direct"),
                e("gate", "fallback", "low/conflict"),
                e("fallback", "decision"),
            ),
        ),
        Diagram(
            slug="07_dispatch",
            title="图 7：任务拆分与处理器",
            caption=(
                "直接路由会生成一个任务；mixed 或 unknown 会进入启发式任务解析。Dispatcher 再按 RouteLabel "
                "分发到聊天、因子研究或预览处理器。"
            ),
            nodes=(
                n("route", "RouteDecision", 0.04, 0.34, 0.14, 0.15, "data"),
                n("parser", "TaskParser\nmixed 拆分", 0.25, 0.34, 0.15, 0.15),
                n("tasks", "TaskSpec[]", 0.46, 0.34, 0.13, 0.15, "data"),
                n("dispatch", "TaskDispatcher", 0.65, 0.34, 0.15, 0.15),
                n("chat", "FinanceChatHandler", 0.83, 0.08, 0.14, 0.14, "branch"),
                n("factor", "FactorResearchTaskHandler", 0.83, 0.34, 0.14, 0.14, "branch"),
                n("preview", "RoutingPreviewHandler", 0.83, 0.60, 0.14, 0.14, "branch"),
            ),
            edges=(
                e("route", "parser", "需要解析"),
                e("parser", "tasks"),
                e("route", "tasks", "直接任务"),
                e("tasks", "dispatch"),
                e("dispatch", "chat"),
                e("dispatch", "factor"),
                e("dispatch", "preview"),
            ),
        ),
        Diagram(
            slug="08_chat_search",
            title="图 8：聊天与联网搜索",
            caption=(
                "Chat Handler 根据 web_search 元数据选择路径：普通聊天调用 OpenAI-compatible chat/completions；"
                "联网模式调用配置的搜索 API 并把结果综合成回答。"
            ),
            nodes=(
                n("handler", "FinanceChatHandler", 0.04, 0.34, 0.16, 0.16),
                n("switch", "web_search?", 0.27, 0.34, 0.14, 0.16, "decision"),
                n("llm_msg", "构造聊天历史\n最近 8 条", 0.49, 0.13, 0.16, 0.16),
                n("llm", "Chat Completions API", 0.73, 0.13, 0.18, 0.16, "external"),
                n("search", "WebSearchClient\n外部搜索 API", 0.49, 0.55, 0.16, 0.16, "external"),
                n("synth", "SearchAnswerSynthesizer\n汇总来源", 0.73, 0.55, 0.18, 0.16),
            ),
            edges=(
                e("handler", "switch"),
                e("switch", "llm_msg", "否"),
                e("llm_msg", "llm"),
                e("switch", "search", "是"),
                e("search", "synth"),
            ),
        ),
        Diagram(
            slug="09_factor_pipeline",
            title="图 9：因子研究 Pipeline",
            caption=(
                "因子研究先把自然语言编译成确定性 ExecutionPlan；如果校验通过，再运行回测、计算指标并生成图表数据。"
            ),
            nodes=horizontal_nodes(
                (
                    "LLM/规则\n解析草稿",
                    "策略校验",
                    "编译计划",
                    "运行回测",
                    "计算指标",
                    "生成图表",
                    "返回结果",
                )
            ),
            edges=chain_edges(7),
            notes=("若缺少股票池、回测区间或选股数量，会返回 needs_clarification。",),
        ),
        Diagram(
            slug="10_sqlite_engine",
            title="图 10：SQLite 回测引擎",
            caption=(
                "Web 版实际注入 SQLiteFactorBacktestEngine。它优先使用本地 SQLite 行情和 daily_basic 因子，"
                "只有本地缺少必要因子时才调用 Tushare 风格的实时因子接口。"
            ),
            nodes=(
                n("plan", "ExecutionPlan", 0.04, 0.34, 0.13, 0.15, "data"),
                n("dates", "交易日/调仓日\n daily_qfq", 0.24, 0.15, 0.15, 0.16, "data"),
                n("factors", "因子值\n daily_basic / factor_value", 0.24, 0.53, 0.15, 0.16, "data"),
                n("select", "过滤 + 排名 + 等权", 0.48, 0.34, 0.17, 0.16),
                n("trade", "订单/成交/成本", 0.70, 0.15, 0.15, 0.16),
                n("nav", "每日收益/NAV/基准", 0.70, 0.53, 0.15, 0.16),
                n("facts", "BacktestFacts", 0.86, 0.34, 0.12, 0.15, "data"),
            ),
            edges=(
                e("plan", "dates"),
                e("plan", "factors"),
                e("dates", "select"),
                e("factors", "select"),
                e("select", "trade"),
                e("select", "nav"),
                e("trade", "facts"),
                e("nav", "facts"),
            ),
        ),
        Diagram(
            slug="11_response_rendering",
            title="图 11：响应与前端图表渲染",
            caption=(
                "后端把 metrics/charts 放在 TaskResult.metadata 中。前端检测到 artifacts 后渲染指标卡、"
                "净值曲线、回撤、月度热力图和年度收益柱状图。"
            ),
            nodes=(
                n("response", "AgentResponse JSON", 0.05, 0.34, 0.15, 0.15, "data"),
                n("bubble", "assistant 气泡\nMarkdown", 0.27, 0.18, 0.15, 0.16, "ui"),
                n("artifact", "extractBacktestArtifact()", 0.27, 0.51, 0.17, 0.16),
                n("cards", "指标卡\n收益/超额/Sharpe/回撤", 0.54, 0.32, 0.18, 0.17, "ui"),
                n("charts", "SVG 图表\nline/heatmap/bar", 0.77, 0.32, 0.16, 0.17, "ui"),
            ),
            edges=(
                e("response", "bubble"),
                e("response", "artifact"),
                e("artifact", "cards"),
                e("artifact", "charts"),
            ),
        ),
        Diagram(
            slug="12_session",
            title="图 12：会话状态",
            caption=(
                "浏览器保存 session_id，本地服务用内存 SessionStore 保存 SessionState。每次响应后都会更新最近消息摘要和 previous_intent，"
                "供下一轮路由使用。"
            ),
            nodes=(
                n("local", "localStorage\nsession_id", 0.05, 0.18, 0.15, 0.16, "data"),
                n("store", "SessionStore\nget/reset", 0.30, 0.18, 0.15, 0.16, "data"),
                n("state", "SessionState\nmessages", 0.55, 0.18, 0.15, 0.16, "data"),
                n("router", "RouterContext\nprevious_intent + summary", 0.77, 0.18, 0.18, 0.16),
                n("user", "record_user_message()", 0.31, 0.58, 0.18, 0.14),
                n("agent", "record_agent_response()", 0.58, 0.58, 0.20, 0.14),
            ),
            edges=(
                e("local", "store"),
                e("store", "state"),
                e("state", "router"),
                e("state", "user"),
                e("user", "agent"),
                e("agent", "state"),
            ),
        ),
        Diagram(
            slug="13_errors_config",
            title="图 13：异常与配置边界",
            caption=(
                "该图把可恢复异常和外部配置边界放在一起：缺模型、缺搜索、缺 Tushare、缺数据时，"
                "系统会转成结构化状态返回前端，而不是让 Web 服务崩溃。"
            ),
            nodes=(
                n("env", ".env / 环境变量", 0.05, 0.14, 0.16, 0.15, "data"),
                n("llm", "聊天/抽取模型\nFINANCE_*", 0.29, 0.08, 0.16, 0.15, "external"),
                n("search", "搜索 API\nFINANCE_WEB_SEARCH_*", 0.29, 0.33, 0.16, 0.15, "external"),
                n("tushare", "Tushare\nTUSHARE_TOKEN", 0.29, 0.58, 0.16, 0.15, "external"),
                n("errors", "TaskResult / PipelineResult\nneeds_user_input 或 failed", 0.58, 0.33, 0.22, 0.18, "branch"),
                n("frontend", "前端展示\n状态 + 错误消息", 0.84, 0.34, 0.13, 0.16, "ui"),
            ),
            edges=(
                e("env", "llm"),
                e("env", "search"),
                e("env", "tushare"),
                e("llm", "errors"),
                e("search", "errors"),
                e("tushare", "errors"),
                e("errors", "frontend"),
            ),
        ),
    )


def n(key: str, label: str, x: float, y: float, w: float, h: float, kind: str = "process") -> Node:
    return Node(key, label, x, y, w, h, kind)


def e(start: str, end: str, label: str = "") -> Edge:
    return Edge(start, end, label)


def horizontal_nodes(labels: Iterable[str]) -> tuple[Node, ...]:
    labels = tuple(labels)
    count = len(labels)
    gap = 0.018
    w = (0.92 - gap * (count - 1)) / count
    return tuple(
        n(f"node_{index}", label, 0.04 + index * (w + gap), 0.34, w, 0.18)
        for index, label in enumerate(labels)
    )


def vertical_nodes(labels: Iterable[str]) -> tuple[Node, ...]:
    labels = tuple(labels)
    count = len(labels)
    gap = 0.025
    h = min(0.10, (0.84 - gap * (count - 1)) / count)
    start_y = 0.08
    return tuple(
        n(f"node_{index}", label, 0.27, start_y + index * (h + gap), 0.46, h)
        for index, label in enumerate(labels)
    )


def chain_edges(count: int) -> tuple[Edge, ...]:
    return tuple(e(f"node_{index}", f"node_{index + 1}") for index in range(count - 1))


def render_diagrams(diagrams: tuple[Diagram, ...]) -> dict[str, Path]:
    font_regular = pil_font(32)
    font_small = pil_font(24)
    font_title = pil_font(42)
    paths: dict[str, Path] = {}
    for diagram in diagrams:
        image = Image.new("RGB", (1800, 1050), "#f8fafc")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((30, 30, 1770, 1020), radius=32, fill="#ffffff", outline="#d9e2ec", width=3)
        draw.text((70, 58), diagram.title, fill="#14213d", font=font_title)
        draw.line((70, 122, 1730, 122), fill="#d9e2ec", width=3)

        scale_x = 1800
        scale_y = 1050
        top = 150
        usable_h = 720
        node_map = {node.key: node for node in diagram.nodes}
        for edge in diagram.edges:
            draw_edge(draw, edge, node_map, scale_x, usable_h, top, font_small)
        for node in diagram.nodes:
            draw_node(draw, node, scale_x, usable_h, top, font_regular)

        note_y = 900
        caption = "图表说明：" + diagram.caption
        wrapped = wrap_text(caption, font_small, 1580)
        for line in wrapped[:3]:
            draw.text((90, note_y), line, fill="#334155", font=font_small)
            note_y += 34
        for note in diagram.notes:
            draw.text((110, note_y), "• " + note, fill="#64748b", font=font_small)
            note_y += 32

        out_path = TMP_DIR / f"{diagram.slug}.png"
        image.save(out_path, quality=95)
        paths[diagram.slug] = out_path
    return paths


def draw_node(
    draw: ImageDraw.ImageDraw,
    node: Node,
    scale_x: int,
    usable_h: int,
    top: int,
    font: ImageFont.ImageFont,
) -> None:
    x1 = int(node.x * scale_x)
    y1 = top + int(node.y * usable_h)
    x2 = int((node.x + node.w) * scale_x)
    y2 = top + int((node.y + node.h) * usable_h)
    fill, outline = node_colors(node.kind)
    if node.kind == "decision":
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        points = [(cx, y1), (x2, cy), (cx, y2), (x1, cy)]
        draw.polygon(points, fill=fill, outline=outline)
        draw.line(points + [points[0]], fill=outline, width=3)
    else:
        draw.rounded_rectangle((x1, y1, x2, y2), radius=24, fill=fill, outline=outline, width=3)

    max_w = max(40, x2 - x1 - 30)
    lines = []
    for part in node.label.split("\n"):
        lines.extend(wrap_text(part, font, max_w))
    line_h = font_bbox(font, "文")[3] + 8
    total_h = line_h * len(lines)
    y = y1 + max(10, (y2 - y1 - total_h) // 2)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        tx = x1 + (x2 - x1 - (bbox[2] - bbox[0])) // 2
        draw.text((tx, y), line, fill="#0f172a", font=font)
        y += line_h


def draw_edge(
    draw: ImageDraw.ImageDraw,
    edge: Edge,
    node_map: dict[str, Node],
    scale_x: int,
    usable_h: int,
    top: int,
    font: ImageFont.ImageFont,
) -> None:
    if edge.start not in node_map or edge.end not in node_map:
        return
    start = node_map[edge.start]
    end = node_map[edge.end]
    sx, sy = node_anchor(start, end, scale_x, usable_h, top)
    ex, ey = node_anchor(end, start, scale_x, usable_h, top)
    draw.line((sx, sy, ex, ey), fill="#64748b", width=4)
    draw_arrow_head(draw, sx, sy, ex, ey)
    if edge.label:
        lx = (sx + ex) // 2
        ly = (sy + ey) // 2 - 22
        bbox = draw.textbbox((0, 0), edge.label, font=font)
        pad = 8
        draw.rounded_rectangle(
            (lx - (bbox[2] - bbox[0]) // 2 - pad, ly - 3, lx + (bbox[2] - bbox[0]) // 2 + pad, ly + 28),
            radius=10,
            fill="#ffffff",
            outline="#cbd5e1",
        )
        draw.text((lx - (bbox[2] - bbox[0]) // 2, ly), edge.label, fill="#475569", font=font)


def node_anchor(a: Node, b: Node, scale_x: int, usable_h: int, top: int) -> tuple[int, int]:
    ax = (a.x + a.w / 2) * scale_x
    ay = top + (a.y + a.h / 2) * usable_h
    bx = (b.x + b.w / 2) * scale_x
    by = top + (b.y + b.h / 2) * usable_h
    dx = bx - ax
    dy = by - ay
    if abs(dx) > abs(dy):
        x = (a.x + (a.w if dx > 0 else 0)) * scale_x
        y = ay
    else:
        x = ax
        y = top + (a.y + (a.h if dy > 0 else 0)) * usable_h
    return int(x), int(y)


def draw_arrow_head(draw: ImageDraw.ImageDraw, sx: int, sy: int, ex: int, ey: int) -> None:
    angle = math.atan2(ey - sy, ex - sx)
    size = 18
    points = [
        (ex, ey),
        (ex - size * math.cos(angle - math.pi / 6), ey - size * math.sin(angle - math.pi / 6)),
        (ex - size * math.cos(angle + math.pi / 6), ey - size * math.sin(angle + math.pi / 6)),
    ]
    draw.polygon(points, fill="#64748b")


def node_colors(kind: str) -> tuple[str, str]:
    palette = {
        "actor": ("#ecfeff", "#0891b2"),
        "ui": ("#f0fdf4", "#16a34a"),
        "data": ("#fff7ed", "#ea580c"),
        "branch": ("#eef2ff", "#4f46e5"),
        "decision": ("#fefce8", "#ca8a04"),
        "external": ("#fdf2f8", "#db2777"),
        "process": ("#eff6ff", "#2563eb"),
    }
    return palette.get(kind, palette["process"])


def wrap_text(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if font_bbox(font, candidate)[2] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def font_bbox(font: ImageFont.ImageFont, text: str) -> tuple[int, int, int, int]:
    try:
        return font.getbbox(text)
    except AttributeError:
        w, h = font.getsize(text)
        return (0, 0, w, h)


def build_pdf(image_paths: dict[str, Path]) -> None:
    page_size = landscape(A4)
    page_w, page_h = page_size
    doc = SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=page_size,
        rightMargin=1.35 * cm,
        leftMargin=1.35 * cm,
        topMargin=1.35 * cm,
        bottomMargin=1.35 * cm,
        title="Finance Router 项目交互流程图",
        author="Codex",
    )
    font = pdf_font()
    styles = get_styles(font)
    story = []

    story.append(Paragraph("Finance Router 项目交互流程图", styles["TitleCN"]))
    story.append(Spacer(1, 0.35 * cm))
    story.append(
        Paragraph(
            "入口文件：run_web.py。本 PDF 按板块梳理本地 Web UI、HTTP 接口、Agent 编排、路由、"
            "聊天/联网搜索、因子研究回测、前端图表渲染、会话状态和异常配置边界。",
            styles["BodyCN"],
        )
    )
    story.append(Spacer(1, 0.45 * cm))
    story.append(summary_table(styles))
    story.append(PageBreak())

    diagrams = build_diagrams()
    for index, diagram in enumerate(diagrams, start=1):
        story.append(Paragraph(f"{index}. {section_title(diagram.title)}", styles["HeadingCN"]))
        story.append(Paragraph(diagram.caption, styles["BodyCN"]))
        if diagram.notes:
            for note in diagram.notes:
                story.append(Paragraph("要点：" + note, styles["SmallCN"]))
        story.append(Spacer(1, 0.25 * cm))
        image = PdfImage(str(image_paths[diagram.slug]))
        max_w = page_w - doc.leftMargin - doc.rightMargin
        image.drawWidth = min(max_w, 23.2 * cm)
        image.drawHeight = max_w * 1050 / 1800
        image.drawHeight = image.drawWidth * 1050 / 1800
        story.append(KeepTogether([image, Spacer(1, 0.15 * cm)]))
        story.append(Paragraph("图表描述：" + diagram.caption, styles["CaptionCN"]))
        if index < len(diagrams):
            story.append(PageBreak())

    story.append(PageBreak())
    story.append(Paragraph("附录：运行时配置边界", styles["HeadingCN"]))
    story.append(config_table(styles))
    story.append(Spacer(1, 0.35 * cm))
    story.append(Paragraph("附录：关键源码索引", styles["HeadingCN"]))
    for item in source_index():
        story.append(Paragraph("• " + item, styles["SmallCN"]))

    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)


def get_styles(font: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "TitleCN": ParagraphStyle(
            "TitleCN",
            parent=base["Title"],
            fontName=font,
            fontSize=25,
            leading=32,
            textColor=colors.HexColor("#14213d"),
            alignment=TA_CENTER,
            spaceAfter=12,
        ),
        "HeadingCN": ParagraphStyle(
            "HeadingCN",
            parent=base["Heading1"],
            fontName=font,
            fontSize=17,
            leading=24,
            textColor=colors.HexColor("#1e3a8a"),
            spaceAfter=8,
        ),
        "BodyCN": ParagraphStyle(
            "BodyCN",
            parent=base["BodyText"],
            fontName=font,
            fontSize=10.8,
            leading=17,
            textColor=colors.HexColor("#172033"),
            alignment=TA_LEFT,
            spaceAfter=8,
        ),
        "SmallCN": ParagraphStyle(
            "SmallCN",
            parent=base["BodyText"],
            fontName=font,
            fontSize=9.2,
            leading=14,
            textColor=colors.HexColor("#475569"),
            spaceAfter=5,
        ),
        "CaptionCN": ParagraphStyle(
            "CaptionCN",
            parent=base["BodyText"],
            fontName=font,
            fontSize=9,
            leading=14,
            textColor=colors.HexColor("#334155"),
            leftIndent=6,
            rightIndent=6,
            spaceAfter=4,
        ),
    }


def summary_table(styles: dict[str, ParagraphStyle]) -> Table:
    rows = [
        ("板块", "覆盖内容"),
        ("Web 入口", "run_web.py、server.main()、build_agent() 的启动链路"),
        ("用户交互", "前端初始化、健康检查、发送消息、新会话、联网搜索开关"),
        ("Agent 编排", "路由、模型选择、任务拆分、任务处理器和响应聚合"),
        ("业务分支", "普通聊天、联网搜索、因子研究和 SQLite 回测"),
        ("输出渲染", "指标卡、SVG 图表、会话状态、异常和配置边界"),
    ]
    return make_table(rows, styles)


def config_table(styles: dict[str, ParagraphStyle]) -> Table:
    rows = [
        ("能力", "主要配置", "使用位置"),
        ("普通聊天 LLM", "FINANCE_CHAT_* / DEEPSEEK_* / OPENAI_*", "ChatLLMSettings.from_model_profile()"),
        ("因子研究 LLM", "FINANCE_RESEARCH_*", "LLMExtractStrategyParser.for_model()"),
        ("联网搜索", "FINANCE_WEB_SEARCH_API_URL / API_KEY", "WebSearchSettings.from_env()"),
        ("本地市场库", "DATA_FETCH_DB_PATH", "SQLiteFactorBacktestEngine"),
        ("实时因子", "TUSHARE_TOKEN / TUSHARE_HTTP_URL", "FactorFetchClient"),
    ]
    return make_table(rows, styles)


def make_table(rows: list[tuple[str, ...]], styles: dict[str, ParagraphStyle]) -> Table:
    data = [
        [Paragraph(str(cell), styles["SmallCN"]) for cell in row]
        for row in rows
    ]
    table = Table(data, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e0f2fe")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def section_title(title: str) -> str:
    return title.split("：", 1)[-1]


def source_index() -> tuple[str, ...]:
    return (
        "run_web.py - 入口文件，设置 src 路径并调用 Web server。",
        "src/stock_common/web/server.py - HTTP 服务、会话存储、Agent 装配。",
        "src/stock_common/web/static/app.js - 前端事件、API 调用和图表渲染。",
        "src/stock_common/agent/finance_agent.py - 单轮消息编排。",
        "src/stock_common/router/router.py - 路由决策主流程。",
        "src/stock_common/backtest/pipeline.py - 因子研究端到端流程。",
        "src/stock_common/backtest/real_engine.py - SQLite + Tushare 回测引擎。",
    )


def page_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont(pdf_font(), 8)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawString(1.55 * cm, 0.75 * cm, "Finance Router 项目交互流程图")
    canvas.drawRightString(landscape(A4)[0] - 1.55 * cm, 0.75 * cm, f"第 {doc.page} 页")
    canvas.restoreState()


if __name__ == "__main__":
    main()
