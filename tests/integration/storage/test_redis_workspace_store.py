from __future__ import annotations

import os

import pytest
import redis.asyncio as aioredis

from pyclaw.storage.workspace.redis import RedisWorkspaceStore

TEST_PREFIX = "pyclaw-test-ws:"
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
def store(redis_client: aioredis.Redis) -> RedisWorkspaceStore:
    return RedisWorkspaceStore(redis_client, key_prefix=TEST_PREFIX)


@pytest.mark.asyncio
async def test_redis_get_file_returns_none(store: RedisWorkspaceStore) -> None:
    result = await store.get_file("test-ws-1", "AGENTS.md")
    assert result is None


@pytest.mark.asyncio
async def test_redis_put_then_get_roundtrip(store: RedisWorkspaceStore) -> None:
    await store.put_file("test-ws-2", "AGENTS.md", "你是一个有用的助手。")
    result = await store.get_file("test-ws-2", "AGENTS.md")
    assert result == "你是一个有用的助手。"


@pytest.mark.asyncio
async def test_redis_put_file_no_ttl(
    store: RedisWorkspaceStore, redis_client: aioredis.Redis
) -> None:
    await store.put_file("test-ws-3", "AGENTS.md", "content")
    key = store._key("test-ws-3", "AGENTS.md")
    ttl = await redis_client.ttl(key)
    assert ttl == -1


@pytest.mark.asyncio
async def test_redis_key_isolation(store: RedisWorkspaceStore) -> None:
    await store.put_file("test-ws-A", "AGENTS.md", "workspace A")
    await store.put_file("test-ws-B", "AGENTS.md", "workspace B")
    assert await store.get_file("test-ws-A", "AGENTS.md") == "workspace A"
    assert await store.get_file("test-ws-B", "AGENTS.md") == "workspace B"
