"""Resolve A-share names and user-entered codes to Tushare ts_code values."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from stock_common.data_fetch.config import DataFetchConfig
from stock_common.llm import (
    ChatLLMConfigError,
    ChatLLMError,
    ChatLLMSettings,
    ChatMessage,
    OpenAICompatibleChatClient,
)
from stock_common.model_selection.defaults import DEFAULT_MODEL_PROFILES
from stock_common.model_selection.schemas import ModelCapability, ModelFunction, ModelProfile
from stock_common.search.schemas import SearchResult, WebSearchConfigError, WebSearchSettings
from stock_common.search.web_search import WebSearchClient


class StockSearchClient(Protocol):
    def search_sync(self, query: str, history: Iterable[object] | None = None) -> tuple[SearchResult, ...]:
        ...


class StockIdentityExtractionClient(Protocol):
    def complete_sync(self, messages: Iterable[ChatMessage]) -> str:
        ...


@dataclass(frozen=True)
class StockIdentity:
    stock_name: str = ""
    stock_code: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.stock_name and not self.stock_code


@dataclass(frozen=True)
class ResolvedStock:
    ts_code: str
    symbol: str
    name: str | None = None
    source: str = "input"


@dataclass(frozen=True)
class StockIdentityExtractor:
    """Extract a single stock name/code JSON object from user text with an LLM."""

    client: StockIdentityExtractionClient | None = None
    model_profile: ModelProfile | None = None
    enabled: bool | None = None
    mode_env_name: str = "STOCK_RESOLVER_LLM_MODE"

    def extract(self, text: str) -> StockIdentity | None:
        if not self._is_enabled():
            return None
        try:
            content = self._client().complete_sync(_stock_identity_messages(text))
            payload = _extract_json_object(content)
        except (ChatLLMConfigError, ChatLLMError, ValueError, TypeError, KeyError):
            return None
        return StockIdentity(
            stock_name=str(payload.get("stock_name") or "").strip(),
            stock_code=str(payload.get("stock_code") or "").strip(),
        )

    def _is_enabled(self) -> bool:
        if self.client is not None:
            return True
        if self.enabled is not None:
            return self.enabled
        mode = os.environ.get(self.mode_env_name, "").strip().lower()
        return mode in {"1", "true", "yes", "y", "on", "llm", "extract", "json"}

    def _client(self) -> StockIdentityExtractionClient:
        if self.client is not None:
            return self.client
        return OpenAICompatibleChatClient(
            ChatLLMSettings.from_model_profile(
                self.model_profile or _default_stock_identity_model()
            )
        )


@dataclass(frozen=True)
class StockSymbolResolver:
    """Resolve stock names/codes with optional LLM extraction and web search."""

    database_path: Path | None = None
    search_client: StockSearchClient | None = None
    enable_web_search: bool = False
    identity_extractor: StockIdentityExtractor | None = None
    enable_llm_extract: bool = False

    def resolve(self, text: str) -> ResolvedStock | None:
        identity = self._extract_identity(text)
        if identity is not None:
            return self._resolve_identity(identity)

        code = extract_ts_code(text)
        if code is not None:
            return self._with_local_metadata(code) or _resolved_from_code(code, "input")

        local = self._resolve_from_local_name(text)
        if local is not None:
            return local

        if self.search_client is None and not self.enable_web_search:
            return None
        return self._resolve_from_search(text)

    def _extract_identity(self, text: str) -> StockIdentity | None:
        extractor = self.identity_extractor
        if extractor is None and self.enable_llm_extract:
            extractor = StockIdentityExtractor()
        if extractor is None:
            return None
        return extractor.extract(text)

    def _resolve_identity(self, identity: StockIdentity) -> ResolvedStock | None:
        if identity.is_empty:
            return None

        code = normalize_ts_code(identity.stock_code) if identity.stock_code else None
        if code is not None:
            return self._with_local_metadata(code) or _resolved_from_code(code, "llm_extract")

        if not identity.stock_name:
            return None

        searched = self._resolve_from_search(identity.stock_name)
        if searched is not None:
            return searched
        return self._resolve_from_local_name(identity.stock_name)

    def _database_path(self) -> Path:
        if self.database_path is not None:
            return self.database_path
        return DataFetchConfig.from_env().database_path

    def _with_local_metadata(self, ts_code: str) -> ResolvedStock | None:
        path = self._database_path()
        if not path.exists():
            return None
        try:
            with closing(sqlite3.connect(path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT ts_code, symbol, name
                    FROM stock_basic
                    WHERE upper(ts_code) = ?
                    LIMIT 1
                    """,
                    (ts_code,),
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        return ResolvedStock(
            ts_code=str(row["ts_code"]).upper(),
            symbol=str(row["symbol"] or ts_code[:6]),
            name=str(row["name"]) if row["name"] else None,
            source="local_stock_basic",
        )

    def _resolve_from_local_name(self, text: str) -> ResolvedStock | None:
        normalized = text.strip()
        if not normalized:
            return None
        path = self._database_path()
        if not path.exists():
            return None
        try:
            with closing(sqlite3.connect(path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT ts_code, symbol, name
                    FROM stock_basic
                    WHERE name IS NOT NULL
                      AND name <> ''
                      AND (list_status IS NULL OR list_status = '' OR list_status = 'L')
                    ORDER BY length(name) DESC, ts_code
                    """
                ).fetchall()
        except sqlite3.Error:
            return None
        row = next(
            (
                candidate
                for candidate in rows
                if str(candidate["name"]).strip() and str(candidate["name"]).strip() in normalized
            ),
            None,
        )
        if row is None:
            return None
        return ResolvedStock(
            ts_code=str(row["ts_code"]).upper(),
            symbol=str(row["symbol"] or str(row["ts_code"])[:6]),
            name=str(row["name"]) if row["name"] else None,
            source="local_stock_basic",
        )

    def _resolve_from_search(self, text: str) -> ResolvedStock | None:
        client = self.search_client
        if client is None:
            try:
                client = WebSearchClient(WebSearchSettings.from_env())
            except WebSearchConfigError:
                return None

        try:
            results = client.search_sync(_stock_code_search_query(text))
        except Exception:
            return None

        for result in results:
            code = extract_ts_code(
                " ".join(
                    part
                    for part in (
                        result.title,
                        result.snippet,
                        result.summary,
                        result.main_text,
                    )
                    if part
                )
            )
            if code is not None:
                return self._with_local_metadata(code) or _resolved_from_code(code, "web_search")
        return None


def _stock_code_search_query(text: str) -> str:
    return f"{text.strip()} A股 股票代码"


def _stock_identity_messages(text: str) -> tuple[ChatMessage, ...]:
    return (
        ChatMessage(
            role="system",
            content=(
                "你是A股用户输入解析器。只提取用户明确提到的单只股票名称或股票代码，"
                "输出一个JSON对象，格式必须是 {\"stock_name\":\"\", \"stock_code\":\"\"}。"
                "如果用户没有提到单只股票，两个字段都返回空字符串。不要输出Markdown。"
            ),
        ),
        ChatMessage(role="user", content=text),
    )


def _extract_json_object(content: str) -> dict[str, object]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("stock identity response did not contain JSON")
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("stock identity response was not a JSON object")
    return payload


def _default_stock_identity_model() -> ModelProfile:
    for profile in DEFAULT_MODEL_PROFILES:
        if (
            ModelFunction.TASK_PARSER in profile.supported_functions
            and ModelCapability.JSON_OUTPUT in profile.capabilities
        ):
            return profile
    for profile in DEFAULT_MODEL_PROFILES:
        if ModelCapability.JSON_OUTPUT in profile.capabilities:
            return profile
    raise ChatLLMConfigError("no json-output model profile configured for stock extraction")


def extract_ts_code(text: str) -> str | None:
    """Extract and normalize a Tushare-style A-share code from free text."""

    normalized = text.strip().upper()
    for pattern in (
        r"\b([0-9]{6})\.(SH|SZ|BJ)\b",
        r"\b(SH|SZ|BJ)([0-9]{6})\b",
        r"(?:股票代码|证券代码|代码)[:：\s]*([0-9]{6})\b",
        r"\b([0-9]{6})\b",
    ):
        match = re.search(pattern, normalized)
        if not match:
            continue
        if len(match.groups()) == 2 and match.group(1) in {"SH", "SZ", "BJ"}:
            return f"{match.group(2)}.{match.group(1)}"
        if len(match.groups()) == 2:
            return f"{match.group(1)}.{match.group(2)}"
        return normalize_ts_code(match.group(1))
    return None


def normalize_ts_code(value: str) -> str | None:
    raw = value.strip().upper()
    dotted = re.fullmatch(r"([0-9]{6})\.(SH|SZ|BJ)", raw)
    if dotted:
        return raw
    prefixed = re.fullmatch(r"(SH|SZ|BJ)([0-9]{6})", raw)
    if prefixed:
        return f"{prefixed.group(2)}.{prefixed.group(1)}"
    if not re.fullmatch(r"[0-9]{6}", raw):
        return None
    return f"{raw}.{_exchange_suffix(raw)}"


def _exchange_suffix(symbol: str) -> str:
    if symbol.startswith(("600", "601", "603", "605", "688", "689", "900")):
        return "SH"
    if symbol.startswith(("000", "001", "002", "003", "200", "300", "301")):
        return "SZ"
    if symbol.startswith(("4", "8", "9")):
        return "BJ"
    return "SH"


def _resolved_from_code(ts_code: str, source: str) -> ResolvedStock:
    return ResolvedStock(ts_code=ts_code, symbol=ts_code[:6], source=source)
