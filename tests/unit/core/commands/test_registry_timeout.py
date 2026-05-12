from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.infra.settings import Settings
from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.registry import CommandRegistry
from pyclaw.core.commands.spec import ALL_CHANNELS, CommandSpec


def _make_ctx(reply: AsyncMock, *, command_timeout: float = 30.0) -> CommandContext:
    return CommandContext(
        session_id="s",
        session_key="key",
        workspace_id="ws",
        user_id="u",
        channel="web",
        deps=MagicMock(),
        session_router=MagicMock(),
        workspace_base=Path("/tmp"),
        reply=reply,
        dispatch_user_message=AsyncMock(),
        raw={"channel": "web"},
        settings=Settings(),
        command_timeout=command_timeout,
    )


@pytest.mark.asyncio
async def test_dispatch_times_out_slow_handler() -> None:
    async def slow_handler(args: str, ctx: CommandContext) -> None:
        await asyncio.sleep(5.0)

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="/slow",
            handler=slow_handler,
            category="test",
            help_text="slow",
            channels=ALL_CHANNELS,
        )
    )

    reply = AsyncMock()
    ctx = _make_ctx(reply, command_timeout=0.05)

    handled = await registry.dispatch("/slow", "", ctx)

    assert handled is True
    reply.assert_awaited_once()
    msg = reply.await_args.args[0]
    assert "/slow" in msg
    assert "0.05" in msg


@pytest.mark.asyncio
async def test_dispatch_does_not_timeout_fast_handler() -> None:
    handler_invoked = False

    async def fast_handler(args: str, ctx: CommandContext) -> None:
        nonlocal handler_invoked
        handler_invoked = True

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="/fast",
            handler=fast_handler,
            category="test",
            help_text="fast",
            channels=ALL_CHANNELS,
        )
    )

    reply = AsyncMock()
    ctx = _make_ctx(reply, command_timeout=5.0)

    handled = await registry.dispatch("/fast", "", ctx)

    assert handled is True
    assert handler_invoked is True
    reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_does_not_inspect_run_control_or_idle_state() -> None:
    captured_ctx: list[CommandContext] = []

    async def handler(args: str, ctx: CommandContext) -> None:
        captured_ctx.append(ctx)

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="/x",
            handler=handler,
            category="test",
            help_text="x",
            channels=ALL_CHANNELS,
            requires_idle=True,
        )
    )

    reply = AsyncMock()
    ctx = _make_ctx(reply)

    await registry.dispatch("/x", "", ctx)

    assert len(captured_ctx) == 1
    assert not hasattr(captured_ctx[0], "run_control")
    assert not hasattr(captured_ctx[0], "abort_event")
