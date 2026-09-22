"""Chart data builders for deterministic backtest reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stock_common.backtest.registry import REGISTRY_VERSIONS
from stock_common.backtest.schemas import ChartArtifact, ChartBundle, MetricsResult


@dataclass(frozen=True)
class ChartConfig:
    """Display-oriented chart defaults."""

    chart_schema_version: str = REGISTRY_VERSIONS["chart_schema_version"]
    max_points: int = 5000


@dataclass(frozen=True)
class ChartEngine:
    """Build chart-ready artifacts from metrics output."""

    config: ChartConfig = ChartConfig()

    def build(self, metrics: MetricsResult) -> ChartBundle:
        charts = (
            _line_chart(
                chart_id="nav_curve",
                title="策略/基准/超额净值",
                data=_limit_points(metrics.series.get("nav", []), self.config.max_points),
                priority="P0",
            ),
            _line_chart(
                chart_id="drawdown_curve",
                title="回撤曲线",
                data=_limit_points(metrics.series.get("drawdown", []), self.config.max_points),
                priority="P0",
            ),
            _heatmap_chart(
                chart_id="monthly_return_heatmap",
                title="月度收益热力图",
                data=_monthly_returns(metrics.series.get("period_returns", [])),
                priority="P0",
            ),
            _bar_chart(
                chart_id="annual_return_comparison",
                title="年度收益对比",
                data=_annual_returns(metrics.series.get("period_returns", [])),
                priority="P0",
            ),
            _line_chart(
                chart_id="rolling_return_curve",
                title="滚动收益率",
                data=_rolling_window(metrics.series.get("rolling_metrics", []), 252),
                priority="P0",
            ),
            _line_chart(
                chart_id="rolling_sharpe_curve",
                title="滚动 Sharpe",
                data=_rolling_window(metrics.series.get("rolling_metrics", []), 252),
                priority="P0",
            ),
            _bar_chart(
                chart_id="turnover_curve",
                title="换手率",
                data=_non_zero_or_tail(metrics.series.get("turnover", [])),
                priority="P1",
            ),
            _bar_chart(
                chart_id="transaction_cost_chart",
                title="交易成本",
                data=metrics.series.get("costs", []),
                priority="P1",
            ),
            _line_chart(
                chart_id="gross_net_nav_curve",
                title="毛净值对比",
                data=_gross_net_nav(metrics.series.get("nav", [])),
                priority="P1",
            ),
            _line_chart(
                chart_id="portfolio_capacity_curve",
                title="持仓数量与现金仓位",
                data=_limit_points(metrics.series.get("portfolio_capacity", []), self.config.max_points),
                priority="P1",
            ),
            _bar_chart(
                chart_id="industry_exposure",
                title="行业暴露",
                data=_industry_exposure(metrics.series.get("exposures", [])),
                priority="P1",
            ),
        )
        return ChartBundle(
            schema_version="1.0.0",
            charts=tuple(_with_empty_status(chart) for chart in charts),
            metadata={
                "chart_schema_version": self.config.chart_schema_version,
                "metrics_version": metrics.metadata.get("metrics_version"),
                "run_id": metrics.metadata.get("run_id"),
                "strategy_id": metrics.metadata.get("strategy_id"),
            },
        )


def _line_chart(
    chart_id: str,
    title: str,
    data: list[dict[str, Any]],
    priority: str,
) -> ChartArtifact:
    return ChartArtifact(
        chart_id=chart_id,
        title=title,
        chart_type="line",
        priority=priority,
        data=data,
    )


def _bar_chart(
    chart_id: str,
    title: str,
    data: list[dict[str, Any]],
    priority: str,
) -> ChartArtifact:
    return ChartArtifact(
        chart_id=chart_id,
        title=title,
        chart_type="bar",
        priority=priority,
        data=data,
    )


def _heatmap_chart(
    chart_id: str,
    title: str,
    data: list[dict[str, Any]],
    priority: str,
) -> ChartArtifact:
    return ChartArtifact(
        chart_id=chart_id,
        title=title,
        chart_type="heatmap",
        priority=priority,
        data=data,
    )


def _with_empty_status(chart: ChartArtifact) -> ChartArtifact:
    if chart.data:
        return chart
    return ChartArtifact(
        chart_id=chart.chart_id,
        title=chart.title,
        chart_type=chart.chart_type,
        priority=chart.priority,
        data=[],
        status="empty",
        warnings=({"code": "EMPTY_CHART_DATA", "message": f"{chart.chart_id} 无可展示数据。"},),
    )


def _limit_points(rows: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    if len(rows) <= max_points:
        return list(rows)
    step = max(1, len(rows) // max_points)
    sampled = rows[::step]
    if sampled[-1] != rows[-1]:
        sampled.append(rows[-1])
    return sampled


def _monthly_returns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        if row.get("period") != "monthly":
            continue
        output.append(
            {
                "year": row["year"],
                "month": row["month"],
                "monthly_return": row["strategy_return"],
                "benchmark_monthly_return": row["benchmark_return"],
                "monthly_excess_return": row["excess_return"],
            }
        )
    return output


def _annual_returns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        if row.get("period") != "yearly":
            continue
        output.append(
            {
                "year": row["year"],
                "strategy_annual_return": row["strategy_return"],
                "benchmark_annual_return": row["benchmark_return"],
                "annual_excess_return": row["excess_return"],
            }
        )
    return output


def _rolling_window(rows: list[dict[str, Any]], window_days: int) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("window_days") == window_days]


def _non_zero_or_tail(rows: list[dict[str, Any]], tail: int = 120) -> list[dict[str, Any]]:
    non_zero = [
        row
        for row in rows
        if row.get("daily_turnover", 0) or row.get("monthly_turnover", 0)
    ]
    if non_zero:
        return non_zero
    return rows[-tail:]


def _gross_net_nav(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        gross_nav = row.get("gross_nav")
        net_nav = row.get("net_nav")
        output.append(
            {
                "date": row["date"],
                "gross_nav": gross_nav,
                "net_nav": net_nav,
                "cost_drag_nav": (
                    gross_nav - net_nav
                    if isinstance(gross_nav, (int, float)) and isinstance(net_nav, (int, float))
                    else None
                ),
            }
        )
    return output


def _industry_exposure(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        output.append(
            {
                "date": row.get("date"),
                "industry_code": row.get("name"),
                "industry_name": row.get("name"),
                "portfolio_weight": row.get("portfolio_value"),
                "benchmark_weight": row.get("benchmark_value"),
                "active_industry_weight": row.get("active_value"),
            }
        )
    return output

