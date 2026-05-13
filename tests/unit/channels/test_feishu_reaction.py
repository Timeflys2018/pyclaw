from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.feishu.handler import (
    _reaction_last_handled,
    _reaction_should_handle,
    handle_feishu_reaction_created,
)


def _make_event(message_id: str = "om_abc", emoji: str = "THUMBSUP"):
    event = MagicMock()
    event.event = MagicMock()
    event.event.message_id = message_id
    event.event.reaction_type = MagicMock()
    event.event.reaction_type.emoji_type = emoji
    return event


def _make_ctx(is_bot_msg_return: bool = True, create_reaction_return: bool = True):
    client = MagicMock()
    client.is_bot_message = MagicMock(return_value=is_bot_msg_return)
    client.create_reaction = AsyncMock(return_value=create_reaction_return)
    ctx = MagicMock()
    ctx.feishu_client = client
    return ctx


@pytest.fixture(autouse=True)
def _clear_dedup_state():
    _reaction_last_handled.clear()
    yield
    _reaction_last_handled.clear()


@pytest.mark.asyncio
async def test_reaction_on_bot_message_mirrors_emoji() -> None:
    event = _make_event("om_msg_1", "THUMBSUP")
    ctx = _make_ctx(is_bot_msg_return=True)
    await handle_feishu_reaction_created(event, ctx)
    ctx.feishu_client.create_reaction.assert_awaited_once_with("om_msg_1", "THUMBSUP")


@pytest.mark.asyncio
async def test_reaction_on_non_bot_message_is_ignored() -> None:
    event = _make_event("om_msg_2", "HEART")
    ctx = _make_ctx(is_bot_msg_return=False)
    await handle_feishu_reaction_created(event, ctx)
    ctx.feishu_client.create_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_reaction_without_message_id_is_ignored() -> None:
    event = _make_event("", "THUMBSUP")
    ctx = _make_ctx(is_bot_msg_return=True)
    await handle_feishu_reaction_created(event, ctx)
    ctx.feishu_client.create_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_reaction_without_emoji_type_is_ignored() -> None:
    event = _make_event("om_msg_3", "")
    ctx = _make_ctx(is_bot_msg_return=True)
    await handle_feishu_reaction_created(event, ctx)
    ctx.feishu_client.create_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_reaction_dedup_second_within_5s_is_skipped() -> None:
    event1 = _make_event("om_msg_4", "THUMBSUP")
    event2 = _make_event("om_msg_4", "HEART")
    ctx = _make_ctx(is_bot_msg_return=True)
    await handle_feishu_reaction_created(event1, ctx)
    await handle_feishu_reaction_created(event2, ctx)
    assert ctx.feishu_client.create_reaction.await_count == 1


@pytest.mark.asyncio
async def test_reaction_dedup_different_messages_not_deduped() -> None:
    ctx = _make_ctx(is_bot_msg_return=True)
    await handle_feishu_reaction_created(_make_event("om_msg_5", "THUMBSUP"), ctx)
    await handle_feishu_reaction_created(_make_event("om_msg_6", "HEART"), ctx)
    assert ctx.feishu_client.create_reaction.await_count == 2


def test_reaction_should_handle_dedup_window() -> None:
    assert _reaction_should_handle("m1") is True
    assert _reaction_should_handle("m1") is False
    _reaction_last_handled["m1"] = time.time() - 10.0
    assert _reaction_should_handle("m1") is True


@pytest.mark.asyncio
async def test_handler_tolerates_api_failure() -> None:
    event = _make_event("om_msg_7", "THUMBSUP")
    ctx = _make_ctx(is_bot_msg_return=True, create_reaction_return=False)
    await handle_feishu_reaction_created(event, ctx)
    ctx.feishu_client.create_reaction.assert_awaited_once()


@pytest.mark.asyncio
async def test_handler_tolerates_malformed_event() -> None:
    event = MagicMock()
    event.event = None
    ctx = _make_ctx()
    await handle_feishu_reaction_created(event, ctx)
    ctx.feishu_client.create_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_catches_exception_in_api_call() -> None:
    event = _make_event("om_msg_8", "THUMBSUP")
    ctx = _make_ctx(is_bot_msg_return=True)
    ctx.feishu_client.create_reaction = AsyncMock(side_effect=RuntimeError("network fail"))
    await handle_feishu_reaction_created(event, ctx)
