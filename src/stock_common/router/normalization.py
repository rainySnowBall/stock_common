"""Text normalization helpers for routing."""

from __future__ import annotations

import re


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Normalize user text while preserving Chinese semantic cues."""

    return _WHITESPACE_RE.sub(" ", text.strip().lower())
