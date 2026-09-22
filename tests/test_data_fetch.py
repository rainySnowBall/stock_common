import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.data_fetch import (
    DailyMarketUpdater,
    DataFetchConfig,
    FactorFetchClient,
    MarketDataRepository,
    next_daily_run,
)
from stock_common.env import load_dotenv


class FakeMarketClient:
    def __init__(self) -> None:
        self.daily_calls = []
        self.daily_basic_calls = []

    def stock_basic(self, statuses):
        return [
            {
                "ts_code": "000001.SZ",
                "symbol": "000001",
                "name": "平安银行",
                "list_status": "L",
                "list_date": "19910403",
            },
            {
                "ts_code": "600000.SH",
                "symbol": "600000",
                "name": "浦发银行",
                "list_status": "L",
                "list_date": "19991110",
            },
        ]

    def qfq_daily(self, *, ts_code, start_date, end_date):
        self.daily_calls.append(
            {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}
        )
        return [
            {
                "ts_code": ts_code,
                "trade_date": start_date,
                "open": 10.0,
                "high": 11.0,
                "low": 9.8,
                "close": 10.5,
                "pre_close": 10.1,
                "change": 0.4,
                "pct_chg": 3.96,
                "vol": 1000,
                "amount": 10500,
            }
        ]

    def daily_basic(self, *, trade_date):
        self.daily_basic_calls.append({"trade_date": trade_date})
        return [
            {
                "ts_code": "000001.SZ",
                "trade_date": trade_date,
                "close": 10.5,
                "turnover_rate": 1.2,
                "pe": 12.3,
                "pe_ttm": 11.8,
                "pb": 1.1,
                "total_mv": 1000000,
                "circ_mv": 800000,
                "limit_status": 0,
            },
            {
                "ts_code": "600000.SH",
                "trade_date": trade_date,
                "close": 8.2,
                "turnover_rate": 0.9,
                "pe": 9.7,
                "pe_ttm": 9.4,
                "pb": 0.8,
                "total_mv": 900000,
                "circ_mv": 700000,
                "limit_status": 0,
            },
        ]


class FakeUpdater:
    def __init__(self) -> None:
        self.end_dates = []

    def update_all(self, end_date=None):
        self.end_dates.append(end_date)


class FailingStockBasicClient(FakeMarketClient):
    def stock_basic(self, statuses):
        raise RuntimeError("token不对，您传过来的是abcdefghijklmnopqrstuvwxyz123456")


