from __future__ import annotations

from typing import Any, Literal, TypedDict, Union

from pydantic import BaseModel, Field


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    data: str
    mime_type: str


ContentBlock = Union[TextBlock, ImageBlock]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    tool_call_id: str
    content: list[ContentBlock]
    is_error: bool = False


class AgentMessageDict(TypedDict, total=False):
    role: str
    content: Any
    tool_calls: list[dict[str, Any]]
    tool_call_id: str
    name: str


class TextChunk(BaseModel):
    type: Literal["text_chunk"] = "text_chunk"
    text: str


class ToolCallStart(BaseModel):
    type: Literal["tool_call_start"] = "tool_call_start"
    tool_call_id: str
    name: str
    arguments: dict[str, Any]


class ToolCallEnd(BaseModel):
    type: Literal["tool_call_end"] = "tool_call_end"
    tool_call_id: str
    result: ToolResult


class Done(BaseModel):
    type: Literal["done"] = "done"
    final_message: str
    usage: dict[str, int] = Field(default_factory=dict)


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    error_code: str
    message: str


class ToolApprovalRequest(BaseModel):
    type: Literal["tool_approval_request"] = "tool_approval_request"
    tool_call_id: str
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = "Tool requires user approval before execution"


AgentEvent = Union[TextChunk, ToolCallStart, ToolCallEnd, Done, ErrorEvent, ToolApprovalRequest]


class AssembleResult(BaseModel):
    messages: list[dict[str, Any]]
    system_prompt_addition: str | None = None
    estimated_tokens: int = 0


CompactionReasonCode = Literal[
    "compacted",
    "no_compactable_entries",
    "below_threshold",
    "already_compacted_recently",
    "live_context_still_exceeds_target",
    "guard_blocked",
    "summary_failed",
    "timeout",
    "aborted",
    "provider_error_4xx",
    "provider_error_5xx",
    "unknown",
]


class CompactResult(BaseModel):
    ok: bool
    compacted: bool
    reason: str | None = None
    reason_code: CompactionReasonCode | None = None
    summary: str | None = None
    first_kept_entry_id: str | None = None
    tokens_before: int = 0
    tokens_after: int | None = None
