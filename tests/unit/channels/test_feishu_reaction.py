from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.feishu.handler import (
    _reaction_last_handled,
    _reaction_should_handle,
    _synthetic_reaction_prompt,
    handle_feishu_reaction_created,
)


def _make_event(
    message_id: str = "om_abc",
    emoji: str = "THUMBSUP",
    open_id: str = "ou_user_1",
):
    event = MagicMock()
    event.event = MagicMock()
    event.event.message_id = message_id
    event.event.reaction_type = MagicMock()
    event.event.reaction_type.emoji_type = emoji
    event.event.user_id = MagicMock()
    event.event.user_id.open_id = open_id
    return event


def _make_ctx(
    *,
    is_bot_msg_return: bool = True,
    current_session_id: str | None = "feishu:app_1:ou_user_1:s:abc12345",
    queue_registry_is_none: bool = False,
):
    client = MagicMock()
    client.is_bot_message = MagicMock(return_value=is_bot_msg_return)
    client.create_reaction = AsyncMock(return_value=True)

    store = MagicMock()
    store.get_current_session_id = AsyncMock(return_value=current_session_id)

    router = MagicMock()
    router.store = store
    router.update_last_interaction = AsyncMock()

    if queue_registry_is_none:
        qr = None
    else:
        qr = MagicMock()

        async def _capture_and_close(_sid, coro, **_kwargs):
            coro.close()

        qr.enqueue = AsyncMock(side_effect=_capture_and_close)

    settings = MagicMock()
    settings.app_id = "app_1"

    ctx = MagicMock()
    ctx.feishu_client = client
    ctx.session_router = router
    ctx.queue_registry = qr
    ctx.settings = settings
    ctx.workspace_base = MagicMock()
    return ctx


@pytest.fixture(autouse=True)
def _clear_dedup_state():
    _reaction_last_handled.clear()
    yield
    _reaction_last_handled.clear()


@pytest.mark.asyncio
async def test_reaction_on_bot_message_dispatches_synthetic_prompt() -> None:
    event = _make_event("om_msg_1", "THUMBSUP", "ou_user_1")
    ctx = _make_ctx()
    await handle_feishu_reaction_created(event, ctx)
    ctx.queue_registry.enqueue.assert_awaited_once()
    call_args = ctx.queue_registry.enqueue.await_args
    assert call_args.args[0] == "feishu:app_1:ou_user_1:s:abc12345"


@pytest.mark.asyncio
async def test_reaction_on_non_bot_message_is_ignored() -> None:
    event = _make_event("om_other_user_msg", "HEART", "ou_user_1")
    ctx = _make_ctx(is_bot_msg_return=False)
    await handle_feishu_reaction_created(event, ctx)
    ctx.queue_registry.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_reaction_without_message_id_is_ignored() -> None:
    event = _make_event("", "THUMBSUP", "ou_user_1")
    ctx = _make_ctx()
    await handle_feishu_reaction_created(event, ctx)
    ctx.queue_registry.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_reaction_without_emoji_type_is_ignored() -> None:
    event = _make_event("om_msg_3", "", "ou_user_1")
    ctx = _make_ctx()
    await handle_feishu_reaction_created(event, ctx)
    ctx.queue_registry.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_reaction_without_user_open_id_is_ignored() -> None:
    event = _make_event("om_msg_3", "THUMBSUP", "")
    ctx = _make_ctx()
    await handle_feishu_reaction_created(event, ctx)
    ctx.queue_registry.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_reaction_with_no_active_session_is_ignored() -> None:
    event = _make_event("om_msg_4", "THUMBSUP", "ou_new_user")
    ctx = _make_ctx(current_session_id=None)
    await handle_feishu_reaction_created(event, ctx)
    ctx.queue_registry.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_reaction_dedup_second_within_10s_is_skipped() -> None:
    event1 = _make_event("om_msg_5", "THUMBSUP", "ou_user_1")
    event2 = _make_event("om_msg_5", "HEART", "ou_user_1")
    ctx = _make_ctx()
    await handle_feishu_reaction_created(event1, ctx)
    await handle_feishu_reaction_created(event2, ctx)
    assert ctx.queue_registry.enqueue.await_count == 1


@pytest.mark.asyncio
async def test_reaction_dedup_different_messages_not_deduped() -> None:
    ctx = _make_ctx()
    await handle_feishu_reaction_created(_make_event("om_msg_6", "THUMBSUP", "ou_u"), ctx)
    await handle_feishu_reaction_created(_make_event("om_msg_7", "HEART", "ou_u"), ctx)
    assert ctx.queue_registry.enqueue.await_count == 2


def test_reaction_dedup_window_is_10_seconds() -> None:
    assert _reaction_should_handle("m1") is True
    assert _reaction_should_handle("m1") is False
    _reaction_last_handled["m1"] = time.time() - 11.0
    assert _reaction_should_handle("m1") is True


@pytest.mark.asyncio
async def test_handler_tolerates_missing_queue_registry() -> None:
    event = _make_event("om_msg_8", "THUMBSUP", "ou_user_1")
    ctx = _make_ctx(queue_registry_is_none=True)
    await handle_feishu_reaction_created(event, ctx)


@pytest.mark.asyncio
async def test_handler_tolerates_malformed_event() -> None:
    event = MagicMock()
    event.event = None
    ctx = _make_ctx()
    await handle_feishu_reaction_created(event, ctx)
    ctx.queue_registry.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_tolerates_exception_in_dispatch() -> None:
    event = _make_event("om_msg_9", "THUMBSUP", "ou_user_1")
    ctx = _make_ctx()

    async def _raise(_sid, coro, **_kwargs):
        coro.close()
        raise RuntimeError("queue fail")

    ctx.queue_registry.enqueue = AsyncMock(side_effect=_raise)
    await handle_feishu_reaction_created(event, ctx)


def test_synthetic_prompt_contains_emoji_name_and_instruction() -> None:
    prompt = _synthetic_reaction_prompt("THUMBSUP")
    assert "THUMBSUP" in prompt
    assert "SYSTEM SIGNAL" in prompt


def test_synthetic_prompt_is_deterministic() -> None:
    p1 = _synthetic_reaction_prompt("HEART")
    p2 = _synthetic_reaction_prompt("HEART")
    assert p1 == p2
