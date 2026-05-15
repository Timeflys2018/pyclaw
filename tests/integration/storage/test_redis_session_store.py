from __future__ import annotations

import asyncio
import os

import pytest
import redis.asyncio as aioredis

from pyclaw.models import SessionHeader, SessionTree, generate_entry_id
from pyclaw.models.session import MessageEntry
from pyclaw.storage.lock.redis import RedisLockManager
from pyclaw.storage.session.redis import RedisSessionStore, SessionLockError

TEST_PREFIX = "pyclaw-test:"


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


def _make_tree(session_id: str) -> SessionTree:
    header = SessionHeader(id=session_id, workspace_id="default", agent_id="main")
    return SessionTree(header=header)


def _msg(tree: SessionTree, role: str, content: str) -> MessageEntry:
    return MessageEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        role=role,
        content=content,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_save_header_and_load_roundtrip(store: RedisSessionStore) -> None:
    tree = _make_tree("s1")
    await store.save_header(tree)
    loaded = await store.load("s1")
    assert loaded is not None
    assert loaded.header.id == "s1"
    assert loaded.header.workspace_id == "default"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_load_returns_none_for_unknown_session(store: RedisSessionStore) -> None:
    result = await store.load("does-not-exist")
    assert result is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_append_entry_order_preserved(store: RedisSessionStore) -> None:
    tree = _make_tree("s2")
    await store.save_header(tree)
    entries = [_msg(tree, "user" if i % 2 == 0 else "assistant", f"msg {i}") for i in range(5)]
    for e in entries:
        tree.append(e)
        await store.append_entry("s2", e, leaf_id=e.id)
    loaded = await store.load("s2")
    assert loaded is not None
    assert loaded.order == [e.id for e in entries]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_leaf_id_tracking(store: RedisSessionStore) -> None:
    tree = _make_tree("s3")
    await store.save_header(tree)
    for i in range(3):
        e = _msg(tree, "user", f"hi {i}")
        tree.append(e)
        await store.append_entry("s3", e, leaf_id=e.id)
    loaded = await store.load("s3")
    assert loaded is not None
    assert loaded.leaf_id == tree.leaf_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ttl_present_after_write(
    store: RedisSessionStore, redis_client: aioredis.Redis
) -> None:
    tree = _make_tree("s4")
    await store.save_header(tree)
    actual_key = store._hdr_key("s4")
    ttl = await redis_client.ttl(actual_key)
    assert ttl > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_appends_serialized(store: RedisSessionStore) -> None:
    tree = _make_tree("s5")
    await store.save_header(tree)
    entries = [_msg(tree, "user", f"concurrent {i}") for i in range(4)]

    async def _append_with_retry(entry: MessageEntry, retries: int = 10) -> None:
        from pyclaw.storage.session.redis import SessionLockError

        for attempt in range(retries):
            try:
                await store.append_entry("s5", entry, leaf_id=entry.id)
                return
            except SessionLockError:
                await asyncio.sleep(0.05 * (attempt + 1))
        raise RuntimeError(f"failed to append {entry.id} after {retries} retries")

    await asyncio.gather(*[_append_with_retry(e) for e in entries])
    loaded = await store.load("s5")
    assert loaded is not None
    assert len(loaded.entries) == 4


@pytest.mark.integration
@pytest.mark.asyncio
async def test_data_survives_new_client(
    redis_client: aioredis.Redis, lock_manager: RedisLockManager
) -> None:
    store_a = RedisSessionStore(redis_client, lock_manager, ttl_seconds=60, key_prefix=TEST_PREFIX)
    tree = _make_tree("s6")
    await store_a.save_header(tree)
    e = _msg(tree, "user", "persist me")
    tree.append(e)
    await store_a.append_entry("s6", e, leaf_id=e.id)

    host = os.environ["PYCLAW_TEST_REDIS_HOST"]
    port = int(os.environ.get("PYCLAW_TEST_REDIS_PORT", "6379"))
    password = os.environ.get("PYCLAW_TEST_REDIS_PASSWORD")
    url = f"redis://:{password}@{host}:{port}" if password else f"redis://{host}:{port}"
    client_b = aioredis.from_url(url, decode_responses=True)
    lock_b = RedisLockManager(client_b, key_prefix=TEST_PREFIX)
    store_b = RedisSessionStore(client_b, lock_b, ttl_seconds=60, key_prefix=TEST_PREFIX)

    loaded = await store_b.load("s6")
    await client_b.aclose()

    assert loaded is not None
    assert e.id in loaded.entries
    assert loaded.entries[e.id].content == "persist me"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_lock_error_on_held_lock(
    store: RedisSessionStore, lock_manager: RedisLockManager
) -> None:
    tree = _make_tree("s7")
    await store.save_header(tree)
    lock_key = store._lock_key("s7")
    token = await lock_manager.acquire(lock_key, ttl_ms=5000)
    try:
        e = _msg(tree, "user", "should fail")
        with pytest.raises(SessionLockError):
            await store.append_entry("s7", e, leaf_id=e.id)
    finally:
        await lock_manager.release(lock_key, token)
