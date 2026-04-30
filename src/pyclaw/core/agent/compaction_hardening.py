from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

IDENTIFIER_PRESERVATION_INSTRUCTIONS = (
    "Preserve identifiers verbatim in the summary: UUIDs, hashes, IDs, hostnames, "
    "IP addresses, ports, URLs, filenames, file paths, model names, session IDs, "
    "commit SHAs, error codes. Never rewrite, shorten, or paraphrase these tokens."
)

HARDENED_SUMMARIZER_SYSTEM_PROMPT = (
    "You are a compaction summarizer. Given a conversation excerpt, produce a concise summary "
    "that preserves key decisions, facts the user revealed, files touched, tool results, and "
    "outstanding tasks. Omit small talk.\n\n"
    f"{IDENTIFIER_PRESERVATION_INSTRUCTIONS}\n\n"
    "Return plain text, no headings, no bullet points unless they improve clarity."
)

_NON_CONVERSATIONAL_ROLES = {"system"}
_HEARTBEAT_MARKERS = ("heartbeat", "ping", "pong", "[heartbeat]", "[system]")


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            else:
                text_attr = getattr(block, "text", None)
                if isinstance(text_attr, str):
                    parts.append(text_attr)
        return "\n".join(parts)
    return ""


def has_real_conversation(messages: list[dict[str, Any]]) -> bool:
    for msg in messages:
        role = msg.get("role")
        if role in _NON_CONVERSATIONAL_ROLES:
            continue
        text = _extract_text(msg.get("content"))
        lowered = text.strip().lower()
        if not lowered:
            if msg.get("tool_calls"):
                return True
            continue
        if any(marker in lowered for marker in _HEARTBEAT_MARKERS):
            continue
        if role in ("user", "assistant", "tool"):
            return True
    return False


def strip_tool_result_details(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "tool" and isinstance(msg.get("content"), list):
            new_content = []
            for block in msg["content"]:
                if isinstance(block, dict):
                    clean_block = {k: v for k, v in block.items() if k != "details"}
                    new_content.append(clean_block)
                else:
                    new_content.append(block)
            new_msg = dict(msg)
            new_msg["content"] = new_content
            cleaned.append(new_msg)
        elif "details" in msg:
            new_msg = {k: v for k, v in msg.items() if k != "details"}
            cleaned.append(new_msg)
        else:
            cleaned.append(msg)
    return cleaned


def filter_oversized_messages(
    messages: list[dict[str, Any]],
    *,
    context_window: int,
    oversized_fraction: float = 0.5,
) -> list[dict[str, Any]]:
    if context_window <= 0:
        return messages
    threshold_chars = int(context_window * oversized_fraction * 4)
    result: list[dict[str, Any]] = []
    for msg in messages:
        text = _extract_text(msg.get("content"))
        if len(text) > threshold_chars:
            role = msg.get("role", "unknown")
            result.append(
                {
                    "role": role,
                    "content": f"[omitted oversized message from {role}]",
                }
            )
        else:
            result.append(msg)
    return result


def _estimate_tokens(msg: dict[str, Any]) -> int:
    from pyclaw.core.agent.compaction import estimate_message_tokens

    return estimate_message_tokens(msg)


def split_into_chunks(
    messages: list[dict[str, Any]],
    *,
    chunk_token_budget: int,
) -> list[list[dict[str, Any]]]:
    if chunk_token_budget <= 0:
        return [list(messages)]
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0
    for msg in messages:
        msg_tokens = _estimate_tokens(msg)
        if current and (current_tokens + msg_tokens) > chunk_token_budget:
            chunks.append(current)
            current = [msg]
            current_tokens = msg_tokens
        else:
            current.append(msg)
            current_tokens += msg_tokens
    if current:
        chunks.append(current)
    return chunks or [[]]


async def summarize_in_stages(
    messages: list[dict[str, Any]],
    *,
    summarizer: Callable[[list[dict[str, Any]]], Awaitable[str]],
    chunk_token_budget: int = 8_000,
) -> str:
    chunks = split_into_chunks(messages, chunk_token_budget=chunk_token_budget)

    if len(chunks) <= 1:
        return await summarizer(messages)

    stage_summaries: list[str] = []
    for chunk in chunks:
        summary = await summarizer(chunk)
        stage_summaries.append(summary)

    merge_payload: list[dict[str, Any]] = [
        {"role": "user", "content": f"[summary of chunk {i + 1}]\n{s}"}
        for i, s in enumerate(stage_summaries)
    ]
    return await summarizer(merge_payload)


def sanity_check_token_estimate(
    tokens_before: int, tokens_after: int | None
) -> int | None:
    if tokens_after is None:
        return None
    if tokens_after > tokens_before:
        return None
    return tokens_after
