from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from pyclaw.channels.feishu.handler import (
    build_session_id,
    extract_text_from_event,
    is_bot_mentioned,
)


def _make_event(
    chat_type: str = "p2p",
    chat_id: str = "cid1",
    open_id: str = "uid1",
    thread_id: str = "",
    msg_type: str = "text",
    content: str = '{"text": "hi"}',
    mentions: list[Any] | None = None,
) -> Any:
    event = MagicMock()
    event.event.sender.sender_id.open_id = open_id
    event.event.message.chat_type = chat_type
    event.event.message.chat_id = chat_id
    event.event.message.thread_id = thread_id
    event.event.message.message_type = msg_type
    event.event.message.content = content
    event.event.message.mentions = mentions or []
    return event


def test_build_session_id_p2p() -> None:
    event = _make_event(chat_type="p2p", open_id="ou_abc")
    sid = build_session_id("app1", event, "chat")
    assert sid == "feishu:app1:ou_abc"


def test_build_session_id_group_chat() -> None:
    event = _make_event(chat_type="group", chat_id="oc_group1", open_id="ou_abc")
    sid = build_session_id("app1", event, "chat")
    assert sid == "feishu:app1:oc_group1"


def test_build_session_id_group_user() -> None:
    event = _make_event(chat_type="group", chat_id="oc_group1", open_id="ou_abc")
    sid = build_session_id("app1", event, "user")
    assert sid == "feishu:app1:oc_group1:ou_abc"


def test_build_session_id_group_thread() -> None:
    event = _make_event(chat_type="group", chat_id="oc_group1", open_id="ou_abc", thread_id="t123")
    sid = build_session_id("app1", event, "thread")
    assert sid == "feishu:app1:oc_group1:thread:t123"


def test_is_bot_mentioned_true() -> None:
    mention = MagicMock()
    mention.id.open_id = "bot_open_id"
    event = _make_event(mentions=[mention])
    assert is_bot_mentioned(event, "bot_open_id")


def test_is_bot_mentioned_false() -> None:
    mention = MagicMock()
    mention.id.open_id = "other_id"
    event = _make_event(mentions=[mention])
    assert not is_bot_mentioned(event, "bot_open_id")


def test_extract_text_from_text_message() -> None:
    event = _make_event(msg_type="text", content='{"text": "hello world"}')
    text = extract_text_from_event(event)
    assert text == "hello world"


def test_extract_text_from_post_message() -> None:
    content = '{"zh_cn": {"title": "title", "content": [[{"tag": "text", "text": "hi there"}]]}}'
    event = _make_event(msg_type="post", content=content)
    text = extract_text_from_event(event)
    assert text is not None
    assert "hi there" in text


def test_extract_text_returns_none_for_image() -> None:
    event = _make_event(msg_type="image", content='{"image_key": "img_abc"}')
    text = extract_text_from_event(event)
    assert text is None
