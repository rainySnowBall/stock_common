"""SQLite + live Tushare factor backtest engine for local web runs."""

from __future__ import annotations

import sqlite3
import math
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from stock_common.backtest.schemas import (
    BacktestFacts,
    CostRecord,
    DataStatus,
    ExecutionPlan,
    FactorCondition,
    FillRecord,
    Operator,
    OrderRecord,
    PortfolioDaily,
    PositionSnapshot,
    RebalanceFrequency,
    SortDirection,
)
from stock_common.backtest.factor_catalog import factor_exists
from stock_common.data_fetch.config import DataFetchConfig
from stock_common.data_fetch.factor_fetch import FactorFetchClient


SUPPORTED_REAL_UNIVERSES = {"all_a_demo"}


_DAILY_BASIC_DIRECT_FACTOR_COLUMNS = {
    "close": "close",
    "turnover_rate": "turnover_rate",
    "turnover_rate_f": "turnover_rate_f",
    "volume_ratio": "volume_ratio",
    "pe": "pe",
    "pe_ttm": "pe_ttm",
    "pb": "pb",
    "ps": "ps",
    "ps_ttm": "ps_ttm",
    "dv_ratio": "dv_ratio",
    "dv_ttm": "dv_ttm",
    "total_share": "total_share",
    "float_share": "float_share",
    "free_share": "free_share",
    "total_mv": "total_mv",
    "market_cap": "total_mv",
    "circ_mv": "circ_mv",
    "limit_status": "limit_status",
}

_DAILY_BASIC_DERIVED_FACTOR_COLUMNS = {
    "earnings_to_price": ("pe_ttm", "pe"),
    "book_to_market": ("pb",),
    "size": ("total_mv",),
    "float_size": ("circ_mv",),
}


