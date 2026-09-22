"""Backfill Tushare daily_basic rows for trade dates present in daily_qfq."""

from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_common.data_fetch import DataFetchConfig, MarketDataRepository
from stock_common.data_fetch.tushare_client import TushareMarketDataClient
from stock_common.data_fetch.updater import _sanitize_error


_THREAD_STATE = threading.local()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill daily_basic for dates already present in daily_qfq."
    )
    parser.add_argument("--end-date", default="20260914", help="YYYYMMDD inclusive.")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0, help="Maximum missing dates to fetch.")
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    config = DataFetchConfig.from_env()
    repo = MarketDataRepository(config.database_path)
    repo.initialize()

    qfq_dates = repo.daily_qfq_trade_dates(end_date=args.end_date)
    existing_dates = set(repo.daily_basic_trade_dates(end_date=args.end_date))
    missing_dates = [trade_date for trade_date in qfq_dates if trade_date not in existing_dates]
    if args.limit > 0:
        missing_dates = missing_dates[: args.limit]

    print(
        {
            "database": str(config.database_path),
            "target_trade_dates": len(qfq_dates),
            "existing_daily_basic_dates": len(existing_dates),
            "missing_to_fetch": len(missing_dates),
            "end_date": args.end_date,
            "workers": args.workers,
        },
        flush=True,
    )

    if not missing_dates:
        print({"status": "nothing_to_do", "daily_basic_rows": repo.count_daily_basic_rows()}, flush=True)
        return

    started = time.perf_counter()
    completed = 0
    inserted_rows = 0
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=max(args.workers, 1)) as executor:
        futures = {
            executor.submit(_fetch_daily_basic, config, trade_date): trade_date
            for trade_date in missing_dates
        }
        for future in as_completed(futures):
            trade_date = futures[future]
            try:
                rows = future.result()
                inserted_rows += repo.upsert_daily_basic(rows)
            except Exception as exc:
                errors.append(
                    f"{trade_date}: {type(exc).__name__}: {_sanitize_error(str(exc))}"
                )
            completed += 1
            if completed % args.progress_every == 0 or completed == len(missing_dates):
                elapsed = max(time.perf_counter() - started, 0.001)
                print(
                    {
                        "completed_dates": completed,
                        "total_dates": len(missing_dates),
                        "inserted_or_updated_rows": inserted_rows,
                        "errors": len(errors),
                        "dates_per_minute": round(completed / elapsed * 60, 2),
                    },
                    flush=True,
                )

    print(
        {
            "status": "succeeded" if not errors else "partial_failed",
            "completed_dates": completed,
            "inserted_or_updated_rows": inserted_rows,
            "errors": errors[:10],
            "daily_basic_rows": repo.count_daily_basic_rows(),
            "covered_daily_basic_dates": len(repo.daily_basic_trade_dates(end_date=args.end_date)),
        },
        flush=True,
    )


def _fetch_daily_basic(
    config: DataFetchConfig,
    trade_date: str,
    *,
    retries: int = 2,
) -> list[dict[str, Any]]:
    client = _thread_client(config)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return client.daily_basic(trade_date=trade_date)
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _thread_client(config: DataFetchConfig) -> TushareMarketDataClient:
    client = getattr(_THREAD_STATE, "client", None)
    if client is None:
        client = TushareMarketDataClient(config)
        _THREAD_STATE.client = client
    return client


if __name__ == "__main__":
    main()
