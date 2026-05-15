"""WebSocket message protocol definitions for the Web channel."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class IdentifyMessage:
    type: Literal["identify"] = "identify"
    token: str = ""


@dataclass
class ChatSendMessage:
    type: Literal["chat.send"] = "chat.send"
    conversation_id: str = ""
    content: str = ""
    attachments: list[Any] = field(default_factory=list)
    tier: Literal["read-only", "approval", "yolo"] | None = None


@dataclass
class ChatAbortMessage:
    type: Literal["chat.abort"] = "chat.abort"
    conversation_id: str = ""


@dataclass
class ToolApproveMessage:
    type: Literal["tool.approve"] = "tool.approve"
    conversation_id: str = ""
    tool_call_id: str = ""
    approved: bool = True


@dataclass
class PongMessage:
    type: Literal["pong"] = "pong"


ClientMessage = (
    IdentifyMessage | ChatSendMessage | ChatAbortMessage | ToolApproveMessage | PongMessage
)

SERVER_HELLO = "hello"
SERVER_READY = "ready"
SERVER_CHAT_DELTA = "chat.delta"
SERVER_CHAT_TOOL_START = "chat.tool_start"
SERVER_CHAT_TOOL_END = "chat.tool_end"
SERVER_CHAT_DONE = "chat.done"
SERVER_CHAT_QUEUED = "chat.queued"
SERVER_TOOL_APPROVE_REQUEST = "tool.approve_request"
SERVER_PING = "ping"
SERVER_ERROR = "error"

_CLIENT_MESSAGE_MAP: dict[str, type[ClientMessage]] = {
    "identify": IdentifyMessage,
    "chat.send": ChatSendMessage,
    "chat.abort": ChatAbortMessage,
    "tool.approve": ToolApproveMessage,
    "pong": PongMessage,
}


_VALID_TIERS = frozenset(("read-only", "approval", "yolo"))


def parse_client_message(data: dict[str, Any]) -> ClientMessage | None:
    """Route a raw JSON dict to the appropriate client message dataclass.

    Returns ``None`` if ``type`` is missing, unrecognised, or if a known
    field carries an invalid enum value (e.g. ``tier`` outside
    :data:`_VALID_TIERS`).
    """
    msg_type = data.get("type")
    if msg_type is None:
        return None
    cls = _CLIENT_MESSAGE_MAP.get(msg_type)
    if cls is None:
        return None
    if cls is ChatSendMessage:
        tier_value = data.get("tier")
        if tier_value is not None and tier_value not in _VALID_TIERS:
            return None
    known = {f.name for f in dataclasses.fields(cls)}
    kwargs = {k: v for k, v in data.items() if k in known}
    return cls(**kwargs)