@dataclass
class SQLiteFactorBacktestEngine:
    """Run a simple long-only backtest using local daily prices and live factors."""

    database_path: Path = field(
        default_factory=lambda: DataFetchConfig.from_env().database_path
    )
    factor_client: FactorFetchClient | None = None
    use_local_daily_basic_factors: bool = False
    commission_rate: float = 0.0003
    tax_rate: float = 0.001
    slippage_rate: float = 0.0005
    annual_trade_days: int = 252
    min_factor_coverage: float = 0.80
    factor_coverage_warning_threshold: float = 0.95

    def __post_init__(self) -> None:
        self._factor_cache: dict[tuple[str, str], dict[str, float]] = {}
        self._symbol_factor_cache: dict[tuple[str, str, str], float | None] = {}
        self._resolved_factor_client: FactorFetchClient | None = self.factor_client

    def run(self, plan: ExecutionPlan) -> BacktestFacts:
        unsupported_universe = _unsupported_universe(plan)
        if unsupported_universe:
            return _failed(plan, "UNSUPPORTED_REAL_UNIVERSE", unsupported_universe)
        unknown_factors = _unknown_live_factors(
            plan,
            allow_daily_basic=self.use_local_daily_basic_factors,
        )
        if unknown_factors:
            return _failed(plan, "UNKNOWN_LIVE_FACTOR", unknown_factors)

        try:
            dates = self._trade_dates(plan)
            if len(dates) < 2:
                return _failed(plan, "NO_PRICE_DATA", "本地 SQLite 没有足够的日线数据。")
        except sqlite3.Error as exc:
            return _failed(plan, type(exc).__name__, _safe_error_message(exc))

        current_weights: dict[str, float] = {}
        gross_nav = 1.0
        net_nav = 1.0
        benchmark_nav = 1.0
        rows: list[PortfolioDaily] = []
        positions: list[PositionSnapshot] = []
        orders: list[OrderRecord] = []
        fills: list[FillRecord] = []
        costs: list[CostRecord] = []
        events: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        rebalance_dates = _rebalance_dates(dates, plan.rebalance.frequency)
        benchmark_returns = self._benchmark_returns(dates, plan.backtest.benchmark)
        benchmark_coverage = _date_coverage(dates, benchmark_returns)
        if benchmark_coverage < 0.95:
            warnings.append(
                {
                    "code": "LOW_BENCHMARK_COVERAGE",
                    "message": f"基准 {plan.backtest.benchmark} 本地日线覆盖率不足，缺失日期按 0 收益处理。",
                    "coverage": benchmark_coverage,
                }
            )
        try:
            factor_coverage = self._check_factor_coverage(
                plan=plan,
                factor_client=self._resolved_factor_client,
                dates=dates,
                rebalance_dates=rebalance_dates,
            )
        except Exception as exc:
            return _failed(
                plan,
                type(exc).__name__,
                f"实时因子覆盖率检查失败：{_safe_error_message(exc)}",
                dates[0],
                dates[-1],
            )
        if factor_coverage and factor_coverage["minimum"] < self.min_factor_coverage:
            return _failed(
                plan,
                "LOW_FACTOR_COVERAGE",
                _factor_coverage_failure_message(
                    factor_coverage,
                    threshold=self.min_factor_coverage,
                ),
                dates[0],
                dates[-1],
            )
        warnings.extend(
            _factor_coverage_warnings(
                factor_coverage,
                warning_threshold=self.factor_coverage_warning_threshold,
            )
        )

        for day_index, trade_date in enumerate(dates):
            turnover = 0.0
            cost_rate = 0.0
            previous_weights = current_weights

            is_rebalance_day = trade_date in rebalance_dates
            if is_rebalance_day:
                signal_date = dates[max(0, day_index - 1)]
                try:
                    target_weights = self._select_target_weights(
                        plan=plan,
                        factor_client=self._resolved_factor_client,
                        signal_date=signal_date,
                        trade_date=trade_date,
                        previous_weights=previous_weights,
                        history_dates=dates[:day_index],
                    )
                except Exception as exc:
                    return _failed(
                        plan,
                        type(exc).__name__,
                        f"实时因子回测失败：{_safe_error_message(exc)}",
                        dates[0],
                        dates[-1],
                    )

                turnover = _one_way_turnover(previous_weights, target_weights)
                cost_rate = _transaction_cost_rate(
                    previous_weights,
                    target_weights,
                    self.commission_rate,
                    self.tax_rate,
                    self.slippage_rate,
                )
                current_weights = target_weights
                signal_id = f"signal_{trade_date}"
                order_records, fill_records = self._records_for_rebalance(
                    trade_date=trade_date,
                    signal_id=signal_id,
                    previous_weights=previous_weights,
                    target_weights=target_weights,
                )
                orders.extend(order_records)
                fills.extend(fill_records)
                costs.append(
                    CostRecord(
                        date=_parse_date(trade_date),
                        commission=sum(fill.commission for fill in fill_records),
                        tax=sum(fill.tax for fill in fill_records),
                        slippage_cost=sum(fill.slippage_cost for fill in fill_records),
                        market_impact_cost=None,
                        borrow_cost=None,
                        total_cost=sum(
                            fill.commission + fill.tax + fill.slippage_cost
                            for fill in fill_records
                        ),
                    )
                )
                events.append(
                    {
                        "timestamp": trade_date,
                        "event_type": "rebalance",
                        "symbol": None,
                        "rule_id": plan.plan_id,
                        "details": {
                            "signal_date": signal_date,
                            "selected_count": len(target_weights),
                            "turnover": turnover,
                            "data_source": self._data_source_label(),
                        },
                    }
                )

            daily_returns = self._returns_for_symbols(
                trade_date,
                tuple(current_weights),
                intraday_only=is_rebalance_day,
            )
            gross_return = sum(
                weight * daily_returns.get(symbol, 0.0)
                for symbol, weight in current_weights.items()
            )
            net_return = gross_return - cost_rate
            gross_nav *= 1 + gross_return
            net_nav *= 1 + net_return
            benchmark_nav *= 1 + benchmark_returns.get(trade_date, 0.0)
            value = plan.backtest.initial_capital * net_nav

            rows.append(
                PortfolioDaily(
                    date=_parse_date(trade_date),
                    cash=0.0,
                    positions_value=value,
                    gross_portfolio_value=plan.backtest.initial_capital * gross_nav,
                    net_portfolio_value=value,
                    benchmark_value=plan.backtest.initial_capital * benchmark_nav,
                    gross_return=gross_return,
                    net_return=net_return,
                    benchmark_return=benchmark_returns.get(trade_date, 0.0),
                    holding_count=len(current_weights),
                    cash_ratio=0.0 if current_weights else 1.0,
                    gross_exposure=sum(abs(weight) for weight in current_weights.values()),
                    net_exposure=sum(current_weights.values()),
                    turnover=turnover,
                )
            )

            if trade_date in rebalance_dates:
                positions.extend(
                    self._position_snapshots(
                        trade_date=trade_date,
                        weights=current_weights,
                        portfolio_value=value,
                    )
                )

        if not any(row.holding_count for row in rows):
            warnings.append(
                {
                    "code": "NO_HOLDINGS",
                    "message": "策略在回测区间内没有选出持仓。",
                }
            )

        return BacktestFacts(
            schema_version="1.0.0",
            status=DataStatus.SUCCEEDED,
            metadata=_metadata(plan, dates[0], dates[-1], "sqlite-factor-live-v1"),
            data_quality={
                "expected_trade_days": plan.backtest.lookback_years * self.annual_trade_days,
                "actual_trade_days": len(dates),
                "market_data_coverage": 1.0,
                "fundamental_data_coverage": 1.0,
                "benchmark_data_coverage": benchmark_coverage,
                "factor_data_coverage": factor_coverage.get("minimum", 1.0),
                "factor_coverage_checks": factor_coverage.get("checks", []),
                "missing_price_count": 0,
                "stale_price_count": 0,
                "invalid_factor_count": factor_coverage.get("missing_count", 0),
                "lookahead_checks_passed": True,
                "survivorship_checks_passed": False,
                "data_source": self._data_source_label(),
            },
            portfolio_daily=tuple(rows),
            positions=tuple(positions),
            orders=tuple(orders),
            fills=tuple(fills),
            costs=tuple(costs),
            exposures=(),
            events=tuple(events),
            warnings=tuple(
                [
                    {
                        "code": "REAL_DATA_SIMPLE_ENGINE",
                        "message": "使用本地 SQLite 日线和实时 Tushare 因子值进行真实数据回测；指数成分、生存者偏差和复杂成交约束尚未完全建模。",
                    },
                    *warnings,
                ]
            ),
        )

    def _data_source_label(self) -> str:
        if self.use_local_daily_basic_factors:
            return "sqlite_daily_qfq+sqlite_daily_basic+live_tushare_factor_value"
        return "sqlite_daily_qfq+live_tushare_factor_value"

    def _check_factor_coverage(
        self,
        *,
        plan: ExecutionPlan,
        factor_client: FactorFetchClient | None,
        dates: tuple[str, ...],
        rebalance_dates: set[str],
    ) -> dict[str, Any]:
        factors = _required_factors(plan)
        if not factors or not rebalance_dates:
            return {}

        checks: list[dict[str, Any]] = []
        total_expected = 0
        total_missing = 0
        for day_index, trade_date in enumerate(dates):
            if trade_date not in rebalance_dates:
                continue
            signal_date = dates[max(0, day_index - 1)]
            candidates = self._priced_symbols(signal_date, trade_date, plan.universe.symbols)
            expected_count = len(candidates)
            if expected_count <= 0:
                continue
            for factor in factors:
                values = self._factor_values(
                    factor_client,
                    factor,
                    signal_date,
                    symbols=tuple(candidates),
                )
                covered_count = sum(1 for symbol in candidates if symbol in values)
                missing_count = expected_count - covered_count
                coverage = covered_count / expected_count
                checks.append(
                    {
                        "factor": factor,
                        "signal_date": signal_date,
                        "trade_date": trade_date,
                        "expected_count": expected_count,
                        "covered_count": covered_count,
                        "missing_count": missing_count,
                        "coverage": coverage,
                    }
                )
                total_expected += expected_count
                total_missing += missing_count

        if not checks:
            return {}
        return {
            "minimum": min(float(item["coverage"]) for item in checks),
            "average": (
                (total_expected - total_missing) / total_expected
                if total_expected
                else 1.0
            ),
            "missing_count": total_missing,
            "checks": checks,
        }

    def _trade_dates(self, plan: ExecutionPlan) -> tuple[str, ...]:
        with self._connect() as conn:
            end_date = plan.backtest.end_date.strftime("%Y%m%d") if plan.backtest.end_date else None
            if end_date is None:
                latest_row = conn.execute("SELECT MAX(trade_date) AS d FROM daily_qfq").fetchone()
                end_date = latest_row["d"] if latest_row else None
            if end_date is None:
                return ()
            limit = plan.backtest.lookback_years * self.annual_trade_days + 2
            rows = conn.execute(
                """
                SELECT trade_date
                FROM (
                    SELECT DISTINCT trade_date
                    FROM daily_qfq
                    WHERE trade_date <= ?
                    ORDER BY trade_date DESC
                    LIMIT ?
                )
                ORDER BY trade_date
                """,
                (end_date, limit),
            ).fetchall()
        return tuple(str(row["trade_date"]) for row in rows)

    def _select_target_weights(
        self,
        *,
        plan: ExecutionPlan,
        factor_client: FactorFetchClient | None,
        signal_date: str,
        trade_date: str,
        previous_weights: dict[str, float],
        history_dates: tuple[str, ...],
    ) -> dict[str, float]:
        candidates = self._priced_symbols(signal_date, trade_date, plan.universe.symbols)
        factors = _required_factors(plan)
        factor_values = {
            factor: self._factor_values(
                factor_client,
                factor,
                signal_date,
                symbols=tuple(candidates),
            )
            for factor in factors
        }
        percentile_values = self._percentile_values(
            client=factor_client,
            factors=_required_percentile_factors(plan),
            history_dates=history_dates or (signal_date,),
            symbols=tuple(candidates),
        )
        candidates = [
            symbol
            for symbol in candidates
            if _has_factor_values(symbol, factors, factor_values)
            and _passes_all(symbol, plan.filters, factor_values, percentile_values)
        ]

        if not plan.entry_rules and not plan.exit_rules:
            selected = _rank_and_select(candidates, plan, factor_values)
            return _equal_weights(selected, plan)

        survivors = [
            symbol
            for symbol in previous_weights
            if symbol in candidates
            and not _triggers_exit(symbol, plan.exit_rules, factor_values, percentile_values)
        ]
        capacity = max(plan.selection.count - len(survivors), 0)
        if capacity <= 0:
            return _equal_weights(survivors[: plan.selection.count], plan)

        buy_candidates = [
            symbol
            for symbol in candidates
            if symbol not in survivors
            and _passes_all(symbol, plan.entry_rules, factor_values, percentile_values)
        ]
        selected_new = _rank_and_select(buy_candidates, plan, factor_values, limit=capacity)
        return _equal_weights([*survivors, *selected_new], plan)

    def _factor_values(
        self,
        client: FactorFetchClient | None,
        factor_name: str,
        trade_date: str,
        *,
        symbols: tuple[str, ...] = (),
    ) -> dict[str, float]:
        key = (factor_name, trade_date)
        values = dict(self._factor_cache.get(key, {}))
        if self.use_local_daily_basic_factors and not values:
            values.update(self._daily_basic_factor_values(factor_name, trade_date))

        missing_symbols = [symbol for symbol in symbols if symbol not in values]
        if not values or missing_symbols:
            records = self._live_factor_client(client).factor_value(
                factor_name=factor_name,
                trade_date=trade_date,
            )
            live_values: dict[str, float] = {}
            for record in records:
                ts_code = str(record.get("ts_code") or "").strip()
                if not ts_code:
                    continue
                try:
                    live_values[ts_code] = float(record["factor_value"])
                except (KeyError, TypeError, ValueError):
                    continue
            live_values.update(values)
            values = live_values

        self._factor_cache[key] = values
        return values

    def _live_factor_client(self, client: FactorFetchClient | None) -> FactorFetchClient:
        if client is not None:
            return client
        if self._resolved_factor_client is None:
            self._resolved_factor_client = FactorFetchClient(DataFetchConfig.from_env())
        return self._resolved_factor_client

    def _daily_basic_factor_values(
        self,
        factor_name: str,
        trade_date: str,
    ) -> dict[str, float]:
        columns = _daily_basic_columns(factor_name)
        if not columns:
            return {}
        select_columns = ", ".join(columns)
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT ts_code, {select_columns}
                    FROM daily_basic
                    WHERE trade_date = ?
                    """,
                    (trade_date,),
                ).fetchall()
        except sqlite3.Error:
            return {}

        values: dict[str, float] = {}
        for row in rows:
            ts_code = str(row["ts_code"] or "").strip()
            if not ts_code:
                continue
            value = _daily_basic_factor_value(factor_name, row)
            if value is not None:
                values[ts_code] = value
        return values

    def _factor_value_for_symbol(
        self,
        client: FactorFetchClient | None,
        factor_name: str,
        trade_date: str,
        symbol: str,
    ) -> float | None:
        key = (factor_name, trade_date, symbol)
        if key in self._symbol_factor_cache:
            return self._symbol_factor_cache[key]
        date_values = self._factor_cache.get((factor_name, trade_date))
        if date_values is not None and symbol in date_values:
            value = date_values[symbol]
            self._symbol_factor_cache[key] = value
            return value

        if self.use_local_daily_basic_factors:
            value = self._daily_basic_factor_value_for_symbol(
                factor_name=factor_name,
                trade_date=trade_date,
                symbol=symbol,
            )
            if value is not None:
                self._symbol_factor_cache[key] = value
                return value

        records = self._live_factor_client(client).factor_value(
            factor_name=factor_name,
            trade_date=trade_date,
            ts_code=symbol,
        )
        value = None
        for record in records:
            record_symbol = str(record.get("ts_code") or symbol).strip()
            if record_symbol and record_symbol != symbol:
                continue
            try:
                value = float(record["factor_value"])
                break
            except (KeyError, TypeError, ValueError):
                continue
        self._symbol_factor_cache[key] = value
        if value is not None:
            values = dict(self._factor_cache.get((factor_name, trade_date), {}))
            values[symbol] = value
            self._factor_cache[(factor_name, trade_date)] = values
        return value

    def _daily_basic_factor_value_for_symbol(
        self,
        *,
        factor_name: str,
        trade_date: str,
        symbol: str,
    ) -> float | None:
        columns = _daily_basic_columns(factor_name)
        if not columns:
            return None
        select_columns = ", ".join(columns)
        try:
            with self._connect() as conn:
                row = conn.execute(
                    f"""
                    SELECT {select_columns}
                    FROM daily_basic
                    WHERE trade_date = ? AND ts_code = ?
                    """,
                    (trade_date, symbol),
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        return _daily_basic_factor_value(factor_name, row)

    def _percentile_values(
        self,
        *,
        client: FactorFetchClient | None,
        factors: tuple[str, ...],
        history_dates: tuple[str, ...],
        symbols: tuple[str, ...],
    ) -> dict[str, dict[str, float]]:
        if not factors or not symbols:
            return {}
        output: dict[str, dict[str, float]] = {}
        for factor in factors:
            output[factor] = {}
            for symbol in symbols:
                values = []
                for trade_date in history_dates:
                    value = self._factor_value_for_symbol(client, factor, trade_date, symbol)
                    if value is not None:
                        values.append(value)
                if values:
                    output[factor][symbol] = _percentile(values[-1], values)
        return output

    def _priced_symbols(
        self,
        signal_date: str,
        trade_date: str,
        universe_symbols: tuple[str, ...] = (),
    ) -> list[str]:
        symbol_filter = ""
        params: tuple[Any, ...]
        if universe_symbols:
            placeholders = ",".join("?" for _ in universe_symbols)
            symbol_filter = f" AND s.ts_code IN ({placeholders})"
            params = (signal_date, trade_date, trade_date, trade_date, *universe_symbols)
        else:
            params = (signal_date, trade_date, trade_date, trade_date)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT s.ts_code
                FROM stock_basic s
                JOIN daily_qfq signal_price
                  ON signal_price.ts_code = s.ts_code
                 AND signal_price.trade_date = ?
                JOIN daily_qfq trade_price
                  ON trade_price.ts_code = s.ts_code
                 AND trade_price.trade_date = ?
                WHERE s.list_status = 'L'
                  AND (s.list_date IS NULL OR s.list_date = '' OR s.list_date <= ?)
                  AND (s.delist_date IS NULL OR s.delist_date = '' OR s.delist_date >= ?)
                  {symbol_filter}
                ORDER BY s.ts_code
                """,
                params,
            ).fetchall()
        return [str(row["ts_code"]) for row in rows]

    def _returns_for_symbols(
        self,
        trade_date: str,
        symbols: tuple[str, ...],
        *,
        intraday_only: bool = False,
    ) -> dict[str, float]:
        if not symbols:
            return {}
        placeholders = ",".join("?" for _ in symbols)
        params: tuple[Any, ...] = (trade_date, *symbols)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT ts_code, open, close, pct_chg
                FROM daily_qfq
                WHERE trade_date = ? AND ts_code IN ({placeholders})
                """,
                params,
            ).fetchall()
        if intraday_only:
            return {
                str(row["ts_code"]): _intraday_return(row)
                for row in rows
            }
        return {
            str(row["ts_code"]): (float(row["pct_chg"] or 0.0) / 100.0)
            for row in rows
        }

    def _benchmark_returns(self, dates: tuple[str, ...], benchmark: str) -> dict[str, float]:
        if not dates:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT trade_date, pct_chg
                FROM daily_qfq
                WHERE ts_code = ?
                  AND trade_date BETWEEN ? AND ?
                """,
                (benchmark, dates[0], dates[-1]),
            ).fetchall()
        return {
            str(row["trade_date"]): float(row["pct_chg"] or 0.0) / 100.0
            for row in rows
        }

    def _records_for_rebalance(
        self,
        *,
        trade_date: str,
        signal_id: str,
        previous_weights: dict[str, float],
        target_weights: dict[str, float],
    ) -> tuple[list[OrderRecord], list[FillRecord]]:
        prices = self._execution_prices_for_symbols(
            trade_date,
            tuple(set(previous_weights) | set(target_weights)),
        )
        orders: list[OrderRecord] = []
        fills: list[FillRecord] = []
        for index, symbol in enumerate(sorted(set(previous_weights) | set(target_weights)), start=1):
            delta = target_weights.get(symbol, 0.0) - previous_weights.get(symbol, 0.0)
            if abs(delta) < 1e-12:
                continue
            execution_price = prices.get(symbol)
            if execution_price is None or execution_price <= 0:
                continue
            side = "buy" if delta > 0 else "sell"
            amount = abs(delta)
            quantity = amount / execution_price
            order_id = f"ord_{trade_date}_{index:03d}"
            orders.append(
                OrderRecord(
                    order_id=order_id,
                    created_at=f"{_iso_date(trade_date)}T09:30:00",
                    symbol=symbol,
                    side=side,
                    order_type="market",
                    requested_quantity=quantity,
                    reference_price=execution_price,
                    status="filled",
                    reason=None,
                    signal_id=signal_id,
                )
            )
            fills.append(
                FillRecord(
                    fill_id=f"fill_{trade_date}_{index:03d}",
                    order_id=order_id,
                    timestamp=f"{_iso_date(trade_date)}T09:31:00",
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=execution_price,
                    amount=amount,
                    commission=amount * self.commission_rate,
                    tax=amount * self.tax_rate if side == "sell" else 0.0,
                    slippage_cost=amount * self.slippage_rate,
                )
            )
        return orders, fills

    def _position_snapshots(
        self,
        *,
        trade_date: str,
        weights: dict[str, float],
        portfolio_value: float,
    ) -> list[PositionSnapshot]:
        prices = self._closes_for_symbols(trade_date, tuple(weights))
        snapshots = []
        for symbol, weight in sorted(weights.items()):
            close = prices.get(symbol)
            if close is None or close <= 0:
                continue
            market_value = portfolio_value * weight
            snapshots.append(
                PositionSnapshot(
                    date=_parse_date(trade_date),
                    symbol=symbol,
                    quantity=market_value / close,
                    close=close,
                    market_value=market_value,
                    weight=weight,
                )
            )
        return snapshots

    def _closes_for_symbols(self, trade_date: str, symbols: tuple[str, ...]) -> dict[str, float]:
        if not symbols:
            return {}
        placeholders = ",".join("?" for _ in symbols)
        params: tuple[Any, ...] = (trade_date, *symbols)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT ts_code, close
                FROM daily_qfq
                WHERE trade_date = ? AND ts_code IN ({placeholders})
                """,
                params,
            ).fetchall()
        return {str(row["ts_code"]): float(row["close"]) for row in rows if row["close"] is not None}

    def _execution_prices_for_symbols(
        self,
        trade_date: str,
        symbols: tuple[str, ...],
    ) -> dict[str, float]:
        if not symbols:
            return {}
        placeholders = ",".join("?" for _ in symbols)
        params: tuple[Any, ...] = (trade_date, *symbols)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT ts_code, open, close
                FROM daily_qfq
                WHERE trade_date = ? AND ts_code IN ({placeholders})
                """,
                params,
            ).fetchall()
        return {
            str(row["ts_code"]): _execution_price(row)
            for row in rows
        }

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def _daily_basic_factor_exists(factor_name: str) -> bool:
    return bool(_daily_basic_columns(factor_name))


