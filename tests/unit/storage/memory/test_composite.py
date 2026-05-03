from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from pyclaw.storage.memory.base import ArchiveEntry, MemoryEntry, MemoryStore
from pyclaw.storage.memory.composite import CompositeMemoryStore


def _make_entry(
    entry_id: str = "e1",
    layer: str = "L2",
    content: str = "hello world",
) -> MemoryEntry:
    now = time.time()
    return MemoryEntry(
        id=entry_id,
        layer=layer,
        type="insight",
        content=content,
        source_session_id="sess-1",
        created_at=now,
        updated_at=now,
    )


def _make_backends() -> tuple[MagicMock, MagicMock]:
    l1 = MagicMock()
    l1.index_get = AsyncMock(return_value=[])
    l1.index_update = AsyncMock()
    l1.index_remove = AsyncMock()
    l1.close = AsyncMock()

    sqlite = MagicMock()
    sqlite.store = AsyncMock()
    sqlite.search = AsyncMock(return_value=[])
    sqlite.delete = AsyncMock()
    sqlite.archive_session = AsyncMock()
    sqlite.search_archives = AsyncMock(return_value=[])
    sqlite.close = AsyncMock()

    return l1, sqlite


async def test_index_get_delegates_to_l1() -> None:
    l1, sqlite = _make_backends()
    entry = _make_entry()
    l1.index_get.return_value = [entry]

    store = CompositeMemoryStore(l1=l1, sqlite=sqlite)
    result = await store.index_get("sess-A")

    l1.index_get.assert_awaited_once_with("sess-A")
    assert result == [entry]


async def test_index_update_delegates_to_l1() -> None:
    l1, sqlite = _make_backends()
    entry = _make_entry(layer="L1")

    store = CompositeMemoryStore(l1=l1, sqlite=sqlite)
    await store.index_update("sess-A", entry)

    l1.index_update.assert_awaited_once_with("sess-A", entry)


async def test_index_remove_delegates_to_l1() -> None:
    l1, sqlite = _make_backends()

    store = CompositeMemoryStore(l1=l1, sqlite=sqlite)
    await store.index_remove("sess-A", "e1")

    l1.index_remove.assert_awaited_once_with("sess-A", "e1")


async def test_search_delegates_to_sqlite() -> None:
    l1, sqlite = _make_backends()
    entry = _make_entry()
    sqlite.search.return_value = [entry]

    store = CompositeMemoryStore(l1=l1, sqlite=sqlite)
    result = await store.search("sess-A", "hello", layers=["L2"], limit=5)

    sqlite.search.assert_awaited_once_with("sess-A", "hello", layers=["L2"], limit=5)
    assert result == [entry]


async def test_store_delegates_and_updates_l1() -> None:
    l1, sqlite = _make_backends()
    entry = _make_entry(content="a" * 200)

    store = CompositeMemoryStore(l1=l1, sqlite=sqlite)
    await store.store("sess-A", entry)

    sqlite.store.assert_awaited_once_with("sess-A", entry)

    l1.index_update.assert_awaited_once()
    call_args = l1.index_update.call_args
    assert call_args[0][0] == "sess-A"
    l1_entry: MemoryEntry = call_args[0][1]
    assert l1_entry.id == entry.id
    assert l1_entry.layer == "L1"
    assert len(l1_entry.content) <= 100


async def test_store_short_content_not_truncated() -> None:
    l1, sqlite = _make_backends()
    entry = _make_entry(content="short")

    store = CompositeMemoryStore(l1=l1, sqlite=sqlite)
    await store.store("sess-A", entry)

    l1_entry: MemoryEntry = l1.index_update.call_args[0][1]
    assert l1_entry.content == "short"


async def test_delete_delegates_and_removes_from_l1() -> None:
    l1, sqlite = _make_backends()

    store = CompositeMemoryStore(l1=l1, sqlite=sqlite)
    await store.delete("sess-A", "e1")

    sqlite.delete.assert_awaited_once_with("sess-A", "e1")
    l1.index_remove.assert_awaited_once_with("sess-A", "e1")


async def test_archive_session_delegates_to_sqlite() -> None:
    l1, sqlite = _make_backends()

    store = CompositeMemoryStore(l1=l1, sqlite=sqlite)
    await store.archive_session("sess-A", "sid-1", "summary text")

    sqlite.archive_session.assert_awaited_once_with("sess-A", "sid-1", "summary text")


async def test_search_archives_delegates_to_sqlite() -> None:
    l1, sqlite = _make_backends()
    archive = ArchiveEntry(
        id="a1", session_id="sid-1", summary="sum", created_at=time.time()
    )
    sqlite.search_archives.return_value = [archive]

    store = CompositeMemoryStore(l1=l1, sqlite=sqlite)
    result = await store.search_archives("sess-A", "query", limit=3)

    sqlite.search_archives.assert_awaited_once_with("sess-A", "query", limit=3)
    assert result == [archive]


async def test_close_calls_all_backends() -> None:
    l1, sqlite = _make_backends()

    store = CompositeMemoryStore(l1=l1, sqlite=sqlite)
    await store.close()

    l1.close.assert_awaited_once()
    sqlite.close.assert_awaited_once()


async def test_close_catches_exceptions() -> None:
    l1, sqlite = _make_backends()
    l1.close = AsyncMock(side_effect=RuntimeError("boom"))

    store = CompositeMemoryStore(l1=l1, sqlite=sqlite)
    await store.close()

    sqlite.close.assert_awaited_once()


async def test_composite_satisfies_protocol() -> None:
    l1, sqlite = _make_backends()
    store = CompositeMemoryStore(l1=l1, sqlite=sqlite)
    assert isinstance(store, MemoryStore)
