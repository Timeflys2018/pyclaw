from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any

DEFAULT_WINDOW_SECONDS = 60.0
DEFAULT_MIN_CHARS = 24

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_dedup(text: str) -> str:
    if not text:
        return ""
    nfc = unicodedata.normalize("NFC", text)
    collapsed = _WHITESPACE_RE.sub(" ", nfc).strip()
    return collapsed.lower()


def _extract_text(content: Any) -> str:
    from pyclaw.models.utils import extract_text_from_content

    return extract_text_from_content(content)


def _parse_timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.timestamp()
        except ValueError:
            return None
    return None


def dedupe_duplicate_user_messages(
    messages: list[dict[str, Any]],
    *,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> list[dict[str, Any]]:
    if not messages:
        return []

    kept: list[dict[str, Any]] = []
    last_seen: dict[str, float] = {}

    for msg in messages:
        role = msg.get("role")
        if role != "user":
            kept.append(msg)
            continue

        text = _extract_text(msg.get("content"))
        if len(text) < min_chars:
            kept.append(msg)
            continue

        normalized = normalize_for_dedup(text)
        if not normalized:
            kept.append(msg)
            continue

        ts = _parse_timestamp(msg.get("timestamp"))

        previous_ts = last_seen.get(normalized)
        is_duplicate = False
        if previous_ts is not None:
            if ts is None or window_seconds <= 0 or (ts - previous_ts) <= window_seconds:
                is_duplicate = True

        if is_duplicate:
            continue

        if ts is not None:
            last_seen[normalized] = ts
        else:
            last_seen[normalized] = float("-inf")
        kept.append(msg)

    return kept
