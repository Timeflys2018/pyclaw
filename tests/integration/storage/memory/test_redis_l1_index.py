from __future__ import annotations

import os
import time

import pytest
import redis.asyncio as aioredis

from pyclaw.storage.memory.base import MemoryEntry
from pyclaw.storage.memory.redis_index import RedisL1Index

TEST_PREFIX = "pyclaw-test-mem:"
pytestmark = pytest.mark.integration


@pytest.fixture
async def redis_client():
    host = os.environ["PYCLAW_TEST_REDIS_HOST"]
    port = int(os.environ.get("PYCLAW_TEST_REDIS_PORT", "6379"))
    password = os.environ.get("PYCLAW_TEST_REDIS_PASSWORD")
    url = f"redis://:{password}@{host}:{port}" if password else f"redis://{host}:{port}"
    client = aioredis.from_url(url, decode_responses=True)
    yield client
    keys = await client.keys(f"{TEST_PREFIX}*")
    if keys:
        await client.delete(*keys)
    await client.aclose()


@pytest.fixture
def index(redis_client: aioredis.Redis) -> RedisL1Index:
    return RedisL1Index(
        redis_client,
        key_prefix=TEST_PREFIX,
        max_entries=5,
        max_chars=200,
        ttl_seconds=60,
    )


def _make_entry(eid: str, content: str, ts: float | None = None) -> MemoryEntry:
    t = ts or time.time()
    return MemoryEntry(
        id=eid, layer="L1", type="env_fact", content=content, created_at=t, updated_at=t
    )


async def test_roundtrip_write_and_read(index: RedisL1Index) -> None:
    entry = _make_entry("e1", "Python 3.12")
    await index.index_update("test:alice", entry)
    result = await index.index_get("test:alice")
    assert len(result) == 1
    assert result[0].content == "Python 3.12"


async def test_empty_key_returns_empty(index: RedisL1Index) -> None:
    result = await index.index_get("test:nonexistent")
    assert result == []


async def test_eviction_by_count(index: RedisL1Index) -> None:
    for i in range(7):
        await index.index_update(
            "test:bob", _make_entry(f"e{i}", f"item{i}", ts=1000.0 + i)
        )
    result = await index.index_get("test:bob")
    assert len(result) == 5
    ids = {e.id for e in result}
    assert "e0" not in ids
    assert "e1" not in ids
    assert "e6" in ids


async def test_eviction_by_chars(index: RedisL1Index) -> None:
    await index.index_update("test:carol", _make_entry("e1", "a" * 100, ts=1.0))
    await index.index_update("test:carol", _make_entry("e2", "b" * 120, ts=2.0))
    result = await index.index_get("test:carol")
    assert len(result) == 1
    assert result[0].id == "e2"


async def test_ttl_is_set(
    index: RedisL1Index, redis_client: aioredis.Redis
) -> None:
    await index.index_update("test:dave", _make_entry("e1", "hello"))
    key = f"{TEST_PREFIX}memory:L1:test:dave"
    ttl = await redis_client.ttl(key)
    assert 0 < ttl <= 60


async def test_remove_entry(index: RedisL1Index) -> None:
    await index.index_update("test:eve", _make_entry("e1", "data"))
    await index.index_remove("test:eve", "e1")
    result = await index.index_get("test:eve")
    assert result == []


async def test_remove_nonexistent(index: RedisL1Index) -> None:
    await index.index_remove("test:frank", "nope")
