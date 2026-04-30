from __future__ import annotations

import re
from typing import Literal

TurnClassification = Literal["ok", "planning", "reasoning", "empty"]

_PLANNING_PHRASES = re.compile(
    r"^\s*(?:i'll|i will|i'm going to|let me|here'?s (?:my|the) plan|my plan|first,? i|step\s*\d)",
    re.IGNORECASE | re.MULTILINE,
)

_NUMBERED_LIST = re.compile(r"^\s*(?:\d+\.|[-*])\s+", re.MULTILINE)

_THINKING_BLOCK = re.compile(r"<thinking>.*?</thinking>", re.IGNORECASE | re.DOTALL)


def strip_thinking(text: str) -> str:
    return _THINKING_BLOCK.sub("", text or "").strip()


def extract_thinking(text: str) -> str:
    matches = _THINKING_BLOCK.findall(text or "")
    return "\n".join(matches)


def classify_turn(
    *,
    text: str,
    tool_calls: list[dict] | None,
    reasoning: str | None = None,
) -> TurnClassification:
    if tool_calls:
        return "ok"

    raw_text = text or ""
    has_reasoning = bool(reasoning) or bool(_THINKING_BLOCK.search(raw_text))
    visible = strip_thinking(raw_text)

    if not visible.strip() and not has_reasoning:
        return "empty"

    if has_reasoning and not visible.strip():
        return "reasoning"

    if _looks_like_planning(visible):
        return "planning"

    return "ok"


def _looks_like_planning(visible: str) -> bool:
    text = visible.strip()
    if not text:
        return False

    if not _PLANNING_PHRASES.search(text):
        return False

    list_matches = len(_NUMBERED_LIST.findall(text))
    word_count = len(text.split())
    short_enough = word_count < 200

    return list_matches >= 2 or (short_enough and _PLANNING_PHRASES.search(text) is not None)


_RETRY_MESSAGES = {
    "planning": "Do not restate the plan. Act now by calling the appropriate tools.",
    "reasoning": "Continue and produce the visible answer now (outside of <thinking> tags).",
    "empty": "Produce the answer now.",
}


def retry_message_for(classification: TurnClassification) -> str | None:
    return _RETRY_MESSAGES.get(classification)