class DataFetchTests(unittest.TestCase):
    def test_repository_upserts_stock_and_qfq_daily_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = MarketDataRepository(Path(tmp) / "market.sqlite3")
            repo.initialize()

            repo.upsert_stock_basic(
                [
                    {
                        "ts_code": "000001.SZ",
                        "symbol": "000001",
                        "name": "平安银行",
                        "list_status": "L",
                    }
                ]
            )
            repo.upsert_daily_qfq(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20260102",
                        "open": 10,
                        "close": 11,
                    }
                ]
            )
            repo.upsert_daily_basic(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20260102",
                        "close": 11,
                        "pe": 10,
                        "pe_ttm": 9,
                        "pb": 1,
                        "total_mv": 1000,
                    }
                ]
            )

            self.assertEqual(repo.get_stock_codes(), ["000001.SZ"])
            self.assertEqual(repo.latest_trade_date("000001.SZ"), "20260102")
            self.assertEqual(repo.count_daily_rows(), 1)
            self.assertEqual(repo.daily_qfq_trade_dates(), ["20260102"])
            self.assertEqual(repo.daily_basic_trade_dates(), ["20260102"])
            self.assertEqual(repo.count_daily_basic_rows(), 1)

    def test_updater_fetches_all_stocks_incrementally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeMarketClient()
            config = DataFetchConfig(
                database_path=Path(tmp) / "market.sqlite3",
                start_date="20260101",
                request_sleep_seconds=0,
            )
            repo = MarketDataRepository(config.database_path)
            updater = DailyMarketUpdater(config=config, repository=repo, client=client)

            summary = updater.update_all(end_date="20260105")

            self.assertEqual(summary.status, "succeeded")
            self.assertEqual(summary.stock_count, 2)
            self.assertEqual(summary.bar_count, 2)
            self.assertEqual(summary.daily_basic_count, 2)
            self.assertEqual(repo.count_daily_rows(), 2)
            self.assertEqual(repo.count_daily_basic_rows(), 2)
            self.assertEqual(
                [call["start_date"] for call in client.daily_calls],
                ["20260101", "20260101"],
            )
            self.assertEqual(
                [call["trade_date"] for call in client.daily_basic_calls],
                ["20260101"],
            )

            updater.update_all(end_date="20260106")
            self.assertEqual(
                [call["start_date"] for call in client.daily_calls[-2:]],
                ["20260102", "20260102"],
            )
            self.assertEqual(
                [call["trade_date"] for call in client.daily_basic_calls],
                ["20260101", "20260102"],
            )

    def test_updater_masks_stock_basic_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = DataFetchConfig(
                database_path=Path(tmp) / "market.sqlite3",
                request_sleep_seconds=0,
            )
            repo = MarketDataRepository(config.database_path)
            updater = DailyMarketUpdater(
                config=config,
                repository=repo,
                client=FailingStockBasicClient(),
            )

            summary = updater.update_all(end_date="20260914")

            self.assertEqual(summary.status, "failed")
            self.assertIn("token不对", summary.message)
            self.assertNotIn("mnopqrstuvwxyz", summary.message)

    def test_next_daily_run_uses_next_20_clock(self) -> None:
        self.assertEqual(
            next_daily_run(datetime(2026, 9, 14, 19, 0), "20:00"),
            datetime(2026, 9, 14, 20, 0),
        )
        self.assertEqual(
            next_daily_run(datetime(2026, 9, 14, 20, 1), "20:00"),
            datetime(2026, 9, 15, 20, 0),
        )

    def test_scheduler_run_once_passes_end_date(self) -> None:
        from stock_common.data_fetch.scheduler import DailyUpdateScheduler

        updater = FakeUpdater()
        scheduler = DailyUpdateScheduler(
            config=DataFetchConfig(tushare_token="token"),
            updater=updater,
        )

        scheduler.run_once(end_date="20260914")

        self.assertEqual(updater.end_dates, ["20260914"])

    def test_factor_client_requires_symbol_or_trade_date(self) -> None:
        client = object.__new__(FactorFetchClient)

        with self.assertRaises(ValueError):
            FactorFetchClient.factor_value(client, factor_name="roe_ttm")

    def test_factor_client_caches_factor_value_rows(self) -> None:
        class FakePro:
            def __init__(self) -> None:
                self.calls = []

            def factor_value(self, *, factor_name, ts_code=None, trade_date=None):
                self.calls.append(
                    {
                        "factor_name": factor_name,
                        "ts_code": ts_code,
                        "trade_date": trade_date,
                    }
                )
                return [
                    {
                        "factor_name": factor_name,
                        "ts_code": "000001.SZ",
                        "trade_date": trade_date,
                        "factor_value": 0.15,
                    }
                ]

        class FakeTushare:
            def __init__(self) -> None:
                self.pro = FakePro()

            def pro_api(self, token, timeout=30):
                return self.pro

        with tempfile.TemporaryDirectory() as tmp:
            fake_ts = FakeTushare()
            config = DataFetchConfig(
                tushare_token="token",
                database_path=Path(tmp) / "market.sqlite3",
                retry_count=0,
            )
            with patch("stock_common.data_fetch.factor_fetch._import_tushare", return_value=fake_ts):
                client = FactorFetchClient(config)
                first = client.factor_value(factor_name="roe_ttm", trade_date="20240101")
                second = client.factor_value(factor_name="roe_ttm", trade_date="20240101")

        self.assertEqual(len(fake_ts.pro.calls), 1)
        self.assertEqual(first[0]["factor_value"], 0.15)
        self.assertEqual(second[0]["factor_value"], 0.15)

    def test_config_from_env_loads_dotenv_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            db_path = Path(tmp) / "local.sqlite3"
            env_path.write_text(
                "\n".join(
                    [
                        "TUSHARE_TOKEN=dotenv-token",
                        "TUSHARE_HTTP_URL=https://example.test/",
                        f"DATA_FETCH_DB_PATH={db_path}",
                        "DATA_FETCH_START_DATE=20200101",
                        "DATA_FETCH_SCHEDULER_TIME=20:00",
                        "TUSHARE_TIMEOUT_SECONDS=77",
                    ]
                ),
                encoding="utf-8",
            )

            def load_test_dotenv():
                return load_dotenv((env_path,))

            with patch("stock_common.data_fetch.config.load_dotenv", side_effect=load_test_dotenv):
                with patch.dict("os.environ", {}, clear=True):
                    config = DataFetchConfig.from_env()

            self.assertEqual(config.tushare_token, "dotenv-token")
            self.assertEqual(config.tushare_http_url, "https://example.test/")
            self.assertEqual(config.database_path, db_path)
            self.assertEqual(config.start_date, "20200101")
            self.assertEqual(config.tushare_timeout_seconds, 77)

    def test_factor_client_uses_configured_timeout_and_proxy_url(self) -> None:
        class FakePro:
            pass

        class FakeTushare:
            def __init__(self) -> None:
                self.calls = []

            def pro_api(self, token, timeout=30):
                self.calls.append({"token": token, "timeout": timeout})
                return FakePro()

        fake_ts = FakeTushare()
        config = DataFetchConfig(
            tushare_token="token",
            tushare_http_url="https://proxy.example/",
            tushare_timeout_seconds=88,
        )

        with patch("stock_common.data_fetch.factor_fetch._import_tushare", return_value=fake_ts):
            client = FactorFetchClient(config)

        self.assertEqual(fake_ts.calls, [{"token": "token", "timeout": 88}])
        self.assertEqual(
            getattr(client._pro, "_DataApi__http_url"),
            "https://proxy.example/",
        )

    def test_data_fetch_env_file_overrides_process_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("TUSHARE_TOKEN=token-from-file\n", encoding="utf-8")

            def load_test_dotenv():
                return load_dotenv((env_path,))

            with patch("stock_common.data_fetch.config.load_dotenv", side_effect=load_test_dotenv):
                with patch.dict("os.environ", {"TUSHARE_TOKEN": "stale-token"}, clear=True):
                    config = DataFetchConfig.from_env()

            self.assertEqual(config.tushare_token, "token-from-file")


if __name__ == "__main__":
    unittest.main()
