from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.feishu.command_adapter import FeishuCommandAdapter
from pyclaw.channels.feishu.handler import FeishuContext
from pyclaw.channels.session_router import SessionRouter
from pyclaw.core.commands.registry import CommandRegistry
from pyclaw.core.commands.spec import ALL_CHANNELS, CommandSpec
from pyclaw.infra.settings import FeishuSettings
from pyclaw.storage.session.base import InMemorySessionStore


def _make_event(open_id: str = "ou_abc") -> Any:
    event = MagicMock()
    event.event.sender.sender_id.open_id = open_id
    event.event.message.chat_type = "p2p"
    event.event.message.chat_id = ""
    return event


def _make_ctx(*, queue_idle: bool) -> FeishuContext:
    store = InMemorySessionStore()
    settings = FeishuSettings(enabled=True, app_id="cli_x", app_secret="s")
    feishu_client = MagicMock()
    feishu_client.reply_text = AsyncMock(return_value=None)
    deps = MagicMock()
    deps.session_store = store
    deps.llm.default_model = "m"

    queue_registry = MagicMock()
    queue_registry.enqueue = AsyncMock(return_value=None)
    queue_registry.is_idle = MagicMock(return_value=queue_idle)

    return FeishuContext(
        settings=settings,
        feishu_client=feishu_client,
        deps=deps,
        dedup=MagicMock(),
        workspace_store=MagicMock(),
        bot_open_id="bot_open_id",
        session_router=SessionRouter(store=store),
        workspace_base=Path("/tmp/test-ws"),
        queue_registry=queue_registry,
    )


def _registry_with(spec: CommandSpec) -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(spec)
    return registry


@pytest.mark.asyncio
async def test_busy_blocks_requires_idle_command_with_friendly_reply() -> None:
    handler_called = False

    async def handler(args: str, ctx) -> None:
        nonlocal handler_called
        handler_called = True

    spec = CommandSpec(
        name="/needsidle",
        handler=handler,
        category="test",
        help_text="needs idle",
        channels=ALL_CHANNELS,
        requires_idle=True,
    )

    ctx = _make_ctx(queue_idle=False)
    adapter = FeishuCommandAdapter(_registry_with(spec))

    handled = await adapter.handle(
        text="/needsidle",
        session_key="feishu:cli_x:ou_abc",
        session_id="sid-1",
        message_id="msg-1",
        event=_make_event(),
        ctx=ctx,
    )

    assert handled is True
    assert handler_called is False
    ctx.queue_registry.is_idle.assert_called_once_with("sid-1")
    ctx.feishu_client.reply_text.assert_awaited_once()
    text = ctx.feishu_client.reply_text.await_args.args[1]
    assert "任务运行中" in text


@pytest.mark.asyncio
async def test_idle_passes_requires_idle_to_handler() -> None:
    handler_called = False

    async def handler(args: str, ctx) -> None:
        nonlocal handler_called
        handler_called = True
        await ctx.reply("ok")

    spec = CommandSpec(
        name="/needsidle",
        handler=handler,
        category="test",
        help_text="needs idle",
        channels=ALL_CHANNELS,
        requires_idle=True,
    )

    ctx = _make_ctx(queue_idle=True)
    adapter = FeishuCommandAdapter(_registry_with(spec))

    handled = await adapter.handle(
        text="/needsidle",
        session_key="feishu:cli_x:ou_abc",
        session_id="sid-2",
        message_id="msg-2",
        event=_make_event(),
        ctx=ctx,
    )

    assert handled is True
    assert handler_called is True


@pytest.mark.asyncio
async def test_requires_idle_false_bypasses_idle_check_in_feishu() -> None:
    handler_called = False

    async def handler(args: str, ctx) -> None:
        nonlocal handler_called
        handler_called = True
        await ctx.reply("ok")

    spec = CommandSpec(
        name="/free",
        handler=handler,
        category="test",
        help_text="free",
        channels=ALL_CHANNELS,
        requires_idle=False,
    )

    ctx = _make_ctx(queue_idle=False)
    adapter = FeishuCommandAdapter(_registry_with(spec))

    handled = await adapter.handle(
        text="/free",
        session_key="feishu:cli_x:ou_abc",
        session_id="sid-3",
        message_id="msg-3",
        event=_make_event(),
        ctx=ctx,
    )

    assert handled is True
    assert handler_called is True
    ctx.queue_registry.is_idle.assert_not_called()
