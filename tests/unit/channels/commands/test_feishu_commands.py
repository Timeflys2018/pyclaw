from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.channels.feishu.command_adapter import FeishuCommandAdapter
from pyclaw.channels.feishu.handler import FeishuContext
from pyclaw.channels.session_router import SessionRouter
from pyclaw.core.commands._helpers import parse_idle_duration
from pyclaw.core.commands.builtin import register_builtin_commands
from pyclaw.core.commands.registry import CommandRegistry
from pyclaw.infra.settings import FeishuSettings, Settings
from pyclaw.storage.session.base import InMemorySessionStore


def _make_registry() -> CommandRegistry:
    registry = CommandRegistry()
    register_builtin_commands(registry)
    return registry


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
    queue_registry = MagicMock()
    queue_registry.enqueue = AsyncMock(return_value=None)
    return FeishuContext(
        settings=feishu_settings,
        settings_full=Settings(),
        feishu_client=feishu_client,
        deps=deps,
        dedup=MagicMock(),
        workspace_store=MagicMock(),
        bot_open_id="bot_open_id",
        session_router=SessionRouter(store=store),
        workspace_base=Path("/tmp/test-workspaces"),
        queue_registry=queue_registry,
    )


def _make_event(open_id: str = "ou_abc", chat_id: str = "", chat_type: str = "p2p") -> Any:
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
async def test_adapter_returns_false_for_non_slash() -> None:
    ctx = _make_ctx()
    adapter = FeishuCommandAdapter(_make_registry())
    result = await adapter.handle(
        text="hello world",
        session_key="key",
        session_id="sid",
        message_id="mid",
        event=_make_event(),
        ctx=ctx,
    )
    assert result is False


@pytest.mark.asyncio
async def test_adapter_returns_false_for_unknown_slash() -> None:
    ctx = _make_ctx()
    adapter = FeishuCommandAdapter(_make_registry())
    result = await adapter.handle(
        text="/unknown",
        session_key="key",
        session_id="sid",
        message_id="mid",
        event=_make_event(),
        ctx=ctx,
    )
    assert result is False


@pytest.mark.asyncio
async def test_adapter_new_rotates_session() -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store)
    sid, _ = await ctx.session_router.resolve_or_create("feishu:cli_x:ou_a", "ws")
    adapter = FeishuCommandAdapter(_make_registry())
    result = await adapter.handle(
        text="/new",
        session_key="feishu:cli_x:ou_a",
        session_id=sid,
        message_id="mid",
        event=_make_event(),
        ctx=ctx,
    )
    assert result is True
    ctx.feishu_client.reply_text.assert_awaited_once()
    msg = ctx.feishu_client.reply_text.call_args[0][1]
    assert "新会话" in msg
    new_sid = await store.get_current_session_id("feishu:cli_x:ou_a")
    assert new_sid != sid


@pytest.mark.asyncio
async def test_adapter_reset_rotates_session() -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store)
    sid, _ = await ctx.session_router.resolve_or_create("feishu:cli_x:ou_a", "ws")
    adapter = FeishuCommandAdapter(_make_registry())
    result = await adapter.handle(
        text="/reset",
        session_key="feishu:cli_x:ou_a",
        session_id=sid,
        message_id="mid",
        event=_make_event(),
        ctx=ctx,
    )
    assert result is True
    new_sid = await store.get_current_session_id("feishu:cli_x:ou_a")
    assert new_sid != sid


@pytest.mark.asyncio
async def test_adapter_status_contains_session_key() -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store)
    sid, _ = await ctx.session_router.resolve_or_create("feishu:cli_x:ou_a", "ws")
    adapter = FeishuCommandAdapter(_make_registry())
    result = await adapter.handle(
        text="/status",
        session_key="feishu:cli_x:ou_a",
        session_id=sid,
        message_id="mid",
        event=_make_event(),
        ctx=ctx,
    )
    assert result is True
    msg = ctx.feishu_client.reply_text.call_args[0][1]
    assert "feishu:cli_x:ou_a" in msg


@pytest.mark.asyncio
async def test_adapter_whoami_p2p() -> None:
    ctx = _make_ctx()
    adapter = FeishuCommandAdapter(_make_registry())
    event = _make_event(open_id="ou_abc", chat_type="p2p")
    await adapter.handle(
        text="/whoami",
        session_key="key",
        session_id="sid",
        message_id="mid",
        event=event,
        ctx=ctx,
    )
    msg = ctx.feishu_client.reply_text.call_args[0][1]
    assert "ou_abc" in msg
    assert "p2p" in msg


