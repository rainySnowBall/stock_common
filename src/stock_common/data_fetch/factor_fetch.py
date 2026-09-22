"""Tushare factor fetching with a local SQLite cache."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from stock_common.data_fetch.config import DataFetchConfig
from stock_common.data_fetch.tushare_client import (
    TushareConfigError,
    _import_tushare,
    _records_from_frame,
)


@dataclass
class FactorFetchClient:
    """Fetch factor metadata and values from Tushare and cache factor_value rows."""

    config: DataFetchConfig

    def __post_init__(self) -> None:
        if not self.config.tushare_token:
            raise TushareConfigError("TUSHARE_TOKEN is required for factor fetch.")
        self._cache_initialized = False
        self._ts = _import_tushare()
        self._pro = self._ts.pro_api(
            self.config.tushare_token,
            timeout=self.config.tushare_timeout_seconds,
        )
        if self.config.tushare_http_url:
            setattr(self._pro, "_DataApi__http_url", self.config.tushare_http_url)

    def factor_list(self) -> list[dict[str, Any]]:
        return _records_from_frame(self._call_with_retries(self._pro.factor_list))

    def factor_value(
        self,
        *,
        factor_name: str,
        ts_code: str | None = None,
        trade_date: str | None = None,
    ) -> list[dict[str, Any]]:
        if not ts_code and not trade_date:
            raise ValueError("Either ts_code or trade_date is required.")
        factor_name = factor_name.strip()
        ts_code = _optional_text(ts_code)
        trade_date = _optional_text(trade_date)

        cached_records = self._cached_factor_value(
            factor_name=factor_name,
            ts_code=ts_code,
            trade_date=trade_date,
        )
        if cached_records is not None:
            return cached_records

        frame = self._call_with_retries(
            self._pro.factor_value,
            factor_name=factor_name,
            ts_code=ts_code,
            trade_date=trade_date,
        )
        records = _records_from_frame(frame)
        self._write_factor_value_cache(
            records,
            factor_name=factor_name,
            ts_code=ts_code,
            trade_date=trade_date,
        )
        return records

    def _call_with_retries(self, func: Any, **kwargs: Any) -> Any:
        attempts = max(self.config.retry_count, 0) + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return func(**kwargs)
            except Exception as exc:
                last_error = exc
                if _is_auth_error(exc) or attempt >= attempts - 1:
                    raise
                time.sleep(min(0.8 * (attempt + 1), 3.0))
        assert last_error is not None
        raise last_error

    def _cached_factor_value(
        self,
        *,
        factor_name: str,
        ts_code: str | None,
        trade_date: str | None,
    ) -> list[dict[str, Any]] | None:
        self._ensure_factor_cache()
        with _connect(self.config.database_path) as conn:
            fetch = conn.execute(
                """
                SELECT row_count
                FROM factor_value_fetches
                WHERE factor_name = ? AND ts_code = ? AND trade_date = ?
                """,
                (factor_name, ts_code or "", trade_date or ""),
            ).fetchone()
            if fetch is None:
                if ts_code and trade_date:
                    row = conn.execute(
                        """
                        SELECT factor_name, ts_code, trade_date, factor_value
                        FROM factor_values
                        WHERE factor_name = ? AND ts_code = ? AND trade_date = ?
                        """,
                        (factor_name, ts_code, trade_date),
                    ).fetchone()
                    if row is not None:
                        return [
                            {
                                "factor_name": row["factor_name"],
                                "ts_code": row["ts_code"],
                                "trade_date": row["trade_date"],
                                "factor_value": row["factor_value"],
                            }
                        ]
                return None

            clauses = ["factor_name = ?"]
            params: list[Any] = [factor_name]
            if ts_code:
                clauses.append("ts_code = ?")
                params.append(ts_code)
            if trade_date:
                clauses.append("trade_date = ?")
                params.append(trade_date)
            rows = conn.execute(
                f"""
                SELECT factor_name, ts_code, trade_date, factor_value
                FROM factor_values
                WHERE {' AND '.join(clauses)}
                ORDER BY trade_date, ts_code
                """,
                params,
            ).fetchall()
        return [
            {
                "factor_name": row["factor_name"],
                "ts_code": row["ts_code"],
                "trade_date": row["trade_date"],
                "factor_value": row["factor_value"],
            }
            for row in rows
        ]

    def _write_factor_value_cache(
        self,
        records: list[dict[str, Any]],
        *,
        factor_name: str,
        ts_code: str | None,
        trade_date: str | None,
    ) -> None:
        self._ensure_factor_cache()
        fetched_at = _utc_now()
        rows = []
        for record in records:
            row_ts_code = _optional_text(record.get("ts_code")) or ts_code
            row_trade_date = _optional_text(record.get("trade_date")) or trade_date
            value = _optional_float(record.get("factor_value"))
            if not row_ts_code or not row_trade_date or value is None:
                continue
            rows.append((factor_name, row_ts_code, row_trade_date, value, fetched_at))

        with _connect(self.config.database_path) as conn:
            if rows:
                conn.executemany(
                    """
                    INSERT INTO factor_values (
                        factor_name, ts_code, trade_date, factor_value, fetched_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(factor_name, ts_code, trade_date) DO UPDATE SET
                        factor_value = excluded.factor_value,
                        fetched_at = excluded.fetched_at
                    """,
                    rows,
                )
            conn.execute(
                """
                INSERT INTO factor_value_fetches (
                    factor_name, ts_code, trade_date, fetched_at, row_count
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(factor_name, ts_code, trade_date) DO UPDATE SET
                    fetched_at = excluded.fetched_at,
                    row_count = excluded.row_count
                """,
                (factor_name, ts_code or "", trade_date or "", fetched_at, len(rows)),
            )

    def _ensure_factor_cache(self) -> None:
        if self._cache_initialized:
            return
        self.config.database_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self.config.database_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS factor_values (
                    factor_name TEXT NOT NULL,
                    ts_code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    factor_value REAL NOT NULL,
                    source TEXT NOT NULL DEFAULT 'tushare.factor_value',
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (factor_name, ts_code, trade_date)
                );

                CREATE INDEX IF NOT EXISTS idx_factor_values_trade_date
                    ON factor_values(factor_name, trade_date);

                CREATE INDEX IF NOT EXISTS idx_factor_values_symbol
                    ON factor_values(factor_name, ts_code);

                CREATE TABLE IF NOT EXISTS factor_value_fetches (
                    factor_name TEXT NOT NULL,
                    ts_code TEXT NOT NULL DEFAULT '',
                    trade_date TEXT NOT NULL DEFAULT '',
                    fetched_at TEXT NOT NULL,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (factor_name, ts_code, trade_date)
                );
                """
            )
        self._cache_initialized = True


def _is_auth_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "token" in message or "权限" in message or "认证" in message


@contextmanager
def _connect(database_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Tushare factor data with local caching.")
    parser.add_argument("--factor-name")
    parser.add_argument("--ts-code")
    parser.add_argument("--trade-date")
    parser.add_argument("--list", action="store_true", help="List available factors.")
    args = parser.parse_args()

    client = FactorFetchClient(DataFetchConfig.from_env())
    if args.list:
        records = client.factor_list()
    else:
        if not args.factor_name:
            parser.error("--factor-name is required unless --list is used")
        records = client.factor_value(
            factor_name=args.factor_name,
            ts_code=args.ts_code,
            trade_date=args.trade_date,
        )
    print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
