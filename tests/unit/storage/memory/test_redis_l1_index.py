from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from pyclaw.storage.memory.base import MemoryEntry
from pyclaw.storage.memory.redis_index import RedisL1Index


def _make_entry(
    entry_id: str = "e1",
    updated_at: float | None = None,
    content: str = "hello",
) -> MemoryEntry:
    now = updated_at or time.time()
    return MemoryEntry(
        id=entry_id,
        layer="L1",
        type="insight",
        content=content,
        source_session_id="sess-1",
        created_at=now,
        updated_at=now,
    )


def _make_index(
    stored: dict[str, dict[str, str]] | None = None,
    *,
    max_entries: int = 30,
    max_chars: int = 3000,
    ttl_seconds: int = 2_592_000,
) -> tuple[RedisL1Index, MagicMock]:
    data: dict[str, dict[str, str]] = stored if stored is not None else {}
    client = MagicMock()

    async def _hgetall(key: str) -> dict[str, str]:
        return dict(data.get(key, {}))

    async def _hset(key: str, field: str, value: str) -> None:
        if key not in data:
            data[key] = {}
        data[key][field] = value

    async def _hdel(key: str, *fields: str) -> None:
        bucket = data.get(key, {})
        for f in fields:
            bucket.pop(f, None)

    async def _expire(key: str, seconds: int) -> None:
        pass

    client.hgetall = AsyncMock(side_effect=_hgetall)
    client.hset = AsyncMock(side_effect=_hset)
    client.hdel = AsyncMock(side_effect=_hdel)
    client.expire = AsyncMock(side_effect=_expire)

    index = RedisL1Index(
        client,
        key_prefix="test:",
        max_entries=max_entries,
        max_chars=max_chars,
        ttl_seconds=ttl_seconds,
    )
    return index, client


async def test_index_get_returns_sorted_entries() -> None:
    e_old = _make_entry("e-old", updated_at=1000.0)
    e_new = _make_entry("e-new", updated_at=2000.0)
    stored = {
        "test:memory:L1:sess-A": {
            "e-old": e_old.model_dump_json(),
            "e-new": e_new.model_dump_json(),
        },
    }
    index, _ = _make_index(stored)
    result = await index.index_get("sess-A")
    assert len(result) == 2
    assert result[0].id == "e-new"
    assert result[1].id == "e-old"


async def test_index_get_empty_key_returns_empty_list() -> None:
    index, _ = _make_index()
    result = await index.index_get("no-such-key")
    assert result == []


async def test_index_update_writes_and_refreshes_ttl() -> None:
    index, client = _make_index()
    entry = _make_entry("e1")
    await index.index_update("sess-A", entry)
    client.hset.assert_called()
    client.expire.assert_called_with("test:memory:L1:sess-A", 2_592_000)


async def test_index_update_upsert_existing_entry() -> None:
    e1 = _make_entry("e1", updated_at=1000.0, content="old")
    stored = {
        "test:memory:L1:sess-A": {"e1": e1.model_dump_json()},
    }
    index, _ = _make_index(stored)
    updated = _make_entry("e1", updated_at=2000.0, content="new")
    await index.index_update("sess-A", updated)
    result = await index.index_get("sess-A")
    assert len(result) == 1
    assert result[0].content == "new"
    assert result[0].updated_at == 2000.0


async def test_lru_eviction_by_entry_count() -> None:
    stored_entries: dict[str, str] = {}
    for i in range(3):
        e = _make_entry(f"e{i}", updated_at=1000.0 + i)
        stored_entries[f"e{i}"] = e.model_dump_json()
    stored = {"test:memory:L1:sess-A": stored_entries}
    index, _ = _make_index(stored, max_entries=3)

    new_entry = _make_entry("e-new", updated_at=5000.0)
    await index.index_update("sess-A", new_entry)

    result = await index.index_get("sess-A")
    assert len(result) == 3
    ids = [r.id for r in result]
    assert "e0" not in ids
    assert "e-new" in ids


async def test_lru_eviction_by_char_limit() -> None:
    e1 = _make_entry("e1", updated_at=1000.0, content="a" * 500)
    e2 = _make_entry("e2", updated_at=2000.0, content="b" * 500)
    stored = {
        "test:memory:L1:sess-A": {
            "e1": e1.model_dump_json(),
            "e2": e2.model_dump_json(),
        },
    }
    index, _ = _make_index(stored, max_chars=1200)

    e3 = _make_entry("e3", updated_at=3000.0, content="c" * 500)
    await index.index_update("sess-A", e3)

    result = await index.index_get("sess-A")
    total_chars = sum(len(r.content) for r in result)
    assert total_chars <= 1200
    ids = [r.id for r in result]
    assert "e1" not in ids
    assert "e3" in ids


async def test_index_remove_existing_entry() -> None:
    e1 = _make_entry("e1", updated_at=1000.0)
    stored = {"test:memory:L1:sess-A": {"e1": e1.model_dump_json()}}
    index, client = _make_index(stored)
    await index.index_remove("sess-A", "e1")
    client.hdel.assert_called_with("test:memory:L1:sess-A", "e1")
    result = await index.index_get("sess-A")
    assert result == []


async def test_index_remove_nonexistent_entry_no_error() -> None:
    index, client = _make_index()
    await index.index_remove("sess-A", "no-such-id")
    client.hdel.assert_called_with("test:memory:L1:sess-A", "no-such-id")