@pytest.mark.asyncio
async def test_adapter_whoami_group() -> None:
    ctx = _make_ctx()
    adapter = FeishuCommandAdapter(_make_registry())
    event = _make_event(open_id="ou_abc", chat_id="oc_chat1", chat_type="group")
    await adapter.handle(
        text="/whoami",
        session_key="key",
        session_id="sid",
        message_id="mid",
        event=event,
        ctx=ctx,
    )
    msg = ctx.feishu_client.reply_text.call_args[0][1]
    assert "ou_abc" in msg
    assert "group" in msg
    assert "oc_chat1" in msg


@pytest.mark.asyncio
async def test_adapter_help_lists_commands() -> None:
    ctx = _make_ctx()
    adapter = FeishuCommandAdapter(_make_registry())
    await adapter.handle(
        text="/help",
        session_key="key",
        session_id="sid",
        message_id="mid",
        event=_make_event(),
        ctx=ctx,
    )
    msg = ctx.feishu_client.reply_text.call_args[0][1]
    assert "/new" in msg
    assert "/status" in msg


@pytest.mark.asyncio
async def test_adapter_idle_30m() -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store)
    sid, _ = await ctx.session_router.resolve_or_create("k", "ws")
    adapter = FeishuCommandAdapter(_make_registry())
    await adapter.handle(
        text="/idle 30m",
        session_key="k",
        session_id=sid,
        message_id="mid",
        event=_make_event(),
        ctx=ctx,
    )
    tree = await store.load(sid)
    assert tree is not None
    assert tree.header.idle_minutes_override == 30


@pytest.mark.asyncio
async def test_adapter_idle_off() -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store)
    sid, _ = await ctx.session_router.resolve_or_create("k", "ws")
    adapter = FeishuCommandAdapter(_make_registry())
    await adapter.handle(
        text="/idle off",
        session_key="k",
        session_id=sid,
        message_id="mid",
        event=_make_event(),
        ctx=ctx,
    )
    tree = await store.load(sid)
    assert tree is not None
    assert tree.header.idle_minutes_override is None


def test_parse_idle_duration_minutes() -> None:
    assert parse_idle_duration("30m") == 30
    assert parse_idle_duration("5min") == 5


def test_parse_idle_duration_hours() -> None:
    assert parse_idle_duration("2h") == 120
    assert parse_idle_duration("1hour") == 60


def test_parse_idle_duration_off() -> None:
    assert parse_idle_duration("off") == 0
    assert parse_idle_duration("0") == 0


def test_parse_idle_duration_invalid() -> None:
    assert parse_idle_duration("abc") is None
    assert parse_idle_duration("") is None


@pytest.mark.asyncio
async def test_adapter_handler_exception_replies_error_and_returns_true() -> None:
    ctx = _make_ctx()
    registry = CommandRegistry()

    async def boom(args: str, c) -> None:
        raise RuntimeError("boom")

    from pyclaw.core.commands.spec import CommandSpec
    registry.register(
        CommandSpec(name="/boom", handler=boom, category="t", help_text="t")
    )
    adapter = FeishuCommandAdapter(registry)
    result = await adapter.handle(
        text="/boom",
        session_key="k",
        session_id="s",
        message_id="mid",
        event=_make_event(),
        ctx=ctx,
    )
    assert result is True
    msg = ctx.feishu_client.reply_text.call_args[0][1]
    assert "/boom" in msg
    assert "失败" in msg


@pytest.mark.asyncio
async def test_adapter_cancelled_error_propagates() -> None:
    ctx = _make_ctx()
    registry = CommandRegistry()

    async def cancelled(args: str, c) -> None:
        raise asyncio.CancelledError()

    from pyclaw.core.commands.spec import CommandSpec
    registry.register(
        CommandSpec(name="/cancel", handler=cancelled, category="t", help_text="t")
    )
    adapter = FeishuCommandAdapter(registry)
    with pytest.raises(asyncio.CancelledError):
        await adapter.handle(
            text="/cancel",
            session_key="k",
            session_id="s",
            message_id="mid",
            event=_make_event(),
            ctx=ctx,
        )


@pytest.mark.asyncio
async def test_adapter_agent_aborted_error_propagates() -> None:
    from pyclaw.core.agent.runtime_util import AgentAbortedError
    from pyclaw.core.commands.spec import CommandSpec

    ctx = _make_ctx()
    registry = CommandRegistry()

    async def aborted(args: str, c) -> None:
        raise AgentAbortedError("aborted")

    registry.register(
        CommandSpec(name="/abort", handler=aborted, category="t", help_text="t")
    )
    adapter = FeishuCommandAdapter(registry)
    with pytest.raises(AgentAbortedError):
        await adapter.handle(
            text="/abort",
            session_key="k",
            session_id="s",
            message_id="mid",
            event=_make_event(),
            ctx=ctx,
        )
