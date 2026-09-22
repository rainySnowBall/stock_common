"""Deterministic metrics engine for backtest facts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, stdev
from typing import Any

from stock_common.backtest.registry import REGISTRY_VERSIONS
from stock_common.backtest.schemas import BacktestFacts, MetricsResult, PortfolioDaily, to_serializable


@dataclass(frozen=True)
class MetricsConfig:
    """Stable defaults for the first metrics implementation."""

    metrics_version: str = REGISTRY_VERSIONS["metrics_version"]
    annualization_factor: int = 252
    risk_free_rate_annual: float = 0.0
    rolling_windows: tuple[int, ...] = (63, 126, 252)
    minimum_sharpe_samples: int = 60
    minimum_beta_samples: int = 60


@dataclass(frozen=True)
class MetricsEngine:
    """Calculate summary and chart-ready metric series from BacktestFacts."""

    config: MetricsConfig = MetricsConfig()

    def calculate(self, facts: BacktestFacts) -> MetricsResult:
        rows = tuple(facts.portfolio_daily)
        if not rows:
            return MetricsResult(
                schema_version="1.0.0",
                summary={},
                series={
                    "nav": [],
                    "drawdown": [],
                    "period_returns": [],
                    "rolling_metrics": [],
                    "turnover": [],
                    "costs": [],
                    "exposures": [],
                    "portfolio_capacity": [],
                },
                warnings=({"code": "NO_PORTFOLIO_DAILY", "message": "缺少日频组合数据。"},),
                metadata=_metadata(facts, self.config),
            )

        nav_series = _nav_series(rows, facts)
        drawdown_series, drawdown_stats = _drawdown_series(nav_series)
        period_returns = _period_returns(nav_series)
        rolling_metrics = _rolling_metrics(nav_series, self.config)
        turnover_series = _turnover_series(rows)
        cost_series = _cost_series(facts)
        portfolio_capacity = _portfolio_capacity_series(rows)
        exposures = [dict(item) for item in facts.exposures]

        daily_returns = [row.net_return for row in rows]
        gross_returns = [row.gross_return for row in rows]
        benchmark_returns = [row.benchmark_return for row in rows]
        active_returns = [
            strategy - benchmark for strategy, benchmark in zip(daily_returns, benchmark_returns)
        ]
        years = _calendar_years(rows)
        daily_rf = (1 + self.config.risk_free_rate_annual) ** (
            1 / self.config.annualization_factor
        ) - 1

        performance = _performance_summary(nav_series, years)
        risk = _risk_summary(
            daily_returns=daily_returns,
            annual_return=performance["annual_return"],
            drawdown_stats=drawdown_stats,
            config=self.config,
            daily_rf=daily_rf,
        )
        benchmark = _benchmark_summary(
            daily_returns=daily_returns,
            benchmark_returns=benchmark_returns,
            active_returns=active_returns,
            period_returns=period_returns,
            config=self.config,
            daily_rf=daily_rf,
        )
        if not _benchmark_is_reliable(facts):
            _clear_benchmark_metrics(performance, benchmark)
        trading = _trading_summary(
            facts=facts,
            rows=rows,
            years=years,
            performance=performance,
        )
        stability = _stability_summary(period_returns)
        concentration = _concentration_summary(rows, facts)

        warnings = list(facts.warnings)
        warnings.extend(
            _sample_warnings(
                daily_returns=daily_returns,
                benchmark_returns=benchmark_returns,
                rolling_metrics=rolling_metrics,
                config=self.config,
            )
        )

        return MetricsResult(
            schema_version="1.0.0",
            summary=_clean_value(
                {
                    "performance": performance,
                    "risk": risk,
                    "benchmark": benchmark,
                    "trading": trading,
                    "stability": stability,
                    "concentration": concentration,
                    "data_quality": facts.data_quality,
                }
            ),
            series=_clean_value(
                {
                    "nav": nav_series,
                    "drawdown": drawdown_series,
                    "period_returns": period_returns,
                    "rolling_metrics": rolling_metrics,
                    "turnover": turnover_series,
                    "costs": cost_series,
                    "exposures": exposures,
                    "portfolio_capacity": portfolio_capacity,
                }
            ),
            warnings=tuple(_clean_value(tuple(warnings))),
            metadata=_metadata(facts, self.config),
        )


def _metadata(facts: BacktestFacts, config: MetricsConfig) -> dict[str, Any]:
    return {
        "run_id": facts.metadata.get("run_id"),
        "strategy_id": facts.metadata.get("strategy_id"),
        "strategy_hash": facts.metadata.get("strategy_hash"),
        "metrics_version": config.metrics_version,
        "annualization_factor": config.annualization_factor,
        "risk_free_rate_annual": config.risk_free_rate_annual,
        "rolling_windows": list(config.rolling_windows),
        "facts_metadata": to_serializable(facts.metadata),
    }


def _nav_series(rows: tuple[PortfolioDaily, ...], facts: BacktestFacts) -> list[dict[str, Any]]:
    initial_capital = float(facts.metadata.get("initial_capital") or rows[0].net_portfolio_value)
    series = []
    for row in rows:
        net_nav = _safe_div(row.net_portfolio_value, initial_capital)
        benchmark_nav = _safe_div(row.benchmark_value, initial_capital)
        gross_nav = _safe_div(row.gross_portfolio_value, initial_capital)
        series.append(
            {
                "date": row.date.isoformat(),
                "strategy_nav": net_nav,
                "benchmark_nav": benchmark_nav,
                "excess_nav": _safe_div(net_nav, benchmark_nav),
                "gross_nav": gross_nav,
                "net_nav": net_nav,
                "strategy_return": row.net_return,
                "gross_return": row.gross_return,
                "benchmark_return": row.benchmark_return,
            }
        )
    return series


def _drawdown_series(nav_series: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    strategy_peak = 0.0
    benchmark_peak = 0.0
    strategy_peak_index = 0
    max_drawdown = 0.0
    max_drawdown_index = 0
    max_drawdown_start_index = 0
    series = []

    for index, row in enumerate(nav_series):
        strategy_nav = float(row["strategy_nav"])
        benchmark_nav = float(row["benchmark_nav"])
        if strategy_nav >= strategy_peak:
            strategy_peak = strategy_nav
            strategy_peak_index = index
        benchmark_peak = max(benchmark_peak, benchmark_nav)
        strategy_drawdown = _safe_div(strategy_nav, strategy_peak, default=1.0) - 1
        benchmark_drawdown = _safe_div(benchmark_nav, benchmark_peak, default=1.0) - 1
        if strategy_drawdown < max_drawdown:
            max_drawdown = strategy_drawdown
            max_drawdown_index = index
            max_drawdown_start_index = strategy_peak_index
        series.append(
            {
                "date": row["date"],
                "strategy_drawdown": strategy_drawdown,
                "benchmark_drawdown": benchmark_drawdown,
            }
        )

    peak_nav = float(nav_series[max_drawdown_start_index]["strategy_nav"])
    recovery_date = None
    recovery_index = len(nav_series) - 1
    for index in range(max_drawdown_index, len(nav_series)):
        if float(nav_series[index]["strategy_nav"]) >= peak_nav:
            recovery_date = nav_series[index]["date"]
            recovery_index = index
            break

    return series, {
        "max_drawdown": max_drawdown,
        "max_drawdown_start_date": nav_series[max_drawdown_start_index]["date"],
        "max_drawdown_bottom_date": nav_series[max_drawdown_index]["date"],
        "max_drawdown_recovery_date": recovery_date,
        "max_drawdown_duration_days": recovery_index - max_drawdown_start_index,
    }


def _period_returns(nav_series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    monthly = _period_return_rows(nav_series, "monthly")
    yearly = _period_return_rows(nav_series, "yearly")
    return monthly + yearly


def _period_return_rows(nav_series: list[dict[str, Any]], period: str) -> list[dict[str, Any]]:
    groups: dict[tuple[int, int], dict[str, Any]] = {}
    for row in nav_series:
        year = int(str(row["date"])[:4])
        month = int(str(row["date"])[5:7])
        key = (year, month if period == "monthly" else 0)
        groups[key] = row

    previous_strategy_nav = 1.0
    previous_benchmark_nav = 1.0
    rows = []
    for key in sorted(groups):
        end_row = groups[key]
        strategy_nav = float(end_row["strategy_nav"])
        benchmark_nav = float(end_row["benchmark_nav"])
        strategy_return = _safe_div(strategy_nav, previous_strategy_nav) - 1
        benchmark_return = _safe_div(benchmark_nav, previous_benchmark_nav) - 1
        row = {
            "period": period,
            "year": key[0],
            "strategy_return": strategy_return,
            "benchmark_return": benchmark_return,
            "excess_return": strategy_return - benchmark_return,
            "period_end_date": end_row["date"],
        }
        if period == "monthly":
            row["month"] = key[1]
        rows.append(row)
        previous_strategy_nav = strategy_nav
        previous_benchmark_nav = benchmark_nav
    return rows


def _rolling_metrics(
    nav_series: list[dict[str, Any]],
    config: MetricsConfig,
) -> list[dict[str, Any]]:
    rows = []
    strategy_returns = [float(row["strategy_return"]) for row in nav_series]
    daily_rf = (1 + config.risk_free_rate_annual) ** (1 / config.annualization_factor) - 1

    for index, row in enumerate(nav_series):
        for window in config.rolling_windows:
            if index < window:
                rows.append(
                    {
                        "date": row["date"],
                        "window_days": window,
                        "rolling_strategy_return": None,
                        "rolling_benchmark_return": None,
                        "rolling_excess_return": None,
                        "rolling_sharpe": None,
                        "rolling_sortino": None,
                        "rolling_volatility": None,
                    }
                )
                continue

            strategy_return = float(row["strategy_nav"]) / float(nav_series[index - window]["strategy_nav"]) - 1
            benchmark_return = float(row["benchmark_nav"]) / float(nav_series[index - window]["benchmark_nav"]) - 1
            window_returns = strategy_returns[index - window + 1 : index + 1]
            rows.append(
                {
                    "date": row["date"],
                    "window_days": window,
                    "rolling_strategy_return": strategy_return,
                    "rolling_benchmark_return": benchmark_return,
                    "rolling_excess_return": (
                        (1 + strategy_return) / (1 + benchmark_return) - 1
                        if benchmark_return > -1
                        else None
                    ),
                    "rolling_sharpe": _sharpe(window_returns, config, daily_rf),
                    "rolling_sortino": _sortino(window_returns, config, daily_rf),
                    "rolling_volatility": _annualized_std(window_returns, config),
                }
            )
    return rows


def _turnover_series(rows: tuple[PortfolioDaily, ...]) -> list[dict[str, Any]]:
    monthly_totals: dict[tuple[int, int], float] = {}
    for row in rows:
        key = (row.date.year, row.date.month)
        monthly_totals[key] = monthly_totals.get(key, 0.0) + row.turnover
    return [
        {
            "date": row.date.isoformat(),
            "daily_turnover": row.turnover,
            "monthly_turnover": monthly_totals[(row.date.year, row.date.month)],
        }
        for row in rows
    ]


def _cost_series(facts: BacktestFacts) -> list[dict[str, Any]]:
    cumulative = 0.0
    rows = []
    for item in facts.costs:
        cumulative += item.total_cost
        rows.append(
            {
                "date": item.date.isoformat(),
                "commission_cost": item.commission,
                "slippage_cost": item.slippage_cost,
                "tax_cost": item.tax,
                "total_transaction_cost": item.total_cost,
                "cumulative_transaction_cost": cumulative,
            }
        )
    return rows


def _portfolio_capacity_series(rows: tuple[PortfolioDaily, ...]) -> list[dict[str, Any]]:
    return [
        {
            "date": row.date.isoformat(),
            "target_holding_count": row.holding_count,
            "actual_holding_count": row.holding_count,
            "cash_ratio": row.cash_ratio,
        }
        for row in rows
    ]


def _performance_summary(nav_series: list[dict[str, Any]], years: float) -> dict[str, Any]:
    last = nav_series[-1]
    strategy_nav = float(last["strategy_nav"])
    benchmark_nav = float(last["benchmark_nav"])
    gross_nav = float(last["gross_nav"])
    annual_return = _annual_return(strategy_nav, years)
    benchmark_annual_return = _annual_return(benchmark_nav, years)
    gross_annual_return = _annual_return(gross_nav, years)
    return {
        "total_return": strategy_nav - 1,
        "annual_return": annual_return,
        "benchmark_total_return": benchmark_nav - 1,
        "benchmark_annual_return": benchmark_annual_return,
        "annual_excess_return": (
            annual_return - benchmark_annual_return
            if annual_return is not None and benchmark_annual_return is not None
            else None
        ),
        "gross_total_return": gross_nav - 1,
        "gross_annual_return": gross_annual_return,
        "net_total_return": strategy_nav - 1,
        "net_annual_return": annual_return,
    }


def _risk_summary(
    daily_returns: list[float],
    annual_return: float | None,
    drawdown_stats: dict[str, Any],
    config: MetricsConfig,
    daily_rf: float,
) -> dict[str, Any]:
    max_drawdown = float(drawdown_stats["max_drawdown"])
    return {
        "annual_volatility": _annualized_std(daily_returns, config),
        **drawdown_stats,
        "sharpe_ratio": _sharpe(daily_returns, config, daily_rf),
        "sortino_ratio": _sortino(daily_returns, config, daily_rf),
        "calmar_ratio": (
            _safe_div(annual_return, abs(max_drawdown))
            if annual_return is not None and max_drawdown < 0
            else None
        ),
    }


def _benchmark_summary(
    daily_returns: list[float],
    benchmark_returns: list[float],
    active_returns: list[float],
    period_returns: list[dict[str, Any]],
    config: MetricsConfig,
    daily_rf: float,
) -> dict[str, Any]:
    beta = _beta(daily_returns, benchmark_returns)
    alpha_annual = None
    if beta is not None:
        alpha_daily = mean([value - daily_rf for value in daily_returns]) - beta * mean(
            [value - daily_rf for value in benchmark_returns]
        )
        alpha_annual = alpha_daily * config.annualization_factor
    tracking_error = _annualized_std(active_returns, config)
    information_ratio = (
        _safe_div(mean(active_returns) * config.annualization_factor, tracking_error)
        if tracking_error not in {None, 0}
        else None
    )
    monthly = [row for row in period_returns if row["period"] == "monthly"]
    yearly = [row for row in period_returns if row["period"] == "yearly"]
    return {
        "alpha_annual": alpha_annual,
        "beta": beta,
        "tracking_error": tracking_error,
        "information_ratio": information_ratio,
        "daily_outperformance_rate": _safe_div(
            sum(1 for value in active_returns if value > 0),
            len(active_returns),
        ),
        "monthly_outperformance_rate": _outperformance_rate(monthly),
        "yearly_outperformance_rate": _outperformance_rate(yearly),
    }


def _benchmark_is_reliable(facts: BacktestFacts) -> bool:
    coverage = facts.data_quality.get("benchmark_data_coverage")
    if not isinstance(coverage, (int, float)):
        return True
    return float(coverage) >= 0.95


def _clear_benchmark_metrics(performance: dict[str, Any], benchmark: dict[str, Any]) -> None:
    for key in (
        "benchmark_total_return",
        "benchmark_annual_return",
        "annual_excess_return",
    ):
        performance[key] = None
    for key in tuple(benchmark):
        benchmark[key] = None


def _trading_summary(
    facts: BacktestFacts,
    rows: tuple[PortfolioDaily, ...],
    years: float,
    performance: dict[str, Any],
) -> dict[str, Any]:
    monthly_turnovers: dict[tuple[int, int], float] = {}
    for row in rows:
        key = (row.date.year, row.date.month)
        monthly_turnovers[key] = monthly_turnovers.get(key, 0.0) + row.turnover
    commission_cost = sum(item.commission for item in facts.costs)
    slippage_cost = sum(item.slippage_cost for item in facts.costs)
    tax_cost = sum(item.tax for item in facts.costs)
    total_transaction_cost = sum(item.total_cost for item in facts.costs)
    gross_annual = performance.get("gross_annual_return")
    net_annual = performance.get("net_annual_return")
    return {
        "annual_turnover": _safe_div(sum(row.turnover for row in rows), years),
        "average_monthly_turnover": (
            mean(monthly_turnovers.values()) if monthly_turnovers else 0.0
        ),
        "max_monthly_turnover": max(monthly_turnovers.values()) if monthly_turnovers else 0.0,
        "total_orders": len(facts.orders),
        "total_transactions": len(facts.fills),
        "rejected_order_count": sum(1 for item in facts.orders if item.status == "rejected"),
        "commission_cost": commission_cost,
        "slippage_cost": slippage_cost,
        "tax_cost": tax_cost,
        "total_transaction_cost": total_transaction_cost,
        "transaction_cost_drag": (
            gross_annual - net_annual
            if gross_annual is not None and net_annual is not None
            else None
        ),
    }


def _stability_summary(period_returns: list[dict[str, Any]]) -> dict[str, Any]:
    monthly = [row for row in period_returns if row["period"] == "monthly"]
    yearly = [row for row in period_returns if row["period"] == "yearly"]
    monthly_values = [float(row["strategy_return"]) for row in monthly]
    yearly_values = [float(row["strategy_return"]) for row in yearly]
    return {
        "positive_month_count": sum(1 for value in monthly_values if value > 0),
        "negative_month_count": sum(1 for value in monthly_values if value < 0),
        "monthly_win_rate": _win_rate(monthly_values),
        "positive_year_count": sum(1 for value in yearly_values if value > 0),
        "negative_year_count": sum(1 for value in yearly_values if value < 0),
        "yearly_win_rate": _win_rate(yearly_values),
        "best_month_return": max(monthly_values) if monthly_values else None,
        "worst_month_return": min(monthly_values) if monthly_values else None,
        "best_year_return": max(yearly_values) if yearly_values else None,
        "worst_year_return": min(yearly_values) if yearly_values else None,
    }


def _concentration_summary(rows: tuple[PortfolioDaily, ...], facts: BacktestFacts) -> dict[str, Any]:
    latest_date = rows[-1].date
    latest_positions = [item for item in facts.positions if item.date == latest_date]
    if not latest_positions and facts.positions:
        latest_position_date = max(item.date for item in facts.positions)
        latest_positions = [item for item in facts.positions if item.date == latest_position_date]
    weights = [item.weight for item in latest_positions]
    return {
        "average_holding_count": mean([row.holding_count for row in rows]),
        "max_holding_count": max(row.holding_count for row in rows),
        "average_cash_ratio": mean([row.cash_ratio for row in rows]),
        "latest_max_position_weight": max(weights) if weights else None,
        "latest_top10_weight": sum(sorted(weights, reverse=True)[:10]) if weights else None,
        "latest_effective_holding_count": (
            _safe_div(1.0, sum(weight * weight for weight in weights)) if weights else None
        ),
    }


def _sample_warnings(
    daily_returns: list[float],
    benchmark_returns: list[float],
    rolling_metrics: list[dict[str, Any]],
    config: MetricsConfig,
) -> list[dict[str, str]]:
    warnings = []
    if len(daily_returns) < config.minimum_sharpe_samples:
        warnings.append(
            {
                "code": "INSUFFICIENT_SHARPE_SAMPLE",
                "message": "Sharpe 样本量低于建议阈值。",
            }
        )
    if len(daily_returns) < config.minimum_beta_samples or len(benchmark_returns) < config.minimum_beta_samples:
        warnings.append(
            {
                "code": "INSUFFICIENT_BETA_SAMPLE",
                "message": "Beta/Alpha 样本量低于建议阈值。",
            }
        )
    if not any(
        row["window_days"] == 252 and row["rolling_strategy_return"] is not None
        for row in rolling_metrics
    ):
        warnings.append(
            {
                "code": "INSUFFICIENT_ROLLING_1Y_SAMPLE",
                "message": "滚动 1 年指标样本不足。",
            }
        )
    return warnings


def _calendar_years(rows: tuple[PortfolioDaily, ...]) -> float:
    if len(rows) <= 1:
        return max(len(rows) / 252, 1 / 252)
    days = max((rows[-1].date - rows[0].date).days, 1)
    return days / 365.25


def _annual_return(nav: float, years: float) -> float | None:
    if nav <= 0 or years <= 0:
        return None
    return nav ** (1 / years) - 1


def _annualized_std(values: list[float], config: MetricsConfig) -> float | None:
    if len(values) < 2:
        return None
    value = stdev(values) * math.sqrt(config.annualization_factor)
    return value if math.isfinite(value) else None


def _sharpe(values: list[float], config: MetricsConfig, daily_rf: float) -> float | None:
    if len(values) < 2:
        return None
    excess = [value - daily_rf for value in values]
    denominator = stdev(excess)
    if denominator == 0:
        return None
    value = mean(excess) / denominator * math.sqrt(config.annualization_factor)
    return value if math.isfinite(value) else None


def _sortino(values: list[float], config: MetricsConfig, daily_rf: float) -> float | None:
    if not values:
        return None
    excess = [value - daily_rf for value in values]
    downside = [min(value, 0.0) ** 2 for value in excess]
    downside_deviation = math.sqrt(mean(downside)) * math.sqrt(config.annualization_factor)
    if downside_deviation == 0:
        return None
    value = mean(excess) * config.annualization_factor / downside_deviation
    return value if math.isfinite(value) else None


def _beta(strategy_returns: list[float], benchmark_returns: list[float]) -> float | None:
    if len(strategy_returns) < 2 or len(strategy_returns) != len(benchmark_returns):
        return None
    strategy_mean = mean(strategy_returns)
    benchmark_mean = mean(benchmark_returns)
    covariance = sum(
        (strategy - strategy_mean) * (benchmark - benchmark_mean)
        for strategy, benchmark in zip(strategy_returns, benchmark_returns)
    ) / (len(strategy_returns) - 1)
    variance = sum(
        (benchmark - benchmark_mean) ** 2 for benchmark in benchmark_returns
    ) / (len(benchmark_returns) - 1)
    if variance == 0:
        return None
    return covariance / variance


def _outperformance_rate(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    return _safe_div(sum(1 for row in rows if row["excess_return"] > 0), len(rows))


def _win_rate(values: list[float]) -> float | None:
    if not values:
        return None
    return _safe_div(sum(1 for value in values if value > 0), len(values))


def _safe_div(numerator: float | None, denominator: float | None, default: float | None = None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return default
    value = numerator / denominator
    return value if math.isfinite(value) else default


def _clean_value(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _clean_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_clean_value(item) for item in value)
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    return value