def _daily_basic_columns(factor_name: str) -> tuple[str, ...]:
    direct_column = _DAILY_BASIC_DIRECT_FACTOR_COLUMNS.get(factor_name)
    if direct_column is not None:
        return (direct_column,)
    return _DAILY_BASIC_DERIVED_FACTOR_COLUMNS.get(factor_name, ())


def _daily_basic_factor_value(factor_name: str, row: sqlite3.Row) -> float | None:
    direct_column = _DAILY_BASIC_DIRECT_FACTOR_COLUMNS.get(factor_name)
    if direct_column is not None:
        return _optional_float(row[direct_column])
    if factor_name == "earnings_to_price":
        return _inverse_first(row, ("pe_ttm", "pe"))
    if factor_name == "book_to_market":
        return _inverse_first(row, ("pb",))
    if factor_name == "size":
        return _negative_log(row["total_mv"])
    if factor_name == "float_size":
        return _negative_log(row["circ_mv"])
    return None


def _inverse_first(row: sqlite3.Row, columns: tuple[str, ...]) -> float | None:
    for column in columns:
        value = _optional_float(row[column])
        if value is not None and value != 0:
            return 1.0 / value
    return None


def _negative_log(value: Any) -> float | None:
    parsed = _optional_float(value)
    if parsed is None or parsed <= 0:
        return None
    return -math.log(parsed)


