"""Shared utilities for content block handling.

This module consolidates the previously-duplicated "extract text from
possibly-list content" logic scattered across compaction/dedup.py,
compaction/hardening.py, sop_extraction.py, session_export.py, and
memory_archive.py.
"""

from __future__ import annotations

from typing import Any

IMAGE_PLACEHOLDER = "[图片]"


def extract_text_from_content(content: Any) -> str:
    """Extract text from content that may be str, list[ContentBlock], or list[dict].

    Handles three shapes:
    - str: returned as-is
    - list[ContentBlock]: Pydantic TextBlock/ImageBlock objects
    - list[dict]: LLM wire format (OpenAI `image_url`, Anthropic `image`, `text`)

    ImageBlock / image_url / image blocks are replaced with a placeholder
    so downstream consumers (L4 archive summaries, SOP extraction) know
    the message had images without carrying base64 data.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            btype = block.get("type")
            if btype == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif btype in ("image", "image_url"):
                parts.append(IMAGE_PLACEHOLDER)
            continue

        text_attr = getattr(block, "text", None)
        if isinstance(text_attr, str):
            parts.append(text_attr)
            continue

        btype_attr = getattr(block, "type", None)
        if btype_attr == "image":
            parts.append(IMAGE_PLACEHOLDER)

    return "\n".join(parts)
