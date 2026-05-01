from __future__ import annotations

import asyncio
import os

import pytest
import redis.asyncio as aioredis

from pyclaw.models import SessionHeader, SessionTree
from pyclaw.storage.lock.redis import RedisLockManager
from pyclaw.storage.session.redis import RedisSessionStore

TEST_PREFIX = "pyclaw-test:"
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
def lock_manager(redis_client: aioredis.Redis) -> RedisLockManager:
    return RedisLockManager(redis_client, key_prefix=TEST_PREFIX)


@pytest.fixture
def store(redis_client: aioredis.Redis, lock_manager: RedisLockManager) -> RedisSessionStore:
    return RedisSessionStore(redis_client, lock_manager, ttl_seconds=60, key_prefix=TEST_PREFIX)


@pytest.mark.asyncio
async def test_redis_get_current_session_id_none_initially(store: RedisSessionStore) -> None:
    result = await store.get_current_session_id("feishu:test_app:test_user")
    assert result is None


@pytest.mark.asyncio
async def test_redis_set_then_get_current_session_id(store: RedisSessionStore) -> None:
    key = "feishu:test_app:test_user_set"
    sid = f"{key}:s:aabbccdd"
    await store.set_current_session_id(key, sid)
    result = await store.get_current_session_id(key)
    assert result == sid


@pytest.mark.asyncio
async def test_redis_create_new_session_returns_tree(store: RedisSessionStore) -> None:
    key = "feishu:test_app:test_user_create"
    tree = await store.create_new_session(key, "ws", "default")
    assert tree.header.session_key == key
    assert tree.header.id.startswith(f"{key}:s:")
    current = await store.get_current_session_id(key)
    assert current == tree.header.id


@pytest.mark.asyncio
async def test_redis_create_new_session_skey_keys_have_no_ttl(
    store: RedisSessionStore, redis_client: aioredis.Redis
) -> None:
    key = "feishu:test_app:test_user_ttl"
    await store.create_new_session(key, "ws", "default")
    current_key = store._skey_current_key(key)
    history_key = store._skey_history_key(key)
    ttl_current = await redis_client.ttl(current_key)
    ttl_history = await redis_client.ttl(history_key)
    assert ttl_current == -1
    assert ttl_history == -1


@pytest.mark.asyncio
async def test_redis_list_session_history_returns_sorted(store: RedisSessionStore) -> None:
    key = "feishu:test_app:test_user_hist"
    t1 = await store.create_new_session(key, "ws", "default")
    await asyncio.sleep(0.01)
    t2 = await store.create_new_session(key, "ws", "default")
    history = await store.list_session_history(key)
    ids = [s.session_id for s in history]
    assert t2.header.id == ids[0]
    assert t1.header.id == ids[1]


@pytest.mark.asyncio
async def test_redis_rotate_session_updates_current_and_history(
    store: RedisSessionStore,
) -> None:
    key = "feishu:test_app:test_user_rotate"
    t1 = await store.create_new_session(key, "ws", "default")
    await asyncio.sleep(0.01)
    t2 = await store.create_new_session(key, "ws", "default", parent_session_id=t1.header.id)
    current = await store.get_current_session_id(key)
    assert current == t2.header.id
    history = await store.list_session_history(key)
    assert len(history) == 2
    assert t2.header.parent_session == t1.header.id


@pytest.mark.asyncio
async def test_redis_lazy_migration_old_format_session(
    store: RedisSessionStore,
) -> None:
    old_key = "feishu:test_app:test_user_legacy"
    old_header = SessionHeader(id=old_key, workspace_id="ws", agent_id="default")
    old_tree = SessionTree(header=old_header)
    await store.save_header(old_tree)

    current = await store.get_current_session_id(old_key)
    assert current is None

    loaded = await store.load(old_key)
    assert loaded is not None
    assert loaded.header.id == old_key

    await store.set_current_session_id(old_key, old_key)
    current_after = await store.get_current_session_id(old_key)
    assert current_after == old_key