def _unsupported_universe(plan: ExecutionPlan) -> str:
    if plan.universe.symbols:
        return ""
    if plan.universe.name in SUPPORTED_REAL_UNIVERSES:
        return ""
    return (
        f"真实数据回测暂只支持全 A 股票池，当前收到 `{plan.universe.name}`；"
        "本地库尚未接入指数历史成分，不能把全 A 结果冒充指数成分回测。"
    )


def _unknown_live_factors(plan: ExecutionPlan, *, allow_daily_basic: bool) -> str:
    unknown = sorted(
        factor
        for factor in _required_factors(plan)
        if not factor_exists(factor)
        and not (allow_daily_basic and _daily_basic_factor_exists(factor))
    )
    if not unknown:
        return ""
    received = "、".join(unknown)
    return f"因子映射表中没有这些 factor_name：{received}。"


def _date_coverage(dates: tuple[str, ...], values: dict[str, float]) -> float:
    if not dates:
        return 0.0
    covered = sum(1 for trade_date in dates if trade_date in values)
    return covered / len(dates)


def _factor_coverage_failure_message(
    factor_coverage: dict[str, Any],
    *,
    threshold: float,
) -> str:
    weakest = min(
        factor_coverage.get("checks", ()),
        key=lambda item: float(item.get("coverage", 1.0)),
        default={},
    )
    if not weakest:
        return "回测前因子覆盖率不足，无法可靠运行真实数据回测。"
    return (
        "回测前因子覆盖率不足，已停止运行："
        f"{weakest.get('factor')} 在信号日 {weakest.get('signal_date')} "
        f"覆盖 {float(weakest.get('coverage', 0.0)):.1%}，"
        f"低于最低要求 {threshold:.0%}。"
    )


