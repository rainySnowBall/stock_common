"""Incremental updater for local qfq daily A-share bars."""

from __future__ import annotations

import time
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

from stock_common.data_fetch.config import DataFetchConfig
from stock_common.data_fetch.repository import MarketDataRepository
from stock_common.data_fetch.tushare_client import TushareMarketDataClient


class MarketDataClient(Protocol):
    def stock_basic(self, statuses: tuple[str, ...]) -> list[dict[str, object]]:
        ...

    def qfq_daily(
        self,
        *,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, object]]:
        ...

    def daily_basic(self, *, trade_date: str) -> list[dict[str, object]]:
        ...


@dataclass(frozen=True)
class FetchSummary:
    status: str
    started_at: str
    finished_at: str
    stock_count: int
    bar_count: int
    error_count: int
    message: str = ""
    daily_basic_count: int = 0


@dataclass
class DailyMarketUpdater:
    """Fetch all configured A-share qfq daily bars into SQLite."""

    config: DataFetchConfig
    repository: MarketDataRepository | None = None
    client: MarketDataClient | None = None

    def __post_init__(self) -> None:
        if self.repository is None:
            self.repository = MarketDataRepository(self.config.database_path)
        if self.client is None:
            self.client = TushareMarketDataClient(self.config)

    def update_all(self, end_date: str | None = None) -> FetchSummary:
        assert self.repository is not None
        assert self.client is not None

        self.repository.initialize()
        started_at = _utc_now()
        end = end_date or self.config.end_date or _today_yyyymmdd()
        try:
            stock_records = self.client.stock_basic(self.config.stock_statuses)
        except Exception as exc:
            finished_at = _utc_now()
            message = f"stock_basic: {type(exc).__name__}: {_sanitize_error(str(exc))}"
            self.repository.insert_run(
                status="failed",
                started_at=started_at,
                finished_at=finished_at,
                message=message,
            )
            return FetchSummary(
                status="failed",
                started_at=started_at,
                finished_at=finished_at,
                stock_count=0,
                bar_count=0,
                error_count=1,
                message=message,
                daily_basic_count=0,
            )
        self.repository.upsert_stock_basic(stock_records)

        bar_count = 0
        daily_basic_count = 0
        errors: list[str] = []
        for stock in stock_records:
            ts_code = str(stock.get("ts_code") or "").strip()
            if not ts_code:
                continue
            start = self._start_date_for_stock(stock, ts_code)
            if start > end:
                continue
            try:
                rows = self.client.qfq_daily(
                    ts_code=ts_code,
                    start_date=start,
                    end_date=end,
                )
                bar_count += self.repository.upsert_daily_qfq(rows)
            except Exception as exc:
                errors.append(f"{ts_code}: {type(exc).__name__}: {_sanitize_error(str(exc))}")
            if self.config.request_sleep_seconds > 0:
                time.sleep(self.config.request_sleep_seconds)

        target_dates = self.repository.daily_qfq_trade_dates(end_date=end)
        existing_dates = set(self.repository.daily_basic_trade_dates(end_date=end))
        for trade_date in target_dates:
            if trade_date in existing_dates:
                continue
            try:
                rows = self.client.daily_basic(trade_date=trade_date)
                daily_basic_count += self.repository.upsert_daily_basic(rows)
            except Exception as exc:
                errors.append(
                    f"daily_basic {trade_date}: {type(exc).__name__}: "
                    f"{_sanitize_error(str(exc))}"
                )
            if self.config.request_sleep_seconds > 0:
                time.sleep(self.config.request_sleep_seconds)

        status = "succeeded" if not errors else "partial_failed"
        finished_at = _utc_now()
        message = "; ".join(errors[:5])
        if len(errors) > 5:
            message += f"; ... {len(errors) - 5} more"
        self.repository.insert_run(
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            message=message,
            stock_count=len(stock_records),
            bar_count=bar_count,
            daily_basic_count=daily_basic_count,
        )
        self.repository.set_state("last_daily_qfq_update", finished_at)
        self.repository.set_state("last_daily_qfq_end_date", end)
        self.repository.set_state("last_daily_basic_update", finished_at)
        self.repository.set_state("last_daily_basic_end_date", end)
        return FetchSummary(
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            stock_count=len(stock_records),
            bar_count=bar_count,
            error_count=len(errors),
            message=message,
            daily_basic_count=daily_basic_count,
        )

    def _start_date_for_stock(self, stock: dict[str, object], ts_code: str) -> str:
        assert self.repository is not None
        latest = self.repository.latest_trade_date(ts_code)
        if latest:
            return _next_date(latest)
        list_date = str(stock.get("list_date") or "").strip()
        if list_date and list_date > self.config.start_date:
            return list_date
        return self.config.start_date


def _next_date(yyyymmdd: str) -> str:
    parsed = datetime.strptime(yyyymmdd, "%Y%m%d").date()
    return (parsed + timedelta(days=1)).strftime("%Y%m%d")


def _today_yyyymmdd() -> str:
    return date.today().strftime("%Y%m%d")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sanitize_error(message: str) -> str:
    return re.sub(r"([A-Za-z0-9]{12})[A-Za-z0-9]{8,}", r"\1...", message)
