import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.backtest.compiler import compile_from_text
from stock_common.backtest.real_engine import SQLiteFactorBacktestEngine
from stock_common.backtest.schemas import (
    BacktestSpec,
    DataStatus,
    ExecutionPlan,
    PortfolioSpec,
    RankingRule,
    RebalanceFrequency,
    RebalanceSpec,
    SelectionSpec,
    UniverseSpec,
)


class FakeFactorClient:
    def __init__(self) -> None:
        self.calls = []

    def factor_value(self, *, factor_name, trade_date, ts_code=None):
        self.calls.append(
            {"factor_name": factor_name, "trade_date": trade_date, "ts_code": ts_code}
        )
        values = {
            "000001.SZ": 0.10,
            "000002.SZ": 0.20,
            "000003.SZ": 0.30,
        }
        return [
            {"ts_code": symbol, "factor_value": value}
            for symbol, value in values.items()
        ]


class FailingFactorClient:
    def factor_value(self, *, factor_name, trade_date, ts_code=None):
        raise RuntimeError("token不对，您传过来的是abcdefghijklmnopqrstuvwxyz123456")


class SparseFactorClient:
    def __init__(self) -> None:
        self.calls = []

    def factor_value(self, *, factor_name, trade_date, ts_code=None):
        self.calls.append(
            {"factor_name": factor_name, "trade_date": trade_date, "ts_code": ts_code}
        )
        return [{"ts_code": "000001.SZ", "factor_value": 0.10}]


class DateAwareFactorClient:
    def __init__(self) -> None:
        self.calls = []

    def factor_value(self, *, factor_name, trade_date, ts_code=None):
        self.calls.append(
            {"factor_name": factor_name, "trade_date": trade_date, "ts_code": ts_code}
        )
        day = int(str(trade_date)[-2:])
        value = -1.0 if day >= 22 else float(day)
        symbol = ts_code or "000001.SZ"
        return [{"ts_code": symbol, "factor_value": value}]


