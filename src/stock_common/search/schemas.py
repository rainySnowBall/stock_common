"""Data contracts for web search and search-grounded answers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlparse, urlunparse

from stock_common.config import WEB_SEARCH_CONFIG
from stock_common.env import load_dotenv


_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "api_url_env": ("ONLINE_SEARCH_BASE_URL",),
    "api_key_env": ("ONLINE_SEARCH_API_KEY",),
}


class SearchContentType(str, Enum):
    """Content type supported by the configured web-search API."""

    SNIPPET = "snippet"
    SUMMARY = "summary"
    MAIN_TEXT = "mainText"


class SearchWay(str, Enum):
    """Search mode supported by the configured web-search API."""

    LITE = "lite"
    PRO = "pro"
    PRO_FETCH = "pro-fetch"


class WebSearchError(RuntimeError):
    """Raised when the web-search API call fails."""


class WebSearchConfigError(WebSearchError):
    """Raised when required web-search runtime configuration is missing."""


@dataclass(frozen=True)
class SearchHistoryMessage:
    """One message passed as search API conversation history."""

    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class WebSearchSettings:
    """Runtime settings for the web-search API."""

    api_url: str
    api_key: str
    query_rewrite: bool = True
    top_k: int = 5
    content_type: SearchContentType = SearchContentType.SNIPPET
    way: SearchWay = SearchWay.PRO
    timeout_seconds: float = 30.0
    max_retries: int = 1

    def __post_init__(self) -> None:
        if not self.api_url:
            raise WebSearchConfigError("FINANCE_WEB_SEARCH_API_URL is required")
        if not self.api_key:
            raise WebSearchConfigError("FINANCE_WEB_SEARCH_API_KEY is required")
        if self.top_k <= 0:
            raise ValueError("top_k must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if not isinstance(self.content_type, SearchContentType):
            object.__setattr__(self, "content_type", SearchContentType(self.content_type))
        if not isinstance(self.way, SearchWay):
            object.__setattr__(self, "way", SearchWay(self.way))

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        load_env_files: bool = True,
    ) -> "WebSearchSettings":
        """Build settings from process env and local .env files."""

        if load_env_files:
            load_dotenv()

        env = env or os.environ
        api_url = build_web_search_api_url(env)
        api_key = _env_value(env, "api_key_env")
        return cls(
            api_url=api_url,
            api_key=api_key,
            query_rewrite=_env_bool(
                env,
                "query_rewrite_env",
                bool(WEB_SEARCH_CONFIG["default_query_rewrite"]),
            ),
            top_k=_env_int(env, "top_k_env", int(WEB_SEARCH_CONFIG["default_top_k"])),
            content_type=SearchContentType(
                _env_str(
                    env,
                    "content_type_env",
                    str(WEB_SEARCH_CONFIG["default_content_type"]),
                )
            ),
            way=SearchWay(_env_str(env, "way_env", str(WEB_SEARCH_CONFIG["default_way"]))),
            timeout_seconds=_env_float(
                env,
                "timeout_seconds_env",
                float(WEB_SEARCH_CONFIG["default_timeout_seconds"]),
            ),
            max_retries=_env_int(
                env,
                "max_retries_env",
                int(WEB_SEARCH_CONFIG["default_max_retries"]),
            ),
        )


@dataclass(frozen=True)
class SearchResult:
    """One normalized web-search result."""

    title: str
    url: str | None = None
    snippet: str | None = None
    summary: str | None = None
    main_text: str | None = None
    source: str | None = None
    published_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def best_text(self) -> str:
        return self.summary or self.snippet or self.main_text or ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "summary": self.summary,
            "main_text": self.main_text,
            "source": self.source,
            "published_at": self.published_at,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class SearchAnswer:
    """Answer synthesized from multiple search results."""

    query: str
    content: str
    results: tuple[SearchResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "content": self.content,
            "results": [result.to_dict() for result in self.results],
        }


def _env_value(env: Mapping[str, str], config_key: str) -> str:
    env_name = str(WEB_SEARCH_CONFIG[config_key])
    value = _unquote(env.get(env_name, "").strip())
    if value:
        return value
    for alias in _ENV_ALIASES.get(config_key, ()):
        value = _unquote(env.get(alias, "").strip())
        if value:
            return value
    return ""


def build_web_search_api_url(env: Mapping[str, str]) -> str:
    """Build the complete web-search endpoint from environment settings."""

    raw_url = _env_value(env, "api_url_env")
    if not raw_url:
        return ""

    parsed = urlparse(raw_url)
    if not parsed.scheme or not parsed.netloc:
        return raw_url

    normalized_path = parsed.path.rstrip("/")
    if "/v3/openapi/" in normalized_path and "/web-search/" in normalized_path:
        return raw_url.rstrip("/")

    workspace = _env_str(env, "workspace_env", str(WEB_SEARCH_CONFIG["default_workspace"]))
    service_id = _env_str(env, "service_id_env", str(WEB_SEARCH_CONFIG["default_service_id"]))
    endpoint_path = (
        f"{normalized_path}/v3/openapi/workspaces/{workspace}/web-search/{service_id}"
        if normalized_path
        else f"/v3/openapi/workspaces/{workspace}/web-search/{service_id}"
    )

    return urlunparse((parsed.scheme, parsed.netloc, endpoint_path, "", "", ""))


def _env_str(env: Mapping[str, str], config_key: str, default: str) -> str:
    value = _env_value(env, config_key)
    return value or default


def _env_bool(env: Mapping[str, str], config_key: str, default: bool) -> bool:
    value = _env_value(env, config_key)
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(env: Mapping[str, str], config_key: str, default: int) -> int:
    value = _env_value(env, config_key)
    return int(value) if value else default


def _env_float(env: Mapping[str, str], config_key: str, default: float) -> float:
    value = _env_value(env, config_key)
    return float(value) if value else default


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
