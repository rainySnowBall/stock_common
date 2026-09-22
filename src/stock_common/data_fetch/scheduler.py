"""Daily 20:00 scheduler for market data updates."""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from stock_common.data_fetch.config import DataFetchConfig
from stock_common.data_fetch.updater import DailyMarketUpdater


def next_daily_run(now: datetime, hhmm: str) -> datetime:
    hour_text, minute_text = hhmm.split(":", maxsplit=1)
    target = now.replace(
        hour=int(hour_text),
        minute=int(minute_text),
        second=0,
        microsecond=0,
    )
    if target <= now:
        target += timedelta(days=1)
    return target


@dataclass
class DailyUpdateScheduler:
    """Long-running local scheduler that updates every day at config time."""

    config: DataFetchConfig
    updater: DailyMarketUpdater | None = None

    def __post_init__(self) -> None:
        if self.updater is None:
            self.updater = DailyMarketUpdater(self.config)

    def run_forever(self) -> None:
        assert self.updater is not None
        while True:
            next_run = next_daily_run(datetime.now(), self.config.scheduler_time)
            sleep_seconds = max((next_run - datetime.now()).total_seconds(), 1.0)
            time.sleep(sleep_seconds)
            self.updater.update_all()

    def run_once(self, end_date: str | None = None) -> None:
        assert self.updater is not None
        self.updater.update_all(end_date=end_date)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run daily Tushare qfq market data updates.")
    parser.add_argument("--once", action="store_true", help="Run one update immediately and exit.")
    parser.add_argument("--end-date", help="Override update end date, format YYYYMMDD.")
    args = parser.parse_args()

    scheduler = DailyUpdateScheduler(DataFetchConfig.from_env())
    if args.once:
        scheduler.run_once(end_date=args.end_date)
    else:
        scheduler.run_forever()


if __name__ == "__main__":
    main()
