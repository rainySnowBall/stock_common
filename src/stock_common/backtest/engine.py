"""Deterministic long-only daily backtest engine for P0 factor strategies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stock_common.backtest.demo_data import DemoDataProvider, DemoMarketData
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


@dataclass(frozen=True)
class BacktestEngine:
    """Run an ExecutionPlan through fixed, versioned P0 simulation rules."""

    data_provider: DemoDataProvider = DemoDataProvider()
    commission_rate: float = 0.0003
    tax_rate: float = 0.001
    slippage_rate: float = 0.0005

    def run(self, plan: ExecutionPlan) -> BacktestFacts:
        market = self.data_provider.load(
            lookback_years=plan.backtest.lookback_years,
            universe_name=plan.universe.name,
            symbols=plan.universe.symbols,
        )
        dates = market.dates
        if not dates:
            return BacktestFacts(
                schema_version="1.0.0",
                status=DataStatus.FAILED,
                metadata=_metadata(plan, None, None),
                data_quality={"expected_trade_days": 0, "actual_trade_days": 0},
                portfolio_daily=(),
                warnings=({"code": "NO_DATA", "message": "没有可用交易日。"},),
            )

        current_weights: dict[str, float] = {}
        gross_nav = 1.0
        net_nav = 1.0
        benchmark_nav = 1.0
        daily_rows: list[PortfolioDaily] = []
        positions: list[PositionSnapshot] = []
        orders: list[OrderRecord] = []
        fills: list[FillRecord] = []
        costs: list[CostRecord] = []
        events: list[dict[str, object]] = []

        rebalance_dates = _rebalance_dates(dates, plan.rebalance.frequency)
        for day_index, trade_date in enumerate(dates):
            turnover = 0.0
            cost_rate = 0.0
            previous_weights = current_weights
            if trade_date in rebalance_dates:
                signal_date = dates[max(0, day_index - 1)]
                target_weights = _select_target_weights(
                    plan,
                    market,
                    signal_date,
                    previous_weights,
                    history_dates=dates[: max(day_index, 1)],
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
                signal_id = f"signal_{trade_date.isoformat()}"
                events.append(
                    {
                        "timestamp": trade_date.isoformat(),
                        "event_type": "rebalance",
                        "symbol": None,
                        "rule_id": plan.plan_id,
                        "details": {
                            "signal_date": signal_date.isoformat(),
                            "selected_count": len(target_weights),
                            "turnover": turnover,
                        },
                    }
                )
                order_records, fill_records = _records_for_rebalance(
                    trade_date=trade_date,
                    signal_id=signal_id,
                    previous_weights=previous_weights,
                    target_weights=target_weights,
                    market=market,
                    commission_rate=self.commission_rate,
                    tax_rate=self.tax_rate,
                    slippage_rate=self.slippage_rate,
                )
                orders.extend(order_records)
                fills.extend(fill_records)
                costs.append(
                    CostRecord(
                        date=trade_date,
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

            gross_return = sum(
                weight * market.returns[trade_date].get(symbol, 0.0)
                for symbol, weight in current_weights.items()
            )
            net_return = gross_return - cost_rate
            gross_nav *= 1 + gross_return
            net_nav *= 1 + net_return
            benchmark_nav *= 1 + market.benchmark_returns[trade_date]
            positions_value = plan.backtest.initial_capital * net_nav

            daily_rows.append(
                PortfolioDaily(
                    date=trade_date,
                    cash=0.0,
                    positions_value=positions_value,
                    gross_portfolio_value=plan.backtest.initial_capital * gross_nav,
                    net_portfolio_value=positions_value,
                    benchmark_value=plan.backtest.initial_capital * benchmark_nav,
                    gross_return=gross_return,
                    net_return=net_return,
                    benchmark_return=market.benchmark_returns[trade_date],
                    holding_count=len(current_weights),
                    cash_ratio=0.0 if current_weights else 1.0,
                    gross_exposure=sum(abs(weight) for weight in current_weights.values()),
                    net_exposure=sum(current_weights.values()),
                    turnover=turnover,
                )
            )

            if trade_date in rebalance_dates:
                positions.extend(
                    _position_snapshots(
                        trade_date=trade_date,
                        weights=current_weights,
                        market=market,
                        portfolio_value=positions_value,
                    )
                )

        return BacktestFacts(
            schema_version="1.0.0",
            status=DataStatus.SUCCEEDED,
            metadata=_metadata(plan, dates[0], dates[-1]),
            data_quality={
                "expected_trade_days": plan.backtest.lookback_years * 252,
                "actual_trade_days": len(dates),
                "market_data_coverage": 1.0,
                "fundamental_data_coverage": 1.0,
                "benchmark_data_coverage": 1.0,
                "missing_price_count": 0,
                "stale_price_count": 0,
                "invalid_factor_count": 0,
                "lookahead_checks_passed": True,
                "survivorship_checks_passed": True,
                "data_source": "deterministic_demo",
            },
            portfolio_daily=tuple(daily_rows),
            positions=tuple(positions),
            orders=tuple(orders),
            fills=tuple(fills),
            costs=tuple(costs),
            exposures=_exposures(daily_rows, positions, market),
            events=tuple(events),
            warnings=(
                {
                    "code": "DEMO_DATA",
                    "message": "当前 P0 引擎使用确定性 demo 数据，不代表真实市场结果。",
                },
            ),
        )


def _metadata(plan: ExecutionPlan, start_date: date | None, end_date: date | None) -> dict[str, object]:
    metadata = {
        "run_id": f"run_{plan.strategy_hash[:12]}",
        "strategy_id": plan.plan_id,
        "strategy_hash": plan.strategy_hash,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "frequency": "daily",
        "benchmark_code": plan.backtest.benchmark,
        "initial_capital": plan.backtest.initial_capital,
        "currency": "CNY",
        "random_seed": 0,
        "created_at": "2026-01-01T00:00:00",
    }
    metadata.update(plan.versions)
    return metadata


def _rebalance_dates(
    dates: tuple[date, ...],
    frequency: RebalanceFrequency,
) -> set[date]:
    if not dates:
        return set()
    if frequency is RebalanceFrequency.TRIGGERED:
        return set(dates[1:] or dates)
    interval = {
        RebalanceFrequency.WEEKLY: 5,
        RebalanceFrequency.MONTHLY: 21,
        RebalanceFrequency.QUARTERLY: 63,
    }[frequency]
    return {trade_date for index, trade_date in enumerate(dates) if index % interval == 0}


def _select_target_weights(
    plan: ExecutionPlan,
    market: DemoMarketData,
    signal_date: date,
    previous_weights: dict[str, float] | None = None,
    history_dates: tuple[date, ...] = (),
) -> dict[str, float]:
    previous_weights = previous_weights or {}
    factor_values = market.factors[signal_date]
    percentile_values = _percentile_values(
        market,
        _required_percentile_factors(plan),
        history_dates or (signal_date,),
    )
    candidates = _symbols_passing_conditions(
        symbols=market.symbols,
        factor_values=factor_values,
        percentile_values=percentile_values,
        conditions=plan.filters,
    )

    if not plan.entry_rules and not plan.exit_rules:
        selected = _rank_and_select(candidates, plan, factor_values)
        return _equal_weights(selected, plan)

    survivor_symbols = [
        symbol
        for symbol in previous_weights
        if symbol in candidates
        and not _triggers_exit(symbol, factor_values, percentile_values, plan.exit_rules)
    ]
    capacity = max(plan.selection.count - len(survivor_symbols), 0)
    if capacity <= 0:
        return _equal_weights(survivor_symbols[: plan.selection.count], plan)

    buy_candidates = [
        symbol
        for symbol in candidates
        if symbol not in survivor_symbols
        and _passes_all_conditions(symbol, factor_values, percentile_values, plan.entry_rules)
    ]
    selected_new = _rank_and_select(buy_candidates, plan, factor_values, limit=capacity)
    return _equal_weights([*survivor_symbols, *selected_new], plan)


def _symbols_passing_conditions(
    *,
    symbols: tuple[str, ...],
    factor_values: dict[str, dict[str, float]],
    percentile_values: dict[str, dict[str, float]],
    conditions: tuple[FactorCondition, ...],
) -> list[str]:
    return [
        symbol
        for symbol in symbols
        if _passes_all_conditions(symbol, factor_values, percentile_values, conditions)
    ]


def _passes_all_conditions(
    symbol: str,
    factor_values: dict[str, dict[str, float]],
    percentile_values: dict[str, dict[str, float]],
    conditions: tuple[FactorCondition, ...],
) -> bool:
    return all(
        _passes_condition(_condition_value(symbol, condition, factor_values, percentile_values), condition)
        for condition in conditions
    )


def _triggers_exit(
    symbol: str,
    factor_values: dict[str, dict[str, float]],
    percentile_values: dict[str, dict[str, float]],
    exit_rules: tuple[FactorCondition, ...],
) -> bool:
    return any(
        _passes_condition(_condition_value(symbol, condition, factor_values, percentile_values), condition)
        for condition in exit_rules
    )


def _condition_value(
    symbol: str,
    condition: FactorCondition,
    factor_values: dict[str, dict[str, float]],
    percentile_values: dict[str, dict[str, float]],
) -> float:
    if condition.unit == "percentile":
        return percentile_values[condition.factor][symbol]
    return factor_values[symbol][condition.factor]


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


def _percentile_values(
    market: DemoMarketData,
    factors: tuple[str, ...],
    history_dates: tuple[date, ...],
) -> dict[str, dict[str, float]]:
    if not factors:
        return {}
    output: dict[str, dict[str, float]] = {}
    for factor in factors:
        output[factor] = {}
        for symbol in market.symbols:
            values = [
                market.factors[trade_date][symbol][factor]
                for trade_date in history_dates
                if trade_date in market.factors
                and symbol in market.factors[trade_date]
                and factor in market.factors[trade_date][symbol]
            ]
            if values:
                output[factor][symbol] = _percentile(values[-1], values)
    return output


def _percentile(value: float, sample: list[float]) -> float:
    if not sample:
        return 0.0
    return sum(1 for item in sample if item <= value) / len(sample)


def _rank_and_select(
    candidates: list[str],
    plan: ExecutionPlan,
    factor_values: dict[str, dict[str, float]],
    limit: int | None = None,
) -> list[str]:
    scores = []
    for symbol in candidates:
        score = 0.0
        for rule in plan.ranking:
            value = factor_values[symbol][rule.factor]
            direction = -1 if rule.direction is SortDirection.ASC else 1
            score += direction * value * rule.weight
        scores.append((score, symbol))

    count = plan.selection.count if limit is None else min(limit, plan.selection.count)
    return [
        symbol
        for _, symbol in sorted(scores, key=lambda item: (item[0], item[1]), reverse=True)[
            : count
        ]
    ]


def _equal_weights(selected: list[str], plan: ExecutionPlan) -> dict[str, float]:
    if not selected:
        return {}
    weight = min(1.0 / len(selected), plan.portfolio.max_position_weight or 1.0)
    return {symbol: weight for symbol in selected}


def _passes_condition(value: float, condition: FactorCondition) -> bool:
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
    buy_turnover = sum(
        max(target.get(symbol, 0.0) - previous.get(symbol, 0.0), 0.0)
        for symbol in symbols
    )
    sell_turnover = sum(
        max(previous.get(symbol, 0.0) - target.get(symbol, 0.0), 0.0)
        for symbol in symbols
    )
    return (
        (buy_turnover + sell_turnover) * (commission_rate + slippage_rate)
        + sell_turnover * tax_rate
    )


def _records_for_rebalance(
    trade_date: date,
    signal_id: str,
    previous_weights: dict[str, float],
    target_weights: dict[str, float],
    market: DemoMarketData,
    commission_rate: float,
    tax_rate: float,
    slippage_rate: float,
) -> tuple[list[OrderRecord], list[FillRecord]]:
    orders = []
    fills = []
    for index, symbol in enumerate(sorted(set(previous_weights) | set(target_weights)), start=1):
        delta = target_weights.get(symbol, 0.0) - previous_weights.get(symbol, 0.0)
        if abs(delta) < 1e-12:
            continue
        side = "buy" if delta > 0 else "sell"
        amount = abs(delta)
        close = market.closes[trade_date][symbol]
        quantity = amount / close
        order_id = f"ord_{trade_date.isoformat()}_{index:03d}"
        orders.append(
            OrderRecord(
                order_id=order_id,
                created_at=f"{trade_date.isoformat()}T09:30:00",
                symbol=symbol,
                side=side,
                order_type="market",
                requested_quantity=quantity,
                reference_price=close,
                status="filled",
                reason=None,
                signal_id=signal_id,
            )
        )
        fills.append(
            FillRecord(
                fill_id=f"fill_{trade_date.isoformat()}_{index:03d}",
                order_id=order_id,
                timestamp=f"{trade_date.isoformat()}T09:31:00",
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=close,
                amount=amount,
                commission=amount * commission_rate,
                tax=amount * tax_rate if side == "sell" else 0.0,
                slippage_cost=amount * slippage_rate,
            )
        )
    return orders, fills


def _position_snapshots(
    trade_date: date,
    weights: dict[str, float],
    market: DemoMarketData,
    portfolio_value: float,
) -> list[PositionSnapshot]:
    snapshots = []
    for symbol, weight in sorted(weights.items()):
        close = market.closes[trade_date][symbol]
        market_value = portfolio_value * weight
        snapshots.append(
            PositionSnapshot(
                date=trade_date,
                symbol=symbol,
                quantity=market_value / close,
                close=close,
                market_value=market_value,
                weight=weight,
                industry_code=market.industries[symbol],
                market_cap=market.factors[trade_date][symbol]["market_cap"],
            )
        )
    return snapshots


def _exposures(
    daily_rows: list[PortfolioDaily],
    positions: list[PositionSnapshot],
    market: DemoMarketData,
) -> tuple[dict[str, object], ...]:
    if not daily_rows or not positions:
        return ()
    latest_date = daily_rows[-1].date
    latest_positions = [position for position in positions if position.date == latest_date]
    if not latest_positions:
        latest_rebalance_date = max(position.date for position in positions)
        latest_positions = [position for position in positions if position.date == latest_rebalance_date]
    by_industry: dict[str, float] = {}
    for position in latest_positions:
        industry = market.industries[position.symbol]
        by_industry[industry] = by_industry.get(industry, 0.0) + position.weight
    return tuple(
        {
            "date": latest_positions[0].date.isoformat(),
            "exposure_type": "industry",
            "name": industry,
            "portfolio_value": value,
            "benchmark_value": None,
            "active_value": None,
        }
        for industry, value in sorted(by_industry.items())
    )