def _factor_coverage_warnings(
    factor_coverage: dict[str, Any],
    *,
    warning_threshold: float,
) -> list[dict[str, Any]]:
    checks = factor_coverage.get("checks", ())
    if not checks:
        return []
    weak_checks = [
        item
        for item in checks
        if float(item.get("coverage", 1.0)) < warning_threshold
    ]
    if not weak_checks:
        return []
    weakest = min(weak_checks, key=lambda item: float(item.get("coverage", 1.0)))
    return [
        {
            "code": "LOW_FACTOR_COVERAGE_WARNING",
            "message": (
                "部分信号日因子覆盖率偏低："
                f"{weakest.get('factor')} 在 {weakest.get('signal_date')} "
                f"覆盖 {float(weakest.get('coverage', 0.0)):.1%}。"
            ),
            "coverage": float(weakest.get("coverage", 0.0)),
            "factor": weakest.get("factor"),
            "signal_date": weakest.get("signal_date"),
        }
    ]


def _intraday_return(row: sqlite3.Row) -> float:
    open_price = _optional_float(row["open"])
    close_price = _optional_float(row["close"])
    if open_price is not None and open_price > 0 and close_price is not None:
        return close_price / open_price - 1.0
    return float(row["pct_chg"] or 0.0) / 100.0


