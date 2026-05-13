from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.feishu.queue import FeishuQueueRegistry
from pyclaw.channels.web.chat import SessionQueue
from pyclaw.infra.task_manager import TaskManager
from pyclaw.models import Done


class _StubEvents:
    def __init__(self, usage: dict[str, int]) -> None:
        self._usage = usage

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._usage is None:
            raise StopAsyncIteration
        ev = Done(final_message="ok", usage=self._usage)
        self._usage = None
        return ev


@pytest.mark.asyncio
async def test_feishu_dispatch_message_captures_done_usage_into_registry() -> None:
    from pyclaw.channels.feishu.dispatch import dispatch_message

    tm = TaskManager()
    registry = FeishuQueueRegistry(task_manager=tm)
    assert registry.get_last_usage("sess-1") is None

    deps = MagicMock()
    inbound = MagicMock()
    inbound.session_id = "sess-1"
    inbound.workspace_id = "ws"
    inbound.user_message = "hi"
    inbound.attachments = []

    usage = {"input": 100, "output": 20, "cache_creation": 0, "cache_read": 50}

    class StubStream:
        def __init__(self, ev) -> None:
            self._ev = ev

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._ev is None:
                raise StopAsyncIteration
            e = self._ev
            self._ev = None
            return e

    stream_returned = StubStream(Done(final_message="reply", usage=usage))

    import pyclaw.channels.feishu.dispatch as dispatch_mod
    original = dispatch_mod.run_agent_stream
    dispatch_mod.run_agent_stream = lambda *a, **kw: stream_returned
    try:
        events_collected = []
        async for ev in dispatch_message(
            inbound, deps, workspace_path=Path("/tmp"), queue_registry=registry,
        ):
            events_collected.append(ev)
    finally:
        dispatch_mod.run_agent_stream = original

    assert len(events_collected) == 1
    assert registry.get_last_usage("sess-1") == usage


@pytest.mark.asyncio
async def test_feishu_command_adapter_reads_last_usage_into_command_context() -> None:
    from pyclaw.channels.feishu.command_adapter import FeishuCommandAdapter
    from pyclaw.core.commands.registry import CommandRegistry
    from pyclaw.core.commands.spec import ALL_CHANNELS, CommandSpec

    tm = TaskManager()
    queue_registry = FeishuQueueRegistry(task_manager=tm)
    usage = {"input": 7000, "output": 900, "cache_creation": 100, "cache_read": 3000}
    queue_registry.set_last_usage("skey:s:abcd", usage)

    captured: dict[str, Any] = {}

    async def fake_handler(args: str, ctx) -> None:
        captured["last_usage"] = ctx.last_usage

    cmd_registry = CommandRegistry()
    cmd_registry.register(CommandSpec(
        name="/probe",
        handler=fake_handler,
        category="inspection",
        help_text="",
        channels=ALL_CHANNELS,
    ))

    adapter = FeishuCommandAdapter(cmd_registry)
    fctx = MagicMock()
    fctx.deps = MagicMock()
    fctx.session_router = MagicMock()
    fctx.session_router.resolve_or_create = AsyncMock(
        return_value=("skey:s:abcd", MagicMock())
    )
    fctx.session_router.update_last_interaction = AsyncMock()
    fctx.queue_registry = queue_registry
    fctx.redis_client = None
    fctx.memory_store = None
    fctx.evolution_settings = None
    fctx.nudge_hook = None
    fctx.agent_settings = MagicMock()
    fctx.settings_full = MagicMock()
    fctx.settings = MagicMock()
    fctx.settings.session_scope = "user"
    fctx.admin_user_ids = []
    fctx.workspace_base = Path("/tmp")
    fctx.feishu_client = MagicMock()
    fctx.feishu_client.reply_text = AsyncMock()

    fevent = MagicMock()
    fevent.event = MagicMock()
    fevent.event.sender = MagicMock()
    fevent.event.sender.sender_id = MagicMock()
    fevent.event.sender.sender_id.open_id = "ou_abc"
    fevent.event.message = MagicMock()
    fevent.event.message.chat_type = "p2p"
    fevent.event.message.chat_id = "chat_id_x"

    handled = await adapter.handle(
        text="/probe",
        session_key="skey",
        session_id="skey:s:abcd",
        message_id="msg-1",
        event=fevent,
        ctx=fctx,
    )
    assert handled is True
    assert captured["last_usage"] == usage


@pytest.mark.asyncio
async def test_web_session_queue_captures_done_usage_via_set_last_usage() -> None:
    sq = SessionQueue()
    assert sq.get_last_usage("conv-1") is None

    done = Done(final_message="ok", usage={"input": 500, "output": 50, "cache_creation": 0, "cache_read": 200})
    sq.set_last_usage("conv-1", done.usage)
    assert sq.get_last_usage("conv-1") == done.usage


def test_set_last_usage_rejects_none_safely() -> None:
    sq = SessionQueue()
    sq.set_last_usage("conv-1", None)
    assert sq.get_last_usage("conv-1") is None

    tm = TaskManager()
    fq = FeishuQueueRegistry(task_manager=tm)
    fq.set_last_usage("sess-1", None)
    assert fq.get_last_usage("sess-1") is None


def test_set_last_usage_filters_non_int_values() -> None:
    sq = SessionQueue()
    sq.set_last_usage("conv-1", {"input": 100, "corrupt": "abc", "output": 50})  # type: ignore[dict-item]
    result = sq.get_last_usage("conv-1")
    assert result is not None
    assert result == {"input": 100, "output": 50}
    assert "corrupt" not in result
