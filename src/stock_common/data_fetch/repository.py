"""SQLite storage for local A-share market data."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


@dataclass(frozen=True)
class MarketDataRepository:
    """Persist stock metadata and qfq daily bars in SQLite."""

    database_path: Path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS stock_basic (
                    ts_code TEXT PRIMARY KEY,
                    symbol TEXT,
                    name TEXT,
                    area TEXT,
                    industry TEXT,
                    market TEXT,
                    exchange TEXT,
                    list_status TEXT,
                    list_date TEXT,
                    delist_date TEXT,
                    is_hs TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daily_qfq (
                    ts_code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    pre_close REAL,
                    change REAL,
                    pct_chg REAL,
                    vol REAL,
                    amount REAL,
                    source TEXT NOT NULL DEFAULT 'tushare.pro_bar.qfq',
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (ts_code, trade_date)
                );

                CREATE INDEX IF NOT EXISTS idx_daily_qfq_trade_date
                    ON daily_qfq(trade_date);

                CREATE TABLE IF NOT EXISTS daily_basic (
                    ts_code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    close REAL,
                    turnover_rate REAL,
                    turnover_rate_f REAL,
                    volume_ratio REAL,
                    pe REAL,
                    pe_ttm REAL,
                    pb REAL,
                    ps REAL,
                    ps_ttm REAL,
                    dv_ratio REAL,
                    dv_ttm REAL,
                    total_share REAL,
                    float_share REAL,
                    free_share REAL,
                    total_mv REAL,
                    circ_mv REAL,
                    limit_status INTEGER,
                    source TEXT NOT NULL DEFAULT 'tushare.daily_basic',
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (ts_code, trade_date)
                );

                CREATE INDEX IF NOT EXISTS idx_daily_basic_trade_date
                    ON daily_basic(trade_date);

                CREATE TABLE IF NOT EXISTS fetch_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    message TEXT,
                    stock_count INTEGER NOT NULL DEFAULT 0,
                    bar_count INTEGER NOT NULL DEFAULT 0,
                    daily_basic_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS fetch_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            _ensure_column(
                conn,
                "fetch_runs",
                "daily_basic_count",
                "INTEGER NOT NULL DEFAULT 0",
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_stock_basic(self, records: Iterable[dict[str, Any]]) -> int:
        rows = []
        updated_at = _utc_now()
        for record in records:
            rows.append(
                (
                    _text(record.get("ts_code")),
                    _text(record.get("symbol")),
                    _text(record.get("name")),
                    _text(record.get("area")),
                    _text(record.get("industry")),
                    _text(record.get("market")),
                    _text(record.get("exchange")),
                    _text(record.get("list_status")),
                    _text(record.get("list_date")),
                    _text(record.get("delist_date")),
                    _text(record.get("is_hs")),
                    updated_at,
                )
            )

        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO stock_basic (
                    ts_code, symbol, name, area, industry, market, exchange,
                    list_status, list_date, delist_date, is_hs, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ts_code) DO UPDATE SET
                    symbol = excluded.symbol,
                    name = excluded.name,
                    area = excluded.area,
                    industry = excluded.industry,
                    market = excluded.market,
                    exchange = excluded.exchange,
                    list_status = excluded.list_status,
                    list_date = excluded.list_date,
                    delist_date = excluded.delist_date,
                    is_hs = excluded.is_hs,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
        return len(rows)

    def upsert_daily_qfq(self, records: Iterable[dict[str, Any]]) -> int:
        rows = []
        fetched_at = _utc_now()
        for record in records:
            ts_code = _text(record.get("ts_code"))
            trade_date = _text(record.get("trade_date"))
            if not ts_code or not trade_date:
                continue
            rows.append(
                (
                    ts_code,
                    trade_date,
                    _float(record.get("open")),
                    _float(record.get("high")),
                    _float(record.get("low")),
                    _float(record.get("close")),
                    _float(record.get("pre_close")),
                    _float(record.get("change")),
                    _float(record.get("pct_chg")),
                    _float(record.get("vol")),
                    _float(record.get("amount")),
                    "tushare.pro_bar.qfq",
                    fetched_at,
                )
            )

        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO daily_qfq (
                    ts_code, trade_date, open, high, low, close, pre_close,
                    change, pct_chg, vol, amount, source, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ts_code, trade_date) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    pre_close = excluded.pre_close,
                    change = excluded.change,
                    pct_chg = excluded.pct_chg,
                    vol = excluded.vol,
                    amount = excluded.amount,
                    source = excluded.source,
                    fetched_at = excluded.fetched_at
                """,
                rows,
            )
        return len(rows)

    def upsert_daily_basic(self, records: Iterable[dict[str, Any]]) -> int:
        rows = []
        fetched_at = _utc_now()
        for record in records:
            ts_code = _text(record.get("ts_code"))
            trade_date = _text(record.get("trade_date"))
            if not ts_code or not trade_date:
                continue
            rows.append(
                (
                    ts_code,
                    trade_date,
                    _float(record.get("close")),
                    _float(record.get("turnover_rate")),
                    _float(record.get("turnover_rate_f")),
                    _float(record.get("volume_ratio")),
                    _float(record.get("pe")),
                    _float(record.get("pe_ttm")),
                    _float(record.get("pb")),
                    _float(record.get("ps")),
                    _float(record.get("ps_ttm")),
                    _float(record.get("dv_ratio")),
                    _float(record.get("dv_ttm")),
                    _float(record.get("total_share")),
                    _float(record.get("float_share")),
                    _float(record.get("free_share")),
                    _float(record.get("total_mv")),
                    _float(record.get("circ_mv")),
                    _int(record.get("limit_status")),
                    "tushare.daily_basic",
                    fetched_at,
                )
            )

        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO daily_basic (
                    ts_code, trade_date, close, turnover_rate, turnover_rate_f,
                    volume_ratio, pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm,
                    total_share, float_share, free_share, total_mv, circ_mv,
                    limit_status, source, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ts_code, trade_date) DO UPDATE SET
                    close = excluded.close,
                    turnover_rate = excluded.turnover_rate,
                    turnover_rate_f = excluded.turnover_rate_f,
                    volume_ratio = excluded.volume_ratio,
                    pe = excluded.pe,
                    pe_ttm = excluded.pe_ttm,
                    pb = excluded.pb,
                    ps = excluded.ps,
                    ps_ttm = excluded.ps_ttm,
                    dv_ratio = excluded.dv_ratio,
                    dv_ttm = excluded.dv_ttm,
                    total_share = excluded.total_share,
                    float_share = excluded.float_share,
                    free_share = excluded.free_share,
                    total_mv = excluded.total_mv,
                    circ_mv = excluded.circ_mv,
                    limit_status = excluded.limit_status,
                    source = excluded.source,
                    fetched_at = excluded.fetched_at
                """,
                rows,
            )
        return len(rows)

    def get_stock_rows(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM stock_basic ORDER BY ts_code"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_stock_codes(self) -> list[str]:
        return [row["ts_code"] for row in self.get_stock_rows()]

    def latest_trade_date(self, ts_code: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT MAX(trade_date) AS latest FROM daily_qfq WHERE ts_code = ?",
                (ts_code,),
            ).fetchone()
        return str(row["latest"]) if row and row["latest"] else None

    def daily_qfq_trade_dates(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[str]:
        clauses = []
        params: list[str] = []
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(end_date)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT trade_date FROM daily_qfq {where} ORDER BY trade_date",
                params,
            ).fetchall()
        return [str(row["trade_date"]) for row in rows]

    def daily_basic_trade_dates(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[str]:
        clauses = []
        params: list[str] = []
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(end_date)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT trade_date FROM daily_basic {where} ORDER BY trade_date",
                params,
            ).fetchall()
        return [str(row["trade_date"]) for row in rows]

    def insert_run(
        self,
        *,
        status: str,
        started_at: str,
        finished_at: str | None = None,
        message: str | None = None,
        stock_count: int = 0,
        bar_count: int = 0,
        daily_basic_count: int = 0,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO fetch_runs (
                    started_at, finished_at, status, message, stock_count, bar_count,
                    daily_basic_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    started_at,
                    finished_at,
                    status,
                    message,
                    stock_count,
                    bar_count,
                    daily_basic_count,
                ),
            )
            return int(cursor.lastrowid)

    def set_state(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO fetch_state(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, _utc_now()),
            )

    def count_daily_rows(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM daily_qfq").fetchone()
        return int(row["count"])

    def count_daily_basic_rows(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM daily_basic").fetchone()
        return int(row["count"])


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ensure_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    columns = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