def _execution_price(row: sqlite3.Row) -> float:
    open_price = _optional_float(row["open"])
    if open_price is not None and open_price > 0:
        return open_price
    close_price = _optional_float(row["close"])
    return close_price if close_price is not None else 0.0


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_error_message(exc: Exception) -> str:
    message = str(exc)
    message = re.sub(r"(传过来的是)[A-Za-z0-9_-]+", r"\1***", message)
    message = re.sub(r"(token[=:：]\s*)[A-Za-z0-9_-]+", r"\1***", message, flags=re.IGNORECASE)
    return message


def _required_factors(plan: ExecutionPlan) -> tuple[str, ...]:
    names = {
        condition.factor
        for condition in (*plan.filters, *plan.entry_rules, *plan.exit_rules)
    }
    names.update(rule.factor for rule in plan.ranking)
    return tuple(sorted(names))


def _required_percentile_factors(plan: ExecutionPlan) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                condition.factor
                for condition in (*plan.filters, *plan.entry_rules, *plan.exit_rules)
                if condition.unit == "percentile"
            }
        )
    )


def _has_factor_values(
    symbol: str,
    factors: tuple[str, ...],
    values: dict[str, dict[str, float]],
) -> bool:
    return all(symbol in values[factor] for factor in factors)


def _passes_all(
    symbol: str,
    conditions: tuple[FactorCondition, ...],
    values: dict[str, dict[str, float]],
    percentile_values: dict[str, dict[str, float]],
) -> bool:
    return all(
        _passes(_condition_value(symbol, condition, values, percentile_values), condition)
        for condition in conditions
    )