class SQLiteFactorBacktestEngineTests(unittest.TestCase):
    def test_runs_against_sqlite_prices_and_live_factor_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market.sqlite3"
            _write_market_fixture(db_path)
            factor_client = FakeFactorClient()
            _, validation, plan = compile_from_text(
                "从全A里选择ROE最高的2只股票，每月调仓，回测过去1年"
            )
            self.assertTrue(validation.is_valid)
            assert plan is not None

            facts = SQLiteFactorBacktestEngine(
                database_path=db_path,
                factor_client=factor_client,
            ).run(plan)

            self.assertEqual(facts.status, DataStatus.SUCCEEDED)
            self.assertEqual(
                facts.data_quality["data_source"],
                "sqlite_daily_qfq+live_tushare_factor_value",
            )
            self.assertEqual(facts.portfolio_daily[0].holding_count, 0)
            self.assertEqual(facts.portfolio_daily[1].holding_count, 2)
            self.assertEqual(factor_client.calls[0]["factor_name"], "roe_ttm")
            self.assertEqual(factor_client.calls[0]["trade_date"], "20240101")
            self.assertEqual(
                {position.symbol for position in facts.positions[:2]},
                {"000002.SZ", "000003.SZ"},
            )
            self.assertTrue(facts.orders)
            self.assertEqual(facts.orders[0].created_at, "2024-01-02T09:30:00")

    def test_queries_live_factor_records_instead_of_sqlite_daily_basic_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market.sqlite3"
            _write_market_fixture(db_path)
            _write_daily_basic_fixture(db_path)
            factor_client = FakeFactorClient()
            plan = _ranking_plan(
                RankingRule(factor="earnings_to_price", direction="desc"),
                count=1,
            )

            facts = SQLiteFactorBacktestEngine(
                database_path=db_path,
                factor_client=factor_client,
            ).run(plan)

            self.assertEqual(facts.status, DataStatus.SUCCEEDED)
            self.assertTrue(factor_client.calls)
            self.assertEqual(factor_client.calls[0]["factor_name"], "earnings_to_price")
            self.assertTrue(facts.positions)
            self.assertEqual(facts.positions[0].symbol, "000003.SZ")

    def test_can_opt_into_sqlite_daily_basic_factor_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market.sqlite3"
            _write_market_fixture(db_path)
            _write_daily_basic_fixture(db_path)
            factor_client = FakeFactorClient()
            plan = _ranking_plan(
                RankingRule(factor="earnings_to_price", direction="desc"),
                count=1,
            )

            facts = SQLiteFactorBacktestEngine(
                database_path=db_path,
                factor_client=factor_client,
                use_local_daily_basic_factors=True,
            ).run(plan)

            self.assertEqual(facts.status, DataStatus.SUCCEEDED)
            self.assertEqual(factor_client.calls, [])
            self.assertTrue(facts.positions)
            self.assertEqual(facts.positions[0].symbol, "000002.SZ")

    def test_rejects_universe_without_historical_components(self) -> None:
        _, validation, plan = compile_from_text(
            "从沪深300里选择ROE最高的2只股票，每月调仓，回测过去1年"
        )
        self.assertTrue(validation.is_valid)
        assert plan is not None

        facts = SQLiteFactorBacktestEngine(
            database_path=Path("missing.sqlite3"),
            factor_client=FakeFactorClient(),
        ).run(plan)

        self.assertEqual(facts.status, DataStatus.FAILED)
        self.assertEqual(facts.warnings[0]["code"], "UNSUPPORTED_REAL_UNIVERSE")

    def test_runs_custom_factor_from_factor_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market.sqlite3"
            _write_market_fixture(db_path)
            _, validation, plan = compile_from_text(
                "从全A里选择ROE最高的2只股票，每月调仓，回测过去1年"
            )
            self.assertTrue(validation.is_valid)
            assert plan is not None
            custom_plan = replace(
                plan,
                ranking=(RankingRule(factor="earnings_to_price", direction="desc"),),
                filters=(),
                entry_rules=(),
                exit_rules=(),
            )
            factor_client = FakeFactorClient()

            facts = SQLiteFactorBacktestEngine(
                database_path=db_path,
                factor_client=factor_client,
            ).run(custom_plan)

            self.assertEqual(facts.status, DataStatus.SUCCEEDED)
            self.assertEqual(factor_client.calls[0]["factor_name"], "earnings_to_price")

    def test_rejects_low_factor_coverage_before_backtest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market.sqlite3"
            _write_market_fixture(db_path)
            factor_client = SparseFactorClient()
            plan = _ranking_plan(RankingRule(factor="roe_ttm", direction="desc"), count=2)

            facts = SQLiteFactorBacktestEngine(
                database_path=db_path,
                factor_client=factor_client,
            ).run(plan)

            self.assertEqual(facts.status, DataStatus.FAILED)
            self.assertEqual(facts.warnings[0]["code"], "LOW_FACTOR_COVERAGE")
            self.assertEqual(facts.orders, ())
            self.assertTrue(factor_client.calls)

    def test_runs_single_stock_percentile_entry_exit_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market.sqlite3"
            _write_market_fixture(db_path)
            _, validation, plan = compile_from_text(
                "回测 000001 PE后20%买入，PE前20%卖出，每月调仓，过去1年"
            )
            self.assertTrue(validation.is_valid)
            assert plan is not None
            factor_client = DateAwareFactorClient()

            facts = SQLiteFactorBacktestEngine(
                database_path=db_path,
                factor_client=factor_client,
            ).run(plan)

            self.assertEqual(facts.status, DataStatus.SUCCEEDED)
            self.assertEqual(plan.universe.symbols, ("000001.SZ",))
            self.assertIn("buy", [order.side for order in facts.orders])
            self.assertIn("sell", [order.side for order in facts.orders])
            self.assertTrue(
                any(call["ts_code"] == "000001.SZ" for call in factor_client.calls)
            )

    def test_masks_token_in_factor_fetch_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market.sqlite3"
            _write_market_fixture(db_path)
            _, validation, plan = compile_from_text(
                "从全A里选择ROE最高的2只股票，每月调仓，回测过去1年"
            )
            self.assertTrue(validation.is_valid)
            assert plan is not None

            facts = SQLiteFactorBacktestEngine(
                database_path=db_path,
                factor_client=FailingFactorClient(),
            ).run(plan)

            self.assertEqual(facts.status, DataStatus.FAILED)
            self.assertIn("传过来的是***", facts.warnings[0]["message"])
            self.assertNotIn("abcdefghijklmnopqrstuvwxyz", facts.warnings[0]["message"])


