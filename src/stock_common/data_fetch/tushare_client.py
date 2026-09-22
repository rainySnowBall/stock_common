"""Thin Tushare client wrappers used by data_fetch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stock_common.data_fetch.config import DataFetchConfig


class TushareConfigError(RuntimeError):
    """Raised when Tushare runtime settings are incomplete."""


DAILY_BASIC_FIELDS = (
    "ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,"
    "pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,"
    "free_share,total_mv,circ_mv,limit_status"
)


@dataclass
class TushareMarketDataClient:
    """Fetch A-share stock metadata and qfq daily bars from Tushare."""

    config: DataFetchConfig

    def __post_init__(self) -> None:
        if not self.config.tushare_token:
            raise TushareConfigError("TUSHARE_TOKEN is required for Tushare data fetch.")
        self._ts = _import_tushare()
        self._pro = self._ts.pro_api(
            self.config.tushare_token,
            timeout=self.config.tushare_timeout_seconds,
        )
        if self.config.tushare_http_url:
            setattr(self._pro, "_DataApi__http_url", self.config.tushare_http_url)

    def stock_basic(self, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
        records = []
        fields = (
            "ts_code,symbol,name,area,industry,market,exchange,"
            "list_status,list_date,delist_date,is_hs"
        )
        for status in statuses:
            frame = self._pro.stock_basic(
                exchange="",
                list_status=status,
                fields=fields,
            )
            for record in _records_from_frame(frame):
                record["list_status"] = record.get("list_status") or status
                records.append(record)
        return records

    def qfq_daily(
        self,
        *,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        kwargs = {
            "ts_code": ts_code,
            "adj": "qfq",
            "freq": "D",
            "start_date": start_date,
            "end_date": end_date,
            "retry_count": self.config.retry_count,
        }
        try:
            frame = self._ts.pro_bar(api=self._pro, **kwargs)
        except TypeError:
            frame = self._ts.pro_bar(**kwargs)
        records = _records_from_frame(frame)
        for record in records:
            record["ts_code"] = record.get("ts_code") or ts_code
        return records

    def daily_basic(self, *, trade_date: str) -> list[dict[str, Any]]:
        frame = self._pro.daily_basic(
            ts_code="",
            trade_date=trade_date,
            fields=DAILY_BASIC_FIELDS,
        )
        return _records_from_frame(frame)

    def trade_cal(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        frame = self._pro.trade_cal(
            exchange="SSE",
            start_date=start_date,
            end_date=end_date,
            fields="exchange,cal_date,is_open,pretrade_date",
        )
        return _records_from_frame(frame)


def _import_tushare() -> Any:
    try:
        import tushare as ts
    except ImportError as exc:
        raise TushareConfigError(
            "The 'tushare' package is required. Install it before running data_fetch."
        ) from exc
    return ts


def _records_from_frame(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if isinstance(frame, list):
        return [dict(item) for item in frame]
    if isinstance(frame, tuple):
        return [dict(item) for item in frame]
    if hasattr(frame, "empty") and frame.empty:
        return []
    if hasattr(frame, "to_dict"):
        return [dict(item) for item in frame.to_dict("records")]
    raise TypeError(f"Unsupported Tushare response type: {type(frame).__name__}")
