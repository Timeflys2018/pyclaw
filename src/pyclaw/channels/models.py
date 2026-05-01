from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FeishuSenderId:
    open_id: str = ""
    user_id: str = ""
    union_id: str = ""


@dataclass
class FeishuSender:
    sender_id: FeishuSenderId = field(default_factory=FeishuSenderId)
    sender_type: str = ""


@dataclass
class FeishuMessage:
    message_id: str = ""
    root_id: str = ""
    parent_id: str = ""
    thread_id: str = ""
    chat_id: str = ""
    chat_type: str = ""
    message_type: str = ""
    content: str = ""
    mentions: list[dict] = field(default_factory=list)  # type: ignore[type-arg]


@dataclass
class FeishuEvent:
    sender: FeishuSender = field(default_factory=FeishuSender)
    message: FeishuMessage = field(default_factory=FeishuMessage)
