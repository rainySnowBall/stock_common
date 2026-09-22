"""Deterministic demo data provider for P0 backtest development."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class DemoMarketData:
    dates: tuple[date, ...]
    symbols: tuple[str, ...]
    returns: dict[date, dict[str, float]]
    closes: dict[date, dict[str, float]]
    factors: dict[date, dict[str, dict[str, float]]]
    benchmark_returns: dict[date, float]
    benchmark_values: dict[date, float]
    industries: dict[str, str]


@dataclass(frozen=True)
class DemoDataProvider:
    """Generate stable daily stock and benchmark data without external IO."""

    symbol_count: int = 80
    latest_date: date = date(2025, 12, 31)

    def load(
        self,
        lookback_years: int,
        universe_name: str,
        symbols: tuple[str, ...] = (),
    ) -> DemoMarketData:
        dates = _business_days(self.latest_date, lookback_years * 252)
        symbols = symbols or _symbols_for_universe(universe_name, self.symbol_count)
        returns: dict[date, dict[str, float]] = {}
        closes: dict[date, dict[str, float]] = {}
        factors: dict[date, dict[str, dict[str, float]]] = {}
        benchmark_returns: dict[date, float] = {}
        benchmark_values: dict[date, float] = {}
        industries = {
            symbol: f"industry_{(index % 6) + 1:02d}"
            for index, symbol in enumerate(symbols)
        }

        previous_closes = {
            symbol: 10.0 + index * 0.73
            for index, symbol in enumerate(symbols)
        }
        benchmark_value = 1.0
        for day_index, trade_date in enumerate(dates):
            daily_returns = {}
            daily_closes = {}
            daily_factors = {}
            market_return = (
                0.00018
                + 0.006 * math.sin(day_index / 17.0)
                + 0.0025 * math.cos(day_index / 41.0)
            )
            benchmark_return = market_return + 0.001 * math.sin(day_index / 9.0)
            benchmark_value *= 1 + benchmark_return
            benchmark_returns[trade_date] = benchmark_return
            benchmark_values[trade_date] = benchmark_value

            for index, symbol in enumerate(symbols):
                quality = ((index * 37) % 100) / 100.0
                cheapness = ((index * 19 + 11) % 100) / 100.0
                size = ((index * 13 + 7) % 100) / 100.0
                seasonal = math.sin((day_index + index * 3) / 29.0)
                idiosyncratic = 0.004 * math.sin(day_index / (8.0 + (index % 7)) + index)
                stock_return = (
                    market_return
                    + 0.00055 * (quality - 0.5)
                    + 0.00035 * (cheapness - 0.5)
                    - 0.0002 * (size - 0.5)
                    + idiosyncratic
                )
                close = max(1.0, previous_closes[symbol] * (1 + stock_return))
                previous_closes[symbol] = close

                roe_ttm = 0.055 + 0.19 * quality + 0.012 * seasonal
                pe_ttm = 8.0 + 34.0 * (1 - cheapness) + 1.2 * math.cos(day_index / 31.0)
                pb = 0.8 + 5.5 * (1 - cheapness * 0.7) + 0.4 * quality
                market_cap = 6_000_000_000 + 120_000_000_000 * size
                momentum_20d = stock_return * 20 + 0.025 * math.sin(day_index / 13.0 + index)

                daily_returns[symbol] = stock_return
                daily_closes[symbol] = close
                daily_factors[symbol] = {
                    "roe_ttm": roe_ttm,
                    "pe_ttm": pe_ttm,
                    "earnings_to_price": 1.0 / pe_ttm,
                    "pb": pb,
                    "market_cap": market_cap,
                    "momentum_20d": momentum_20d,
                }

            returns[trade_date] = daily_returns
            closes[trade_date] = daily_closes
            factors[trade_date] = daily_factors

        return DemoMarketData(
            dates=dates,
            symbols=symbols,
            returns=returns,
            closes=closes,
            factors=factors,
            benchmark_returns=benchmark_returns,
            benchmark_values=benchmark_values,
            industries=industries,
        )


def _business_days(end_date: date, count: int) -> tuple[date, ...]:
    days = []
    current = end_date
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return tuple(reversed(days))


def _symbols_for_universe(universe_name: str, symbol_count: int) -> tuple[str, ...]:
    if universe_name == "csi300_demo":
        prefix = "CSI300"
    elif universe_name == "csi500_demo":
        prefix = "CSI500"
    else:
        prefix = "CNA"
    return tuple(f"{prefix}{index:03d}.SH" for index in range(1, symbol_count + 1))
