"""Small .env loader used by local runtime clients."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


def load_dotenv(paths: tuple[Path, ...] | None = None) -> dict[str, str]:
    """Load key/value pairs from .env files without overriding process env."""

    loaded: dict[str, str] = {}
    for path in paths or default_dotenv_paths():
        if not path.exists() or not path.is_file():
            continue
        for key, value in parse_dotenv(path.read_text(encoding="utf-8")).items():
            if key not in loaded:
                loaded[key] = value
            if os.environ.get(key, "") == "":
                os.environ[key] = value
    return loaded


def default_dotenv_paths() -> tuple[Path, ...]:
    package_dir = Path(__file__).resolve().parent
    project_root = package_dir.parents[1]
    explicit_paths = tuple(
        Path(value).expanduser()
        for value in (
            os.getenv("STOCK_COMMON_ENV_FILE"),
            os.getenv("FINANCE_ENV_FILE"),
            os.getenv("ENV_FILE"),
        )
        if value
    )
    home = _home_dir()
    candidates = explicit_paths + (
        package_dir / ".env",
        Path.cwd() / ".env",
        project_root / ".env",
        project_root.parent / ".env",
        *( ((home / ".env"),) if home else () ),
    )
    return _dedupe_paths(candidates)


def _home_dir() -> Path | None:
    try:
        return Path.home()
    except RuntimeError:
        return None


def _dedupe_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    seen: set[str] = set()
    unique = []
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path.absolute())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return tuple(unique)


def parse_dotenv(content: str) -> Mapping[str, str]:
    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue

        values[key] = _unquote(value.strip())
    return values


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
