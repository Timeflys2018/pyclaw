from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.core.commands.builtin import cmd_help, register_builtin_commands
from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.registry import CommandRegistry
from pyclaw.infra.settings import Settings


def _make_ctx(registry: CommandRegistry) -> tuple[CommandContext, AsyncMock]:
    reply = AsyncMock()
    ctx = CommandContext(
        session_id="s",
        session_key="k",
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
        registry=registry,
    )
    return ctx, reply


@pytest.mark.asyncio
async def test_help_includes_runtime_operations_section() -> None:
    registry = CommandRegistry()
    register_builtin_commands(registry)
    ctx, reply = _make_ctx(registry)

    await cmd_help("", ctx)

    msg = reply.await_args[0][0]
    assert "⚡ Runtime Operations" in msg


@pytest.mark.asyncio
async def test_help_lists_stop_under_runtime_section() -> None:
    registry = CommandRegistry()
    register_builtin_commands(registry)
    ctx, reply = _make_ctx(registry)

    await cmd_help("", ctx)

    msg = reply.await_args[0][0]
    runtime_idx = msg.index("⚡ Runtime Operations")
    runtime_section = msg[runtime_idx:]
    assert "/stop" in runtime_section
    assert "停止当前运行" in runtime_section


@pytest.mark.asyncio
async def test_help_marks_requires_idle_commands_with_lock_emoji() -> None:
    registry = CommandRegistry()
    register_builtin_commands(registry)
    ctx, reply = _make_ctx(registry)

    await cmd_help("", ctx)

    msg = reply.await_args[0][0]
    new_line = next(line for line in msg.splitlines() if "/new " in line)
    extract_line = next(line for line in msg.splitlines() if "/extract" in line)
    status_line = next(line for line in msg.splitlines() if "/status" in line)

    assert "🔒" in new_line
    assert "🔒" in extract_line
    assert "🔒" not in status_line


@pytest.mark.asyncio
async def test_help_includes_lock_legend_when_any_idle_locked_present() -> None:
    registry = CommandRegistry()
    register_builtin_commands(registry)
    ctx, reply = _make_ctx(registry)

    await cmd_help("", ctx)

    msg = reply.await_args[0][0]
    assert "🔒 标记的命令需要 runner 闲置时执行" in msg


@pytest.mark.asyncio
async def test_runtime_section_appears_after_categories() -> None:
    registry = CommandRegistry()
    register_builtin_commands(registry)
    ctx, reply = _make_ctx(registry)

    await cmd_help("", ctx)

    msg = reply.await_args[0][0]
    last_category_idx = msg.rfind("📂 ")
    runtime_idx = msg.index("⚡ Runtime Operations")
    assert runtime_idx > last_category_idx
