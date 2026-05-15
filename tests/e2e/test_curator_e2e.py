"""E2E tests for Curator Phase 1 — real Redis + real SQLite, shortened intervals."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path

import pytest

from pyclaw.core.curator import (
    CURATOR_CYCLE_LOCK_KEY,
    CURATOR_LAST_RUN_KEY,
    CURATOR_LOCK_KEY,
    create_curator_loop,
)
from pyclaw.infra.settings import CuratorSettings
from pyclaw.infra.task_manager import TaskManager
from pyclaw.storage.lock.redis import RedisLockManager
from pyclaw.storage.memory.base import MemoryEntry
from pyclaw.storage.memory.composite import CompositeMemoryStore
from pyclaw.storage.memory.redis_index import RedisL1Index
from pyclaw.storage.memory.sqlite import SqliteMemoryBackend

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not os.environ.get("PYCLAW_TEST_REDIS_HOST"),
    reason="PYCLAW_TEST_REDIS_HOST not set — skipping curator E2E",
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

KEY_PREFIX = "pyclaw_test_curator:"
SESSION_KEY = "test_curator_session"


@pytest.fixture
async def redis_client():
    import redis.asyncio as aioredis

    host = os.environ.get("PYCLAW_TEST_REDIS_HOST", "localhost")
    port = int(os.environ.get("PYCLAW_TEST_REDIS_PORT", "6379"))
    password = os.environ.get("PYCLAW_TEST_REDIS_PASSWORD") or None
    client = aioredis.Redis(host=host, port=port, password=password, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip("Redis not reachable")

    yield client

    await client.delete(
        CURATOR_LOCK_KEY,
        CURATOR_LAST_RUN_KEY,
        f"pyclaw:{CURATOR_CYCLE_LOCK_KEY}",
    )
    # Cleanup L1 keys with our test prefix
    keys = await client.keys(f"{KEY_PREFIX}*")
    if keys:
        await client.delete(*keys)
    await client.aclose()


@pytest.fixture
def l1_index(redis_client):
    return RedisL1Index(
        redis_client,
        key_prefix=KEY_PREFIX,
        max_entries=30,
        max_chars=3000,
        ttl_seconds=300,
    )


@pytest.fixture
def sqlite_backend(tmp_path: Path):
    return SqliteMemoryBackend(base_dir=tmp_path, embedding=None)


@pytest.fixture
def memory_store(l1_index, sqlite_backend):
    return CompositeMemoryStore(l1=l1_index, sqlite=sqlite_backend)


@pytest.fixture
def short_settings():
    return CuratorSettings(
        enabled=True,
        check_interval_seconds=1,
        interval_seconds=2,
        stale_after_days=30,
        archive_after_days=90,
    )


@pytest.fixture
def lock_manager(redis_client):
    return RedisLockManager(redis_client, key_prefix="pyclaw:")


@pytest.fixture
async def task_manager():
    tm = TaskManager()
    yield tm
    await tm.shutdown(grace_s=1.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_procedure_entry(
    entry_id: str | None = None,
    *,
    content: str = "test procedure content",
    created_days_ago: int = 0,
) -> MemoryEntry:
    """Create a procedure MemoryEntry with configurable age."""
    now = time.time()
    created_at = now - created_days_ago * 86400
    return MemoryEntry(
        id=entry_id or str(uuid.uuid4()),
        layer="L3",
        type="procedure",
        content=content,
        source_session_id="test-session",
        created_at=created_at,
        updated_at=created_at,
        last_used_at=None,
        use_count=0,
        status="active",
    )


# ---------------------------------------------------------------------------
# E2E 1: Curator loop archives expired entries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_curator_loop_archives_expired_entries(
    tmp_path: Path, redis_client, l1_index, short_settings, lock_manager, task_manager
):
    """Full E2E: start curator loop with short interval → verify archive happens."""
    # 1. Store a procedure created 100 days ago directly in SQLite
    entry = _make_procedure_entry(created_days_ago=100)
    sqlite = SqliteMemoryBackend(base_dir=tmp_path, embedding=None)
    store = CompositeMemoryStore(l1=l1_index, sqlite=sqlite)

    await store.store(SESSION_KEY, entry)

    # 2. Verify it's in L1 before curator runs
    l1_entries = await l1_index.index_get(SESSION_KEY)
    assert any(e.id == entry.id for e in l1_entries), "Entry should be in L1 index"

    # 3. Force last_run to be old enough to trigger scan immediately
    await redis_client.set(CURATOR_LAST_RUN_KEY, str(time.time() - 100))

    # 4. Start curator loop with short intervals
    task = asyncio.create_task(
        create_curator_loop(
            settings=short_settings,
            memory_base_dir=tmp_path,
            redis_client=redis_client,
            l1_index=l1_index,
            lock_manager=lock_manager,
            task_manager=task_manager,
        )
    )

    # 5. Wait for at least one cycle
    await asyncio.sleep(4)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # 6. Verify: procedure is archived in DB
    import apsw

    from pyclaw.storage.memory.jieba_tokenizer import register_jieba_tokenizer

    db_name = SESSION_KEY.replace(":", "_") + ".db"
    db_path = tmp_path / db_name
    conn = apsw.Connection(str(db_path))
    register_jieba_tokenizer(conn)
    row = list(
        conn.execute("SELECT status, archive_reason FROM procedures WHERE id=?", (entry.id,))
    )
    conn.close()

    assert row, "Procedure row not found in DB"
    assert row[0][0] == "archived", f"Expected status='archived', got {row[0][0]!r}"
    assert row[0][1] == "curator:90d_unused", f"Unexpected archive_reason: {row[0][1]!r}"

    # 7. Verify: L1 Redis hash no longer contains the entry
    l1_entries_after = await l1_index.index_get(SESSION_KEY)
    assert not any(e.id == entry.id for e in l1_entries_after), "Entry should be evicted from L1"

    await sqlite.close()


# ---------------------------------------------------------------------------
# E2E 2: ForgetTool through CompositeMemoryStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forget_via_composite_store(tmp_path: Path, redis_client, l1_index, memory_store):
    """Full E2E: store → archive_entry → search returns empty → L1 evicted."""
    entry = _make_procedure_entry(content="unique search target for forget test")

    # 1. Store the procedure
    await memory_store.store(SESSION_KEY, entry)

    # 2. Verify it's in L1
    l1_entries = await l1_index.index_get(SESSION_KEY)
    assert any(e.id == entry.id for e in l1_entries), "Entry should be in L1"

    # 3. Call archive_entry
    reason = "user_requested:forget_test"
    changed = await memory_store.archive_entry(SESSION_KEY, entry.id, reason=reason)
    assert changed is True, "archive_entry should return True for active entry"

    # 4. Verify: search returns empty for this entry
    results = await memory_store.search(SESSION_KEY, "unique search target", layers=["L3"])
    matching = [r for r in results if r.id == entry.id]
    assert not matching, "Archived entry should not appear in search results"

    # 5. Verify: L1 no longer has it
    l1_after = await l1_index.index_get(SESSION_KEY)
    assert not any(e.id == entry.id for e in l1_after), "Entry should be evicted from L1"

    # 6. Verify: raw SQL shows status='archived' and archive_reason matches
    import apsw

    from pyclaw.storage.memory.jieba_tokenizer import register_jieba_tokenizer

    db_name = SESSION_KEY.replace(":", "_") + ".db"
    db_path = tmp_path / db_name
    conn = apsw.Connection(str(db_path))
    register_jieba_tokenizer(conn)
    row = list(
        conn.execute("SELECT status, archive_reason FROM procedures WHERE id=?", (entry.id,))
    )
    conn.close()

    assert row, "Procedure row not found"
    assert row[0][0] == "archived"
    assert row[0][1] == reason


# ---------------------------------------------------------------------------
# E2E 3: Curator preserves fresh entries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_curator_preserves_fresh_entries(
    tmp_path: Path, redis_client, l1_index, short_settings, lock_manager, task_manager
):
    """Fresh procedures (created recently) should NOT be archived."""
    # 1. Store a procedure created just now
    entry = _make_procedure_entry(created_days_ago=0, content="fresh procedure")
    sqlite = SqliteMemoryBackend(base_dir=tmp_path, embedding=None)
    store = CompositeMemoryStore(l1=l1_index, sqlite=sqlite)

    await store.store(SESSION_KEY, entry)

    # 2. Force last_run to be old enough to trigger scan
    await redis_client.set(CURATOR_LAST_RUN_KEY, str(time.time() - 100))

    # 3. Start curator loop
    task = asyncio.create_task(
        create_curator_loop(
            settings=short_settings,
            memory_base_dir=tmp_path,
            redis_client=redis_client,
            l1_index=l1_index,
            lock_manager=lock_manager,
            task_manager=task_manager,
        )
    )

    await asyncio.sleep(4)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # 4. Verify: procedure still active
    import apsw

    from pyclaw.storage.memory.jieba_tokenizer import register_jieba_tokenizer

    db_name = SESSION_KEY.replace(":", "_") + ".db"
    db_path = tmp_path / db_name
    conn = apsw.Connection(str(db_path))
    register_jieba_tokenizer(conn)
    row = list(conn.execute("SELECT status FROM procedures WHERE id=?", (entry.id,)))
    conn.close()

    assert row, "Procedure row not found"
    assert row[0][0] == "active", f"Fresh entry should remain active, got {row[0][0]!r}"

    # 5. Verify: still in L1
    l1_entries = await l1_index.index_get(SESSION_KEY)
    assert any(e.id == entry.id for e in l1_entries), "Fresh entry should stay in L1"

    await sqlite.close()


# ---------------------------------------------------------------------------
# E2E 4: Distributed lock prevents double execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_curator_lock_prevents_concurrent_execution(
    tmp_path: Path, redis_client, l1_index, short_settings, lock_manager, task_manager
):
    """Only one curator instance should execute when lock is held."""
    entry = _make_procedure_entry(created_days_ago=100, content="lock test procedure")
    sqlite = SqliteMemoryBackend(base_dir=tmp_path, embedding=None)
    store = CompositeMemoryStore(l1=l1_index, sqlite=sqlite)

    await store.store(SESSION_KEY, entry)

    await redis_client.set(CURATOR_LAST_RUN_KEY, str(time.time() - 100))

    holder_token = await lock_manager.acquire(CURATOR_CYCLE_LOCK_KEY, ttl_ms=10_000)
    assert holder_token, "Should be able to acquire the cycle lock initially"

    task = asyncio.create_task(
        create_curator_loop(
            settings=short_settings,
            memory_base_dir=tmp_path,
            redis_client=redis_client,
            l1_index=l1_index,
            lock_manager=lock_manager,
            task_manager=task_manager,
        )
    )

    await asyncio.sleep(3)

    import apsw

    from pyclaw.storage.memory.jieba_tokenizer import register_jieba_tokenizer

    db_name = SESSION_KEY.replace(":", "_") + ".db"
    db_path = tmp_path / db_name
    conn = apsw.Connection(str(db_path))
    register_jieba_tokenizer(conn)
    row = list(conn.execute("SELECT status FROM procedures WHERE id=?", (entry.id,)))
    assert row[0][0] == "active", "Entry should still be active while lock is held"

    await lock_manager.release(CURATOR_CYCLE_LOCK_KEY, holder_token)
    await redis_client.set(CURATOR_LAST_RUN_KEY, str(time.time() - 100))

    # 8. Wait for next check cycle
    await asyncio.sleep(3)

    # 9. Verify: archiving occurred after lock released
    row = list(
        conn.execute("SELECT status, archive_reason FROM procedures WHERE id=?", (entry.id,))
    )
    conn.close()

    assert row[0][0] == "archived", f"Expected archived after lock release, got {row[0][0]!r}"
    assert row[0][1] == "curator:90d_unused"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    await sqlite.close()
