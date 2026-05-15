from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pyclaw.channels.session_router import SessionRouter
from pyclaw.storage.session.base import InMemorySessionStore


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def router(store: InMemorySessionStore) -> SessionRouter:
    return SessionRouter(store=store)


@pytest.mark.asyncio
async def test_idle_disabled_by_default(router: SessionRouter) -> None:
    sid, _ = await router.resolve_or_create("key1", "ws")
    result = await router.check_idle_reset("key1", sid, idle_minutes=0)
    assert result is False


@pytest.mark.asyncio
async def test_idle_no_reset_within_window(
    store: InMemorySessionStore, router: SessionRouter
) -> None:
    sid, _ = await router.resolve_or_create("key1", "ws")
    await router.update_last_interaction(sid)
    result = await router.check_idle_reset("key1", sid, idle_minutes=60)
    assert result is False


@pytest.mark.asyncio
async def test_idle_reset_triggers_rotation(
    store: InMemorySessionStore, router: SessionRouter
) -> None:
    sid, _ = await router.resolve_or_create("key1", "ws")
    stale_ts = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    tree = await store.load(sid)
    assert tree is not None
    updated = tree.model_copy(
        update={"header": tree.header.model_copy(update={"last_interaction_at": stale_ts})}
    )
    await store.save_header(updated)

    needs_reset = await router.check_idle_reset("key1", sid, idle_minutes=30)
    assert needs_reset is True

    new_sid, _ = await router.rotate("key1", "ws")
    assert new_sid != sid


@pytest.mark.asyncio
async def test_last_interaction_updated_after_agent_run(
    store: InMemorySessionStore, router: SessionRouter
) -> None:
    sid, tree = await router.resolve_or_create("key1", "ws")
    assert tree.header.last_interaction_at is None
    await router.update_last_interaction(sid)
    updated = await store.load(sid)
    assert updated is not None
    assert updated.header.last_interaction_at is not None


@pytest.mark.asyncio
async def test_idle_minutes_zero_never_resets(
    store: InMemorySessionStore, router: SessionRouter
) -> None:
    sid, _ = await router.resolve_or_create("key1", "ws")
    stale_ts = (datetime.now(UTC) - timedelta(days=365)).isoformat()
    tree = await store.load(sid)
    assert tree is not None
    updated = tree.model_copy(
        update={"header": tree.header.model_copy(update={"last_interaction_at": stale_ts})}
    )
    await store.save_header(updated)

    result = await router.check_idle_reset("key1", sid, idle_minutes=0)
    assert result is False
