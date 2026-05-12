"""Tests for /memory slash command (Phase C)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.memory import cmd_memory
from pyclaw.storage.memory.base import MemoryEntry


def _entry(eid: str, layer: str = "L2", content: str = "hello") -> MemoryEntry:
    now = time.time()
    return MemoryEntry(
        id=eid,
        layer=layer,
        type="insight" if layer == "L2" else "procedure",
        content=content,
        source_session_id="sess",
        created_at=now,
        updated_at=now,
        use_count=0,
        status="active",
    )


def _ctx(*, memory_store=None, reply=None) -> CommandContext:
    deps = MagicMock()
    return CommandContext(
        session_id="s1",
        session_key="web:user_x",
        workspace_id="ws",
        user_id="user_x",
        channel="web",
        deps=deps,
        session_router=MagicMock(),
        workspace_base=Path("/tmp"),
        reply=reply or AsyncMock(),
        dispatch_user_message=AsyncMock(),
        raw={},
        memory_store=memory_store,
    )


@pytest.mark.asyncio
async def test_memory_usage_without_args() -> None:
    reply = AsyncMock()
    await cmd_memory("", _ctx(reply=reply))
    assert "用法" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_memory_store_none_returns_error() -> None:
    reply = AsyncMock()
    await cmd_memory("list", _ctx(memory_store=None, reply=reply))
    assert "Memory store 未初始化" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_memory_list_empty() -> None:
    store = MagicMock()
    store.search = AsyncMock(return_value=[])
    reply = AsyncMock()

    await cmd_memory("list", _ctx(memory_store=store, reply=reply))
    assert "无 all 记忆" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_memory_list_facts_filter() -> None:
    store = MagicMock()
    store.search = AsyncMock(return_value=[_entry("f1", "L2", "fact content")])
    reply = AsyncMock()

    await cmd_memory("list --facts", _ctx(memory_store=store, reply=reply))
    msg = reply.await_args[0][0]
    assert "facts" in msg
    assert "fact content" in msg
    layers_kw = store.search.await_args.kwargs.get("layers")
    assert layers_kw == ["L2"]


@pytest.mark.asyncio
async def test_memory_list_procedures_filter() -> None:
    store = MagicMock()
    store.search = AsyncMock(return_value=[_entry("p1", "L3", "proc content")])
    reply = AsyncMock()

    await cmd_memory("list --procedures", _ctx(memory_store=store, reply=reply))
    assert store.search.await_args.kwargs.get("layers") == ["L3"]


@pytest.mark.asyncio
async def test_memory_list_respects_limit() -> None:
    store = MagicMock()
    store.search = AsyncMock(return_value=[_entry(f"e{i}") for i in range(100)])
    reply = AsyncMock()

    await cmd_memory("list --limit 3", _ctx(memory_store=store, reply=reply))
    msg = reply.await_args[0][0]
    assert "上限 3" in msg


@pytest.mark.asyncio
async def test_memory_search_empty_query() -> None:
    store = MagicMock()
    store.search = AsyncMock(return_value=[])
    reply = AsyncMock()

    await cmd_memory("search", _ctx(memory_store=store, reply=reply))
    assert "用法" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_memory_search_returns_results() -> None:
    store = MagicMock()
    store.search = AsyncMock(return_value=[
        _entry("f1", "L2", "matching fact"),
        _entry("p1", "L3", "matching proc"),
    ])
    reply = AsyncMock()

    await cmd_memory("search hello world", _ctx(memory_store=store, reply=reply))
    msg = reply.await_args[0][0]
    assert "搜索结果" in msg
    assert "hello world" in msg
    assert "matching fact" in msg
    assert "matching proc" in msg


@pytest.mark.asyncio
async def test_memory_search_no_match() -> None:
    store = MagicMock()
    store.search = AsyncMock(return_value=[])
    reply = AsyncMock()

    await cmd_memory("search foo", _ctx(memory_store=store, reply=reply))
    assert "无匹配" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_memory_stats_shows_all_layers() -> None:
    store = MagicMock()
    store.count_by_layer = AsyncMock(return_value={"l1": 3, "l2": 7, "l3": 2, "l4": 5})
    reply = AsyncMock()

    await cmd_memory("stats", _ctx(memory_store=store, reply=reply))
    msg = reply.await_args[0][0]
    assert "L1" in msg and "3" in msg
    assert "L2" in msg and "7" in msg
    assert "L3" in msg and "2" in msg
    assert "L4" in msg and "5" in msg


@pytest.mark.asyncio
async def test_memory_unknown_subcommand() -> None:
    reply = AsyncMock()
    await cmd_memory("unknown", _ctx(memory_store=MagicMock(), reply=reply))
    assert "未知子命令" in reply.await_args[0][0]