def _ranking_plan(rule: RankingRule, *, count: int = 2) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="plan_test",
        strategy_hash="test_strategy_hash",
        universe=UniverseSpec(name="all_a_demo"),
        filters=(),
        ranking=(rule,),
        selection=SelectionSpec(count=count),
        portfolio=PortfolioSpec(max_position_weight=1.0 / count),
        rebalance=RebalanceSpec(frequency=RebalanceFrequency.MONTHLY),
        backtest=BacktestSpec(lookback_years=1, benchmark="000300.SH"),
        versions={},
    )


def _write_market_fixture(db_path: Path) -> None:
    dates = tuple(f"202401{day:02d}" for day in range(1, 25))
    symbols = ("000001.SZ", "000002.SZ", "000003.SZ")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_basic (
                ts_code TEXT PRIMARY KEY,
                list_status TEXT,
                list_date TEXT,
                delist_date TEXT
            );
            CREATE TABLE daily_qfq (
                ts_code TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                pre_close REAL,
                change REAL,
                pct_chg REAL,
                vol REAL,
                amount REAL,
                source TEXT,
                fetched_at TEXT,
                PRIMARY KEY (ts_code, trade_date)
            );
            """
        )
        conn.executemany(
            "INSERT INTO stock_basic VALUES (?, 'L', '20200101', '')",
            [(symbol,) for symbol in symbols],
        )
        for index, trade_date in enumerate(dates):
            rows = []
            for offset, symbol in enumerate((*symbols, "000300.SH"), start=1):
                pre_close = 10.0 + offset + index * 0.1
                open_price = pre_close * 1.001
                close = open_price * (1.002 + offset * 0.001)
                pct_chg = (close / pre_close - 1.0) * 100.0
                rows.append(
                    (
                        symbol,
                        trade_date,
                        open_price,
                        close,
                        close,
                        close,
                        pre_close,
                        close - pre_close,
                        pct_chg,
                        1000.0,
                        10000.0,
                        "fixture",
                        "2024-01-01T00:00:00Z",
                    )
                )
            conn.executemany(
                """
                INSERT INTO daily_qfq (
                    ts_code, trade_date, open, high, low, close, pre_close,
                    change, pct_chg, vol, amount, source, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        conn.commit()


def _write_daily_basic_fixture(db_path: Path) -> None:
    dates = tuple(f"202401{day:02d}" for day in range(1, 25))
    pe_ttm_values = {
        "000001.SZ": 30.0,
        "000002.SZ": 8.0,
        "000003.SZ": 12.0,
    }
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE daily_basic (
                ts_code TEXT,
                trade_date TEXT,
                pe REAL,
                pe_ttm REAL,
                pb REAL,
                total_mv REAL,
                circ_mv REAL,
                PRIMARY KEY (ts_code, trade_date)
            );
            """
        )
        rows = []
        for trade_date in dates:
            for index, (symbol, pe_ttm) in enumerate(pe_ttm_values.items(), start=1):
                rows.append(
                    (
                        symbol,
                        trade_date,
                        pe_ttm + 1.0,
                        pe_ttm,
                        1.0 + index * 0.1,
                        1000.0 * index,
                        800.0 * index,
                    )
                )
        conn.executemany(
            """
            INSERT INTO daily_basic (
                ts_code, trade_date, pe, pe_ttm, pb, total_mv, circ_mv
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


if __name__ == "__main__":
    unittest.main()
