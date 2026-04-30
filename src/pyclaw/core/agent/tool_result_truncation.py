from __future__ import annotations

from pyclaw.models import ContentBlock, ImageBlock, TextBlock, ToolResult

DEFAULT_MAX_OUTPUT_CHARS = 25_000


def _marker(dropped: int) -> str:
    return f"[... {dropped} more characters truncated]"


def _text_length(blocks: list[ContentBlock]) -> int:
    total = 0
    for block in blocks:
        if isinstance(block, TextBlock):
            total += len(block.text)
    return total


def truncate_tool_result(result: ToolResult, max_chars: int) -> ToolResult:
    if max_chars <= 0:
        return result

    total_text_len = _text_length(result.content)
    if total_text_len <= max_chars:
        return result

    new_blocks: list[ContentBlock] = []
    remaining = max_chars

    for block in result.content:
        if isinstance(block, TextBlock):
            if remaining <= 0:
                continue
            if len(block.text) <= remaining:
                new_blocks.append(block)
                remaining -= len(block.text)
            else:
                new_blocks.append(TextBlock(text=block.text[:remaining]))
                remaining = 0
        elif isinstance(block, ImageBlock):
            new_blocks.append(block)

    dropped = total_text_len - max_chars
    if dropped > 0:
        new_blocks.append(TextBlock(text="\n" + _marker(dropped)))

    return ToolResult(
        tool_call_id=result.tool_call_id,
        content=new_blocks,
        is_error=result.is_error,
    )


def resolve_max_output_chars(tool, default_cap: int) -> int:
    override = getattr(tool, "max_output_chars", None)
    if override is None:
        return default_cap
    try:
        value = int(override)
    except (TypeError, ValueError):
        return default_cap
    return value if value >= 0 else default_cap
