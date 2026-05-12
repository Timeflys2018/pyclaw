"""Tests for memory inspection pure ops (Phase A3-memory)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.storage.memory.base import MemoryEntry
from pyclaw.storage.memory.inspection import (
    list_for_user,
    search_for_user,
    stats_for_user,
)


def _entry(eid: str, layer: str = "L2") -> MemoryEntry:
    now = time.time()
    return MemoryEntry(
        id=eid,
        layer=layer,
        type="insight" if layer == "L2" else "procedure",
        content=f"content-{eid}",
        source_session_id="sess",
        created_at=now,
        updated_at=now,
        status="active",
    )


@pytest.mark.asyncio
async def test_list_for_user_facts() -> None:
    store = MagicMock()
    store.search = AsyncMock(return_value=[_entry("f1", "L2"), _entry("f2", "L2")])

    results = await list_for_user(store, "test:user_x", kind="facts", limit=50)
    assert len(results) == 2
    store.search.assert_awaited_once()
    call_kwargs = store.search.await_args.kwargs
    assert call_kwargs.get("layers") == ["L2"] or "L2" in (call_kwargs.get("layers") or [])


@pytest.mark.asyncio
async def test_list_for_user_procedures() -> None:
    store = MagicMock()
    store.search = AsyncMock(return_value=[_entry("p1", "L3")])

    results = await list_for_user(store, "test:user_x", kind="procedures")
    assert len(results) == 1
    call_kwargs = store.search.await_args.kwargs
    assert call_kwargs.get("layers") == ["L3"] or "L3" in (call_kwargs.get("layers") or [])


@pytest.mark.asyncio
async def test_list_for_user_all() -> None:
    store = MagicMock()
    store.search = AsyncMock(return_value=[_entry("f1", "L2"), _entry("p1", "L3")])

    results = await list_for_user(store, "test:user_x", kind="all", limit=5)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_list_for_user_limit_applied() -> None:
    store = MagicMock()
    store.search = AsyncMock(return_value=[_entry("f1"), _entry("f2"), _entry("f3")])

    results = await list_for_user(store, "test:user_x", kind="facts", limit=2)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_search_for_user_returns_results() -> None:
    store = MagicMock()
    store.search = AsyncMock(return_value=[_entry("f1", "L2"), _entry("p1", "L3")])

    results = await search_for_user(store, "test:user_x", "my query")
    assert len(results) == 2
    store.search.assert_awaited_once()
    args = store.search.await_args
    assert "my query" in args[0] or "my query" in args.kwargs.values()


@pytest.mark.asyncio
async def test_stats_for_user_uses_count_by_layer() -> None:
    store = MagicMock()
    store.count_by_layer = AsyncMock(return_value={"l1": 3, "l2": 7, "l3": 2, "l4": 0})

    stats = await stats_for_user(store, "test:user_x")
    assert stats["l1"] == 3
    assert stats["l2"] == 7
    assert stats["l3"] == 2
    assert stats["l4"] == 0
    store.count_by_layer.assert_awaited_once_with("test:user_x")


@pytest.mark.asyncio
async def test_store_none_raises() -> None:
    with pytest.raises(ValueError, match="memory_store is None"):
        await list_for_user(None, "test:user_x")

    with pytest.raises(ValueError, match="memory_store is None"):
        await search_for_user(None, "test:user_x", "q")

    with pytest.raises(ValueError, match="memory_store is None"):
        await stats_for_user(None, "test:user_x")
