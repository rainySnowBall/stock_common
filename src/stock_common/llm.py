"""OpenAI-compatible chat-completions client for configured runtime models."""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse, urlunparse

from stock_common.env import load_dotenv
from stock_common.model_selection.schemas import ModelProfile


class ChatLLMError(RuntimeError):
    """Raised when a chat-completions request fails."""


class ChatLLMConfigError(ChatLLMError):
    """Raised when runtime model configuration is missing."""


@dataclass(frozen=True)
class ChatMessage:
    """One message passed to an OpenAI-compatible chat-completions API."""

    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ChatLLMSettings:
    """Runtime settings for one selected chat model."""

    api_url: str
    api_key: str
    model_name: str
    timeout_seconds: float = 30.0
    max_retries: int = 1

    def __post_init__(self) -> None:
        if not self.api_url:
            raise ChatLLMConfigError("chat LLM base URL is required")
        if not self.api_key:
            raise ChatLLMConfigError("chat LLM API key is required")
        if not self.model_name:
            raise ChatLLMConfigError("chat LLM model name is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")

    @classmethod
    def from_model_profile(
        cls,
        model: ModelProfile,
        env: Mapping[str, str] | None = None,
        load_env_files: bool = True,
    ) -> "ChatLLMSettings":
        """Build settings from the selected model profile and fallback env names."""

        if load_env_files:
            load_dotenv()

        env = env or os.environ
        model_name_names = _env_name_candidates(
            model.model_name_env,
            "FINANCE_CHAT_MODEL_NAME",
            "DEEPSEEK_MODEL_NAME",
            "DEEPSEEK_MODEL",
            "OPENAI_MODEL",
        )
        base_url_names = _env_name_candidates(
            model.base_url_env,
            "FINANCE_CHAT_BASE_URL",
            "DEEPSEEK_BASE_URL",
            "OPENAI_BASE_URL",
        )
        api_key_names = _env_name_candidates(
            model.api_key_env,
            "FINANCE_CHAT_API_KEY",
            "DEEPSEEK_API_KEY",
            "OPENAI_API_KEY",
        )
        timeout_names = _env_name_candidates(
            model.timeout_seconds_env,
            "FINANCE_CHAT_TIMEOUT_SECONDS",
            "LLM_TIMEOUT_SECONDS",
        )
        retry_names = _env_name_candidates(
            model.max_retries_env,
            "FINANCE_CHAT_MAX_RETRIES",
            "LLM_MAX_RETRIES",
        )

        model_name = _first_env_value(env, model_name_names)
        base_url = _first_env_value(env, base_url_names)
        api_key = _first_env_value(env, api_key_names)

        missing = []
        if not model_name:
            missing.append(f"model name ({', '.join(model_name_names)})")
        if not base_url:
            missing.append(f"base URL ({', '.join(base_url_names)})")
        if not api_key:
            missing.append(f"API key ({', '.join(api_key_names)})")
        if missing:
            raise ChatLLMConfigError("missing chat LLM env: " + "; ".join(missing))

        return cls(
            api_url=build_chat_completions_url(base_url),
            api_key=api_key,
            model_name=model_name,
            timeout_seconds=_env_float(env, timeout_names, 30.0),
            max_retries=_env_int(env, retry_names, 1),
        )


@dataclass(frozen=True)
class OpenAICompatibleChatClient:
    """Call an OpenAI-compatible chat-completions endpoint."""

    settings: ChatLLMSettings

    async def complete(self, messages: Iterable[ChatMessage]) -> str:
        return await asyncio.to_thread(self.complete_sync, tuple(messages))

    def complete_sync(self, messages: Iterable[ChatMessage]) -> str:
        payload = {
            "model": self.settings.model_name,
            "messages": [message.to_dict() for message in messages],
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.settings.api_key}",
        }

        last_error: Exception | str | None = None
        for attempt in range(self.settings.max_retries + 1):
            request = urllib.request.Request(
                self.settings.api_url,
                data=data,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.settings.timeout_seconds,
                ) as response:
                    raw_body = response.read().decode("utf-8")
                    response_payload = json.loads(raw_body) if raw_body else {}
                    return extract_chat_completion_content(response_payload)
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

        raise ChatLLMError(f"chat LLM request failed: {last_error}")


def build_chat_completions_url(base_url: str) -> str:
    """Build a chat-completions endpoint from a base URL or full endpoint."""

    raw_url = _unquote(base_url.strip()).rstrip("/")
    if not raw_url:
        return ""
    if raw_url.endswith("/chat/completions"):
        return raw_url

    parsed = urlparse(raw_url)
    if not parsed.scheme or not parsed.netloc:
        return f"{raw_url}/chat/completions"

    normalized_path = parsed.path.rstrip("/")
    endpoint_path = (
        f"{normalized_path}/chat/completions"
        if normalized_path
        else "/chat/completions"
    )
    return urlunparse((parsed.scheme, parsed.netloc, endpoint_path, "", "", ""))


def extract_chat_completion_content(payload: Any) -> str:
    """Extract assistant text from common chat-completions response shapes."""

    if not isinstance(payload, Mapping):
        raise ChatLLMError("chat LLM response was not a JSON object")

    output_text = _text_from_content(payload.get("output_text"))
    if output_text:
        return output_text

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, Mapping):
            message = first_choice.get("message")
            if isinstance(message, Mapping):
                content = _text_from_content(message.get("content"))
                if content:
                    return content
            text = _text_from_content(first_choice.get("text"))
            if text:
                return text

    raise ChatLLMError("chat LLM response did not include assistant content")


def _text_from_content(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        text = "".join(parts).strip()
        return text or None
    return str(value).strip() or None


def _env_name_candidates(*names: str | None) -> tuple[str, ...]:
    seen: set[str] = set()
    candidates = []
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        candidates.append(name)
    return tuple(candidates)


def _first_env_value(env: Mapping[str, str], names: Iterable[str]) -> str:
    for name in names:
        value = _unquote(env.get(name, "").strip())
        if value:
            return value
    return ""


def _env_int(env: Mapping[str, str], names: Iterable[str], default: int) -> int:
    value = _first_env_value(env, names)
    return int(value) if value else default


def _env_float(env: Mapping[str, str], names: Iterable[str], default: float) -> float:
    value = _first_env_value(env, names)
    return float(value) if value else default


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


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