def _triggers_exit(
    symbol: str,
    conditions: tuple[FactorCondition, ...],
    values: dict[str, dict[str, float]],
    percentile_values: dict[str, dict[str, float]],
) -> bool:
    return any(
        _passes(_condition_value(symbol, condition, values, percentile_values), condition)
        for condition in conditions
    )


def _condition_value(
    symbol: str,
    condition: FactorCondition,
    values: dict[str, dict[str, float]],
    percentile_values: dict[str, dict[str, float]],
) -> float:
    if condition.unit == "percentile":
        return percentile_values[condition.factor][symbol]
    return values[condition.factor][symbol]


def _passes(value: float, condition: FactorCondition) -> bool:
    if condition.operator is Operator.GREATER_THAN:
        return value > condition.value
    if condition.operator is Operator.GREATER_EQUAL:
        return value >= condition.value
    if condition.operator is Operator.LESS_THAN:
        return value < condition.value
    if condition.operator is Operator.LESS_EQUAL:
        return value <= condition.value
    if condition.operator is Operator.EQUAL:
        return value == condition.value
    return False


def _percentile(value: float, sample: list[float]) -> float:
    if not sample:
        return 0.0
    return sum(1 for item in sample if item <= value) / len(sample)


def _rank_and_select(
    candidates: list[str],
    plan: ExecutionPlan,
    values: dict[str, dict[str, float]],
    limit: int | None = None,
) -> list[str]:
    scores = []
    for symbol in candidates:
        score = 0.0
        for rule in plan.ranking:
            direction = -1 if rule.direction is SortDirection.ASC else 1
            score += direction * values[rule.factor][symbol] * rule.weight
        scores.append((score, symbol))
    count = plan.selection.count if limit is None else min(limit, plan.selection.count)
    return [
        symbol
        for _, symbol in sorted(scores, key=lambda item: (item[0], item[1]), reverse=True)[:count]
    ]


