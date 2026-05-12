from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.web.command_adapter import WebCommandAdapter
from pyclaw.infra.settings import Settings
from pyclaw.channels.web.protocol import SERVER_CHAT_DONE
from pyclaw.channels.web.websocket import ConnectionState
from pyclaw.core.commands.registry import CommandRegistry
from pyclaw.core.commands.spec import ALL_CHANNELS, CommandSpec


def _make_state(user_id: str = "me") -> tuple[ConnectionState, AsyncMock]:
    mock_ws = AsyncMock()
    mock_ws.app.state.workspace_base = Path(tempfile.mkdtemp())
    state = ConnectionState(
        ws=mock_ws, ws_session_id="s1", user_id=user_id, authenticated=True,
    )
    return state, mock_ws


class _BusyQueue:
    def __init__(self) -> None:
        self.is_idle_calls: list[str] = []

    def is_idle(self, key: str) -> bool:
        self.is_idle_calls.append(key)
        return False


class _IdleQueue:
    def __init__(self) -> None:
        self.is_idle_calls: list[str] = []

    def is_idle(self, key: str) -> bool:
        self.is_idle_calls.append(key)
        return True


@pytest.mark.asyncio
async def test_busy_state_blocks_requires_idle_command_with_friendly_reply() -> None:
    handler_called = False

    async def handler(args: str, ctx) -> None:
        nonlocal handler_called
        handler_called = True

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="/needsidle",
            handler=handler,
            category="test",
            help_text="needs idle",
            channels=ALL_CHANNELS,
            requires_idle=True,
        )
    )

    state, mock_ws = _make_state()
    queue = _BusyQueue()

    adapter = WebCommandAdapter(registry=registry)
    handled = await adapter.handle(
        text="/needsidle",
        state=state,
        conversation_id="conv-1",
        session_id="web:me:conv-1",
        deps=MagicMock(),
        session_router=MagicMock(),
        workspace_base=mock_ws.app.state.workspace_base,
        settings=Settings(),
        session_queue=queue,
    )

    assert handled is True
    assert handler_called is False
    assert queue.is_idle_calls == ["conv-1"]

    sent = mock_ws.send_json.call_args_list
    assert len(sent) == 1
    payload = sent[0][0][0]
    assert payload["type"] == SERVER_CHAT_DONE
    assert "任务运行中" in payload["data"]["final_message"]


@pytest.mark.asyncio
async def test_idle_state_passes_requires_idle_through_to_handler() -> None:
    handler_called = False

    async def handler(args: str, ctx) -> None:
        nonlocal handler_called
        handler_called = True
        await ctx.reply("ok")

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="/needsidle",
            handler=handler,
            category="test",
            help_text="needs idle",
            channels=ALL_CHANNELS,
            requires_idle=True,
        )
    )

    state, _mock_ws = _make_state()
    queue = _IdleQueue()

    adapter = WebCommandAdapter(registry=registry)
    handled = await adapter.handle(
        text="/needsidle",
        state=state,
        conversation_id="conv-2",
        session_id="web:me:conv-2",
        deps=MagicMock(),
        session_router=MagicMock(),
        workspace_base=Path("/tmp"),
        settings=Settings(),
        session_queue=queue,
    )

    assert handled is True
    assert handler_called is True


@pytest.mark.asyncio
async def test_requires_idle_false_bypasses_idle_check() -> None:
    handler_called = False

    async def handler(args: str, ctx) -> None:
        nonlocal handler_called
        handler_called = True
        await ctx.reply("ok")

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="/free",
            handler=handler,
            category="test",
            help_text="free",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )

    state, _mock_ws = _make_state()
    queue = _BusyQueue()

    adapter = WebCommandAdapter(registry=registry)
    handled = await adapter.handle(
        text="/free",
        state=state,
        conversation_id="conv-3",
        session_id="web:me:conv-3",
        deps=MagicMock(),
        session_router=MagicMock(),
        workspace_base=Path("/tmp"),
        settings=Settings(),
        session_queue=queue,
    )

    assert handled is True
    assert handler_called is True
    assert queue.is_idle_calls == []
