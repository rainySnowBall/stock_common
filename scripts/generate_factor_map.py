"""Generate Markdown documentation for live factor lookup and local SQLite data."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from stock_common.backtest.registry import FACTOR_REGISTRY
from stock_common.data_fetch.config import DataFetchConfig
from stock_common.data_fetch.factor_fetch import FactorFetchClient


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = WORKSPACE_ROOT / "data" / "market_data.sqlite3"
OUTPUT_PATH = WORKSPACE_ROOT / "src" / "stock_common" / "data_fetch" / "factor_map.md"


def main() -> None:
    client = FactorFetchClient(DataFetchConfig.from_env())
    try:
        setattr(client._pro, "_DataApi__timeout", 120)
    except Exception:
        pass

    factors = sorted(
        client.factor_list(),
        key=lambda item: (str(item.get("factor_type")), str(item.get("factor_name"))),
    )
    sqlite_info = _sqlite_info()
    content = _render_markdown(factors, sqlite_info)
    OUTPUT_PATH.write_text(content, encoding="utf-8")
    print(f"wrote {OUTPUT_PATH} with {len(factors)} factors")


def _sqlite_info() -> dict[str, object]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    tables = []
    for row in conn.execute(
        """
        SELECT name, sql
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ):
        table = row["name"]
        tables.append(
            {
                "name": table,
                "sql": row["sql"],
                "columns": [dict(r) for r in conn.execute(f"PRAGMA table_info({table})")],
                "indexes": [dict(r) for r in conn.execute(f"PRAGMA index_list({table})")],
                "count": conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"],
            }
        )

    source_counts = [
        dict(r)
        for r in conn.execute(
            """
            SELECT
                source,
                COUNT(*) AS row_count,
                COUNT(DISTINCT ts_code) AS stock_count
            FROM daily_qfq
            GROUP BY source
            ORDER BY row_count DESC
            """
        )
    ]
    coverage = dict(
        conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM stock_basic) AS stock_basic_count,
                (SELECT COUNT(DISTINCT ts_code) FROM daily_qfq) AS stocks_with_daily,
                (SELECT MAX(trade_date) FROM daily_qfq) AS max_trade_date,
                (
                    SELECT COUNT(DISTINCT ts_code)
                    FROM daily_qfq
                    WHERE trade_date = (SELECT MAX(trade_date) FROM daily_qfq)
                ) AS stocks_on_max_trade_date,
                (SELECT COUNT(*) FROM daily_qfq) AS daily_rows
            """
        ).fetchone()
    )
    conn.close()
    return {
        "tables": tables,
        "source_counts": source_counts,
        "coverage": coverage,
    }


def _render_markdown(factors: list[dict[str, object]], sqlite_info: dict[str, object]) -> str:
    by_type: dict[str, list[dict[str, object]]] = defaultdict(list)
    for factor in factors:
        by_type[str(factor.get("factor_type") or "UNKNOWN")].append(factor)
    type_counts = Counter(str(factor.get("factor_type") or "UNKNOWN") for factor in factors)
    existing_names = {str(factor.get("factor_name")) for factor in factors}
    aliases = _manual_aliases(existing_names)

    lines: list[str] = []
    lines.append("# 因子实时查询与 SQLite 数据格式说明")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}")
    lines.append("- SQLite 数据库：`data/market_data.sqlite3`")
    lines.append("- 用途：给自然语言自定义回测解析器实时查询 Tushare 因子，并明确本地行情 SQLite 的数据口径。")
    lines.append("- 注意：`factor_value` 因子值不在当前 SQLite 中持久化，按用户策略需要实时查询。")
    lines.append("")

    _render_sqlite_section(lines, sqlite_info)
    _render_factor_api_section(lines)
    _render_factor_summary(lines, factors, type_counts)
    _render_alias_section(lines, aliases)
    _render_builtin_registry(lines)
    _render_full_factor_list(lines, by_type)
    return "\n".join(lines) + "\n"


def _render_sqlite_section(lines: list[str], sqlite_info: dict[str, object]) -> None:
    coverage = sqlite_info["coverage"]
    assert isinstance(coverage, dict)

    lines.append("## 1. 本地 SQLite 数据格式")
    lines.append("")
    lines.append("### 1.1 覆盖概况")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---:|")
    rows = (
        ("stock_basic_count", "股票基础表股票数"),
        ("stocks_with_daily", "已有日线股票数"),
        ("max_trade_date", "最新交易日"),
        ("stocks_on_max_trade_date", "最新交易日有数据股票数"),
        ("daily_rows", "日线行数"),
    )
    for key, label in rows:
        lines.append(f"| {label} | {coverage.get(key)} |")
    lines.append("")

    lines.append("### 1.2 表结构")
    lines.append("")
    for table in sqlite_info["tables"]:
        assert isinstance(table, dict)
        lines.append(f"#### `{table['name']}`")
        lines.append("")
        lines.append(f"- 行数：`{table['count']}`")
        lines.append("")
        lines.append("| 字段 | 类型 | NOT NULL | 主键序号 | 默认值 |")
        lines.append("|---|---|---:|---:|---|")
        for col in table["columns"]:
            assert isinstance(col, dict)
            default = col["dflt_value"] if col["dflt_value"] is not None else ""
            lines.append(
                f"| `{col['name']}` | `{col['type']}` | {col['notnull']} | {col['pk']} | {default} |"
            )
        if table["indexes"]:
            lines.append("")
            lines.append("索引：")
            for idx in table["indexes"]:
                assert isinstance(idx, dict)
                lines.append(f"- `{idx['name']}` unique={idx['unique']} origin={idx['origin']}")
        lines.append("")

    lines.append("### 1.3 `daily_qfq.source` 口径")
    lines.append("")
    lines.append("| source | 股票数 | 行数 | 说明 |")
    lines.append("|---|---:|---:|---|")
    for row in sqlite_info["source_counts"]:
        assert isinstance(row, dict)
        source = str(row["source"])
        note = (
            "Tushare pro_bar 前复权日线"
            if source == "tushare.pro_bar.qfq"
            else "Tushare pro.daily 原始不复权日线 fallback"
        )
        lines.append(f"| `{source}` | {row['stock_count']} | {row['row_count']} | {note} |")
    lines.append("")
    lines.append(
        "推荐回测读取：优先使用 `source = tushare.pro_bar.qfq`；如果策略允许不复权 fallback，可显式接受 `tushare.pro.daily.raw`。"
    )
    lines.append("")


def _render_factor_api_section(lines: list[str]) -> None:
    lines.append("## 2. FactorFetchClient 接口")
    lines.append("")
    lines.append("代码位置：`src/stock_common/data_fetch/factor_fetch.py`")
    lines.append("")
    lines.append("### 2.1 获取因子列表")
    lines.append("")
    lines.append("```python")
    lines.append("client.factor_list()")
    lines.append("```")
    lines.append("")
    lines.append("返回字段：")
    lines.append("")
    lines.append("| 字段 | 含义 |")
    lines.append("|---|---|")
    lines.append("| `factor_name` | Tushare 因子唯一名称，传给 `factor_value(factor_name=...)` |")
    lines.append("| `asset_type` | 资产类型，当前接口返回多为 `STK` |")
    lines.append("| `factor_type` | 因子类别，如 Alpha101、Quality、Value |")
    lines.append("| `factor_desc` | 因子中文说明或公式说明 |")
    lines.append("")
    lines.append("### 2.2 获取因子值")
    lines.append("")
    lines.append("```python")
    lines.append("client.factor_value(factor_name='roe_ttm', ts_code='000001.SZ')")
    lines.append("client.factor_value(factor_name='roe_ttm', trade_date='20260914')")
    lines.append("```")
    lines.append("")
    lines.append("必须提供 `ts_code` 或 `trade_date` 至少一个。返回为长表：")
    lines.append("")
    lines.append("| 字段 | 含义 |")
    lines.append("|---|---|")
    lines.append("| `factor_name` | 因子名称 |")
    lines.append("| `ts_code` | 股票代码 |")
    lines.append("| `trade_date` | 交易日，`YYYYMMDD` |")
    lines.append("| `factor_value` | 因子值，数值单位由因子定义决定 |")
    lines.append("")


def _render_factor_summary(
    lines: list[str],
    factors: list[dict[str, object]],
    type_counts: Counter[str],
) -> None:
    lines.append("## 3. 当前 Tushare 因子分类统计")
    lines.append("")
    lines.append("| factor_type | 数量 |")
    lines.append("|---|---:|")
    for factor_type, count in sorted(type_counts.items()):
        lines.append(f"| {factor_type} | {count} |")
    lines.append(f"| **合计** | **{len(factors)}** |")
    lines.append("")


def _render_alias_section(lines: list[str], aliases: dict[str, list[str]]) -> None:
    lines.append("## 4. 常用中文查询词到 factor_name 的建议映射")
    lines.append("")
    lines.append("这些映射用于自然语言解析时的候选召回；最终仍以 `factor_list()` 返回的 `factor_name` 为准。")
    lines.append("")
    lines.append("| 用户说法/关键词 | 候选 factor_name |")
    lines.append("|---|---|")
    for phrase, names in sorted(aliases.items()):
        factor_names = ", ".join(f"`{name}`" for name in names)
        lines.append(f"| {phrase} | {factor_names} |")
    lines.append("")


def _render_builtin_registry(lines: list[str]) -> None:
    lines.append("## 5. 当前 P0 回测内置因子注册表")
    lines.append("")
    lines.append("这是本地 DSL 已经硬编码支持的最小集合；LLM Extract 可以先从完整 Tushare 因子表里抽取，再由 Validator 决定是否接入。")
    lines.append("")
    lines.append("| factor_name | aliases | dtype | unit | default_direction | available_from |")
    lines.append("|---|---|---|---|---|---|")
    for name, definition in FACTOR_REGISTRY.items():
        aliases = ", ".join(definition.aliases)
        lines.append(
            f"| `{name}` | {aliases} | {definition.dtype} | {definition.unit} | {definition.direction} | {definition.available_from} |"
        )
    lines.append("")


def _render_full_factor_list(
    lines: list[str],
    by_type: dict[str, list[dict[str, object]]],
) -> None:
    lines.append("## 6. 完整 factor_list 映射")
    lines.append("")
    lines.append("### 使用建议")
    lines.append("")
    lines.append("- 自定义回测解析时，先在本表按中文描述、英文名称、类别召回候选因子。")
    lines.append("- 如果用户指定阈值或方向，直接生成 `FactorCondition(factor=factor_name, operator=..., value=...)`。")
    lines.append("- 如果用户只说“高质量”“低估值”等风格词，先返回候选因子让用户确认，避免静默误用。")
    lines.append("- 财务类因子需要注意数据可得时间；当前实时接口按 Tushare 因子值返回，回测防未来函数需要后续在引擎层校验。")
    lines.append("")
    for factor_type in sorted(by_type):
        lines.append(f"### {factor_type}")
        lines.append("")
        lines.append("| factor_name | asset_type | factor_desc |")
        lines.append("|---|---|---|")
        for factor in by_type[factor_type]:
            name = str(factor.get("factor_name") or "")
            asset = str(factor.get("asset_type") or "")
            desc = str(factor.get("factor_desc") or "").replace("\n", " ").replace("|", "\\|")
            lines.append(f"| `{name}` | {asset} | {desc} |")
        lines.append("")


def _manual_aliases(existing_names: set[str]) -> dict[str, list[str]]:
    aliases = {
        "ROE / 净资产收益率": ["roe_ttm", "yoy_roe", "delta_roe"],
        "ROA / 资产收益率": ["roa_ttm", "delta_roa"],
        "市盈率 / PE / 估值": ["earnings_to_price"],
        "账面市值比 / PB反向 / B/M": ["book_to_market"],
        "股息率 / 分红收益": ["dividend_yield_3y_avg"],
        "自由现金流收益率": ["fcf_to_market"],
        "EBITDA估值": ["ebitda_to_market"],
        "营收增长 / 收入同比": ["yoy_revenue"],
        "净利润增长 / 利润同比": ["yoy_net_profit"],
        "总资产增长": ["asset_growth_qoq"],
        "动量 / 过去N日收益": ["return_5d", "return_21d", "return_63d", "return_126d", "return_252d"],
        "MACD": ["macd"],
        "RSI": ["rsi"],
        "波动率 / 收益标准差": ["return_std_21d", "return_std_63d", "return_std_126d", "return_std_252d"],
        "Beta / 市场敏感度": ["beta_250d_000300", "beta_500d_000300", "beta_250d_000001"],
        "Sharpe / 夏普": ["sharpe_250d", "sharpe_750d"],
        "市值 / 小市值 / 规模": ["size", "float_size", "nl_size"],
        "换手率 / 流动性": ["avg_turnover_5d", "avg_turnover_21d", "avg_turnover_63d", "avg_turnover_252d"],
        "资产负债率 / 杠杆": ["debt_asset_ratio"],
        "毛利率": ["gross_margin"],
        "净利率": ["net_profit_margin"],
        "质量综合": ["quality_composite"],
    }
    return {
        phrase: [name for name in names if name in existing_names]
        for phrase, names in aliases.items()
        if any(name in existing_names for name in names)
    }


if __name__ == "__main__":
    main()
