"""Python client for the configured web-search API."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from stock_common.search.schemas import (
    SearchHistoryMessage,
    SearchResult,
    WebSearchError,
    WebSearchSettings,
)


@dataclass(frozen=True)
class WebSearchClient:
    """Call the Aliyun OpenSearch-style web-search endpoint."""

    settings: WebSearchSettings

    async def search(
        self,
        query: str,
        history: Iterable[SearchHistoryMessage] | None = None,
    ) -> tuple[SearchResult, ...]:
        return await asyncio.to_thread(self.search_sync, query, history)

    def search_sync(
        self,
        query: str,
        history: Iterable[SearchHistoryMessage] | None = None,
    ) -> tuple[SearchResult, ...]:
        payload = {
            "query": query,
            "query_rewrite": self.settings.query_rewrite,
            "top_k": self.settings.top_k,
            "history": [message.to_dict() for message in history or ()],
            "content_type": self.settings.content_type.value,
            "way": self.settings.way.value,
        }

        request = urllib.request.Request(
            self.settings.api_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.api_key}",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.settings.timeout_seconds,
                ) as response:
                    raw_body = response.read().decode("utf-8")
                    response_payload = json.loads(raw_body) if raw_body else {}
                    return extract_search_results(response_payload, self.settings.top_k)
            except urllib.error.HTTPError as exc:
                last_error = _format_http_error(exc, self.settings.api_url)
                if attempt < self.settings.max_retries:
                    time.sleep(min(1.5, 0.2 * (attempt + 1)))
                    continue
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.settings.max_retries:
                    time.sleep(min(1.5, 0.2 * (attempt + 1)))
                    continue
                break

        raise WebSearchError(f"web search failed: {last_error}")


def extract_search_results(payload: Any, limit: int) -> tuple[SearchResult, ...]:
    """Extract normalized results from several common API response shapes."""

    results: list[SearchResult] = []
    seen: set[str] = set()

    for item in _iter_result_dicts(payload):
        result = _to_search_result(item)
        if result is None:
            continue

        fingerprint = (result.url or result.title or result.best_text).strip()
        if not fingerprint or fingerprint in seen:
            continue

        seen.add(fingerprint)
        results.append(result)
        if len(results) >= limit:
            break

    return tuple(results)


def _iter_result_dicts(payload: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_result_dicts(item)
        return

    if not isinstance(payload, dict):
        return

    if _looks_like_result(payload):
        yield payload

    preferred_keys = (
        "results",
        "result",
        "items",
        "documents",
        "data",
        "records",
        "webpages",
        "search_results",
        "searchResults",
    )
    for key in preferred_keys:
        if key in payload:
            yield from _iter_result_dicts(payload[key])

    for key, value in payload.items():
        if key in preferred_keys:
            continue
        if isinstance(value, (dict, list)):
            yield from _iter_result_dicts(value)


def _looks_like_result(value: Mapping[str, Any]) -> bool:
    keys = {str(key) for key in value.keys()}
    content_keys = {
        "snippet",
        "summary",
        "mainText",
        "main_text",
        "content",
        "description",
        "body",
    }
    identity_keys = {"title", "name", "url", "link"}
    return bool(keys & content_keys) and bool(keys & identity_keys)


def _to_search_result(value: Mapping[str, Any]) -> SearchResult | None:
    title = _first_text(value, ("title", "name")) or "未命名来源"
    url = _first_text(value, ("url", "link", "href"))
    snippet = _first_text(value, ("snippet", "description", "content"))
    summary = _first_text(value, ("summary", "abstract"))
    main_text = _first_text(value, ("mainText", "main_text", "body", "text"))
    source = _first_text(value, ("source", "site", "siteName", "hostname"))
    published_at = _first_text(value, ("publishedAt", "publish_time", "date", "time"))

    if not any((snippet, summary, main_text)):
        return None

    return SearchResult(
        title=title,
        url=url,
        snippet=snippet,
        summary=summary,
        main_text=main_text,
        source=source,
        published_at=published_at,
        metadata={
            "raw_keys": sorted(str(key) for key in value.keys()),
        },
    )


def _first_text(value: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if candidate is None:
            continue
        text = str(candidate).strip()
        if text:
            return text
    return None


def _format_http_error(exc: urllib.error.HTTPError, api_url: str) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""

    parsed = urlparse(api_url)
    location = f"{parsed.netloc}{parsed.path}" if parsed.netloc else parsed.path
    message = f"HTTP {exc.code} {exc.reason} for {location}"
    if body:
        compact_body = " ".join(body.split())
        if len(compact_body) > 300:
            compact_body = compact_body[:299] + "..."
        message = f"{message}; response={compact_body}"
    return message
