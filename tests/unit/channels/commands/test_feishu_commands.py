from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.feishu.commands import (
    _cmd_history,
    _cmd_idle,
    _cmd_whoami,
    handle_command,
    _parse_idle_duration,
)
from pyclaw.channels.feishu.handler import FeishuContext
from pyclaw.channels.session_router import SessionRouter
from pyclaw.infra.settings import FeishuSettings
from pyclaw.storage.session.base import InMemorySessionStore


def _make_ctx(store: InMemorySessionStore | None = None) -> FeishuContext:
    if store is None:
        store = InMemorySessionStore()
    feishu_settings = FeishuSettings(enabled=True, app_id="cli_x", app_secret="s")
    feishu_client = MagicMock()
    feishu_client.reply_text = AsyncMock(return_value=None)
    deps = MagicMock()
    deps.session_store = store
    deps.llm = MagicMock()
    deps.llm.default_model = "test-model"
    dedup = MagicMock()
    workspace_store = MagicMock()
    router = SessionRouter(store=store)
    return FeishuContext(
        settings=feishu_settings,
        feishu_client=feishu_client,
        deps=deps,
        dedup=dedup,
        workspace_store=workspace_store,
        bot_open_id="bot_open_id",
        session_router=router,
        workspace_base=Path("/tmp/test-workspaces"),
    )


def _make_p2p_event(open_id: str = "ou_abc", chat_id: str = "", chat_type: str = "p2p") -> Any:
    event = MagicMock()
    event.event.sender.sender_id.open_id = open_id
    event.event.message.chat_type = chat_type
    event.event.message.chat_id = chat_id
    event.event.message.thread_id = ""
    event.event.message.message_type = "text"
    event.event.message.content = '{"text": "/new"}'
    event.event.message.mentions = []
    return event


@pytest.mark.asyncio
async def test_handle_command_returns_false_for_non_slash() -> None:
    ctx = _make_ctx()
    result = await handle_command("hello world", "key", "sid", "mid", MagicMock(), ctx)
    assert result is False


@pytest.mark.asyncio
async def test_handle_command_returns_false_for_unknown_slash() -> None:
    ctx = _make_ctx()
    result = await handle_command("/unknown", "key", "sid", "mid", MagicMock(), ctx)
    assert result is False


@pytest.mark.asyncio
async def test_cmd_new_rotates_session() -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store)
    old_id, _ = await ctx.session_router.resolve_or_create("feishu:cli_x:ou_a", "ws")

    result = await handle_command("/new", "feishu:cli_x:ou_a", old_id, "mid", MagicMock(), ctx)
    assert result is True
    ctx.feishu_client.reply_text.assert_awaited_once()
    reply_text = ctx.feishu_client.reply_text.call_args[0][1]
    assert "新会话" in reply_text

    new_id = await store.get_current_session_id("feishu:cli_x:ou_a")
    assert new_id != old_id


@pytest.mark.asyncio
async def test_cmd_reset_rotates_session() -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store)
    old_id, _ = await ctx.session_router.resolve_or_create("feishu:cli_x:ou_a", "ws")

    result = await handle_command("/reset", "feishu:cli_x:ou_a", old_id, "mid", MagicMock(), ctx)
    assert result is True
    new_id = await store.get_current_session_id("feishu:cli_x:ou_a")
    assert new_id != old_id


@pytest.mark.asyncio
async def test_cmd_status_format_contains_session_key() -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store)
    sid, _ = await ctx.session_router.resolve_or_create("feishu:cli_x:ou_a", "ws")

    result = await handle_command("/status", "feishu:cli_x:ou_a", sid, "mid", MagicMock(), ctx)
    assert result is True
    reply_text = ctx.feishu_client.reply_text.call_args[0][1]
    assert "feishu:cli_x:ou_a" in reply_text


@pytest.mark.asyncio
async def test_cmd_whoami_p2p() -> None:
    ctx = _make_ctx()
    event = _make_p2p_event(open_id="ou_abc", chat_type="p2p")
    reply = _cmd_whoami(event)
    assert "ou_abc" in reply
    assert "p2p" in reply


@pytest.mark.asyncio
async def test_cmd_whoami_group() -> None:
    ctx = _make_ctx()
    event = _make_p2p_event(open_id="ou_abc", chat_id="oc_chat1", chat_type="group")
    reply = _cmd_whoami(event)
    assert "ou_abc" in reply
    assert "group" in reply
    assert "oc_chat1" in reply


@pytest.mark.asyncio
async def test_cmd_history_empty() -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store)
    sid, _ = await ctx.session_router.resolve_or_create("key1", "ws")
    reply = await _cmd_history("key1", ctx)
    assert "只有一个会话" in reply or "历史会话" in reply


@pytest.mark.asyncio
async def test_cmd_history_with_entries() -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store)
    await ctx.session_router.resolve_or_create("key1", "ws")
    await ctx.session_router.rotate("key1", "ws")
    reply = await _cmd_history("key1", ctx)
    assert "历史会话" in reply


@pytest.mark.asyncio
async def test_cmd_help_lists_commands() -> None:
    ctx = _make_ctx()
    result = await handle_command("/help", "key", "sid", "mid", MagicMock(), ctx)
    assert result is True
    reply_text = ctx.feishu_client.reply_text.call_args[0][1]
    assert "/new" in reply_text
    assert "/status" in reply_text


@pytest.mark.asyncio
async def test_cmd_idle_sets_30m() -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store)
    sid, _ = await ctx.session_router.resolve_or_create("key1", "ws")
    reply = await _cmd_idle("30m", sid, ctx)
    assert "30" in reply or "分钟" in reply
    tree = await store.load(sid)
    assert tree is not None
    assert tree.header.idle_minutes_override == 30


@pytest.mark.asyncio
async def test_cmd_idle_off_disables() -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store)
    sid, _ = await ctx.session_router.resolve_or_create("key1", "ws")
    reply = await _cmd_idle("off", sid, ctx)
    assert "关闭" in reply
    tree = await store.load(sid)
    assert tree is not None
    assert tree.header.idle_minutes_override is None


def test_parse_idle_duration_minutes() -> None:
    assert _parse_idle_duration("30m") == 30
    assert _parse_idle_duration("5min") == 5


def test_parse_idle_duration_hours() -> None:
    assert _parse_idle_duration("2h") == 120
    assert _parse_idle_duration("1hour") == 60


def test_parse_idle_duration_off() -> None:
    assert _parse_idle_duration("off") == 0
    assert _parse_idle_duration("0") == 0


def test_parse_idle_duration_invalid() -> None:
    assert _parse_idle_duration("abc") is None
    assert _parse_idle_duration("") is None
