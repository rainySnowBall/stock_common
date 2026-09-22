"""Configuration for Tushare market data fetchers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from stock_common.env import load_dotenv


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class DataFetchConfig:
    """Runtime settings for local market data storage."""

    tushare_token: str | None = None
    tushare_http_url: str | None = None
    database_path: Path = _workspace_root() / "data" / "market_data.sqlite3"
    start_date: str = "20100101"
    end_date: str | None = None
    stock_statuses: tuple[str, ...] = ("L", "D", "P")
    scheduler_time: str = "20:00"
    request_sleep_seconds: float = 0.12
    retry_count: int = 3
    tushare_timeout_seconds: int = 120

    @classmethod
    def from_env(cls) -> "DataFetchConfig":
        loaded_env = load_dotenv()
        values = {**os.environ, **loaded_env}
        statuses = values.get("DATA_FETCH_STOCK_STATUSES", "L,D,P")
        return cls(
            tushare_token=_optional_value(values.get("TUSHARE_TOKEN")),
            tushare_http_url=_optional_value(values.get("TUSHARE_HTTP_URL")),
            database_path=Path(
                values.get(
                    "DATA_FETCH_DB_PATH",
                    str(_workspace_root() / "data" / "market_data.sqlite3"),
                )
            ),
            start_date=values.get("DATA_FETCH_START_DATE", "20100101"),
            end_date=_optional_value(values.get("DATA_FETCH_END_DATE")),
            stock_statuses=tuple(
                item.strip().upper() for item in statuses.split(",") if item.strip()
            ),
            scheduler_time=values.get("DATA_FETCH_SCHEDULER_TIME", "20:00"),
            request_sleep_seconds=float(values.get("DATA_FETCH_REQUEST_SLEEP_SECONDS", "0.12")),
            retry_count=int(values.get("DATA_FETCH_RETRY_COUNT", "3")),
            tushare_timeout_seconds=int(
                values.get(
                    "TUSHARE_TIMEOUT_SECONDS",
                    values.get("DATA_FETCH_TIMEOUT_SECONDS", "120"),
                )
            ),
        )


def _optional_env(name: str) -> str | None:
    return _optional_value(os.getenv(name))


def _optional_value(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
