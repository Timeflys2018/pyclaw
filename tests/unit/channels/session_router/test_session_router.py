from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pyclaw.channels.session_router import SessionRouter
from pyclaw.models.session import SessionHeader, SessionTree, now_iso
from pyclaw.storage.session.base import InMemorySessionStore


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def router(store: InMemorySessionStore) -> SessionRouter:
    return SessionRouter(store=store)


@pytest.mark.asyncio
async def test_resolve_creates_session_on_first_use(router: SessionRouter) -> None:
    session_id, tree = await router.resolve_or_create("feishu:cli_x:ou_a", "ws")
    assert session_id.startswith("feishu:cli_x:ou_a:s:")
    assert tree.header.session_key == "feishu:cli_x:ou_a"


@pytest.mark.asyncio
async def test_resolve_returns_existing_session(router: SessionRouter) -> None:
    sid1, _ = await router.resolve_or_create("feishu:cli_x:ou_a", "ws")
    sid2, _ = await router.resolve_or_create("feishu:cli_x:ou_a", "ws")
    assert sid1 == sid2


@pytest.mark.asyncio
async def test_resolve_lazy_migration_old_session(
    store: InMemorySessionStore, router: SessionRouter
) -> None:
    old_key = "feishu:cli_x:ou_a"
    old_header = SessionHeader(id=old_key, workspace_id="ws", agent_id="default")
    old_tree = SessionTree(header=old_header)
    await store.save_header(old_tree)

    session_id, tree = await router.resolve_or_create(old_key, "ws")
    assert session_id == old_key
    assert tree.header.id == old_key
    current = await store.get_current_session_id(old_key)
    assert current == old_key


@pytest.mark.asyncio
async def test_rotate_creates_new_session_id(router: SessionRouter) -> None:
    sid1, _ = await router.resolve_or_create("feishu:cli_x:ou_a", "ws")
    sid2, _ = await router.rotate("feishu:cli_x:ou_a", "ws")
    assert sid1 != sid2
    assert sid2.startswith("feishu:cli_x:ou_a:s:")


@pytest.mark.asyncio
async def test_rotate_sets_parent_session(
    store: InMemorySessionStore, router: SessionRouter
) -> None:
    sid1, _ = await router.resolve_or_create("key1", "ws")
    sid2, tree2 = await router.rotate("key1", "ws")
    assert tree2.header.parent_session == sid1


@pytest.mark.asyncio
async def test_rotate_archives_old_session(
    store: InMemorySessionStore, router: SessionRouter
) -> None:
    sid1, _ = await router.resolve_or_create("key1", "ws")
    await router.rotate("key1", "ws")
    old_tree = await store.load(sid1)
    assert old_tree is not None


@pytest.mark.asyncio
async def test_check_idle_reset_false_when_disabled(
    store: InMemorySessionStore, router: SessionRouter
) -> None:
    sid, _ = await router.resolve_or_create("key1", "ws")
    result = await router.check_idle_reset("key1", sid, idle_minutes=0)
    assert result is False


@pytest.mark.asyncio
async def test_check_idle_reset_false_within_window(
    store: InMemorySessionStore, router: SessionRouter
) -> None:
    sid, _ = await router.resolve_or_create("key1", "ws")
    await router.update_last_interaction(sid)
    result = await router.check_idle_reset("key1", sid, idle_minutes=30)
    assert result is False


@pytest.mark.asyncio
async def test_check_idle_reset_true_when_exceeded(
    store: InMemorySessionStore, router: SessionRouter
) -> None:
    sid, _ = await router.resolve_or_create("key1", "ws")
    stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
    tree = await store.load(sid)
    assert tree is not None
    updated_header = tree.header.model_copy(update={"last_interaction_at": stale_ts})
    updated_tree = tree.model_copy(update={"header": updated_header})
    await store.save_header(updated_tree)
    result = await router.check_idle_reset("key1", sid, idle_minutes=30)
    assert result is True


@pytest.mark.asyncio
async def test_update_last_interaction_sets_timestamp(
    store: InMemorySessionStore, router: SessionRouter
) -> None:
    sid, tree = await router.resolve_or_create("key1", "ws")
    assert tree.header.last_interaction_at is None
    await router.update_last_interaction(sid)
    updated = await store.load(sid)
    assert updated is not None
    assert updated.header.last_interaction_at is not None