def _equal_weights(selected: list[str], plan: ExecutionPlan) -> dict[str, float]:
    if not selected:
        return {}
    weight = min(1.0 / len(selected), plan.portfolio.max_position_weight or 1.0)
    return {symbol: weight for symbol in selected}


def _rebalance_dates(dates: tuple[str, ...], frequency: RebalanceFrequency) -> set[str]:
    if frequency is RebalanceFrequency.TRIGGERED:
        return set(dates[1:] or dates)
    interval = {
        RebalanceFrequency.WEEKLY: 5,
        RebalanceFrequency.MONTHLY: 21,
        RebalanceFrequency.QUARTERLY: 63,
    }[frequency]
    return {
        trade_date
        for index, trade_date in enumerate(dates)
        if index > 0 and (index - 1) % interval == 0
    }


def _one_way_turnover(previous: dict[str, float], target: dict[str, float]) -> float:
    symbols = set(previous) | set(target)
    return sum(abs(target.get(symbol, 0.0) - previous.get(symbol, 0.0)) for symbol in symbols) / 2


def _transaction_cost_rate(
    previous: dict[str, float],
    target: dict[str, float],
    commission_rate: float,
    tax_rate: float,
    slippage_rate: float,
) -> float:
    symbols = set(previous) | set(target)
    buy_turnover = sum(max(target.get(symbol, 0.0) - previous.get(symbol, 0.0), 0.0) for symbol in symbols)
    sell_turnover = sum(max(previous.get(symbol, 0.0) - target.get(symbol, 0.0), 0.0) for symbol in symbols)
    return (buy_turnover + sell_turnover) * (commission_rate + slippage_rate) + sell_turnover * tax_rate


def _metadata(
    plan: ExecutionPlan,
    start_date: str | None,
    end_date: str | None,
    engine_version: str,
) -> dict[str, Any]:
    metadata = {
        "run_id": f"run_{plan.strategy_hash[:12]}",
        "strategy_id": plan.plan_id,
        "strategy_hash": plan.strategy_hash,
        "start_date": _iso_date(start_date) if start_date else None,
        "end_date": _iso_date(end_date) if end_date else None,
        "frequency": "daily",
        "benchmark_code": plan.backtest.benchmark,
        "initial_capital": plan.backtest.initial_capital,
        "currency": "CNY",
        "random_seed": None,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    metadata.update(plan.versions)
    metadata["engine_version"] = engine_version
    metadata["data_version"] = "local-sqlite+daily-basic+live-factor"
    return metadata


def _failed(
    plan: ExecutionPlan,
    code: str,
    message: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> BacktestFacts:
    return BacktestFacts(
        schema_version="1.0.0",
        status=DataStatus.FAILED,
        metadata=_metadata(plan, start_date, end_date, "sqlite-factor-live-v1"),
        data_quality={"expected_trade_days": 0, "actual_trade_days": 0},
        portfolio_daily=(),
        warnings=({"code": code, "message": message},),
    )


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _iso_date(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
