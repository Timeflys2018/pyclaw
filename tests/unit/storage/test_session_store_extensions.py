from __future__ import annotations

import asyncio

import pytest

from pyclaw.storage.session.base import InMemorySessionStore


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.mark.asyncio
async def test_get_current_session_id_none_initially(store: InMemorySessionStore) -> None:
    result = await store.get_current_session_id("feishu:cli_x:ou_a")
    assert result is None


@pytest.mark.asyncio
async def test_set_then_get_current_session_id(store: InMemorySessionStore) -> None:
    await store.set_current_session_id("feishu:cli_x:ou_a", "feishu:cli_x:ou_a:s:aabbccdd")
    result = await store.get_current_session_id("feishu:cli_x:ou_a")
    assert result == "feishu:cli_x:ou_a:s:aabbccdd"


@pytest.mark.asyncio
async def test_create_new_session_returns_tree_with_session_key(
    store: InMemorySessionStore,
) -> None:
    tree = await store.create_new_session("feishu:cli_x:ou_a", "feishu_cli_x_ou_a", "default")
    assert tree.header.session_key == "feishu:cli_x:ou_a"
    assert tree.header.id.startswith("feishu:cli_x:ou_a:s:")
    assert tree.header.workspace_id == "feishu_cli_x_ou_a"
    assert tree.header.agent_id == "default"


@pytest.mark.asyncio
async def test_create_new_session_registers_as_current(store: InMemorySessionStore) -> None:
    tree = await store.create_new_session("feishu:cli_x:ou_a", "ws", "default")
    current = await store.get_current_session_id("feishu:cli_x:ou_a")
    assert current == tree.header.id


@pytest.mark.asyncio
async def test_create_new_session_sets_parent_session_id(store: InMemorySessionStore) -> None:
    tree = await store.create_new_session(
        "feishu:cli_x:ou_a", "ws", "default", parent_session_id="feishu:cli_x:ou_a:s:old12345"
    )
    assert tree.header.parent_session == "feishu:cli_x:ou_a:s:old12345"


@pytest.mark.asyncio
async def test_create_new_session_no_parent_session_id(store: InMemorySessionStore) -> None:
    tree = await store.create_new_session("feishu:cli_x:ou_a", "ws", "default")
    assert tree.header.parent_session is None


@pytest.mark.asyncio
async def test_create_two_sessions_history_has_two_entries(store: InMemorySessionStore) -> None:
    t1 = await store.create_new_session("key1", "ws", "default")
    await asyncio.sleep(0.01)
    t2 = await store.create_new_session("key1", "ws", "default")
    history = await store.list_session_history("key1")
    ids = [s.session_id for s in history]
    assert t1.header.id in ids
    assert t2.header.id in ids
    assert len(ids) == 2


@pytest.mark.asyncio
async def test_list_session_history_returns_newest_first(store: InMemorySessionStore) -> None:
    t1 = await store.create_new_session("key2", "ws", "default")
    await asyncio.sleep(0.01)
    t2 = await store.create_new_session("key2", "ws", "default")
    history = await store.list_session_history("key2")
    assert history[0].session_id == t2.header.id
    assert history[1].session_id == t1.header.id


@pytest.mark.asyncio
async def test_list_session_history_empty_for_unknown_key(store: InMemorySessionStore) -> None:
    history = await store.list_session_history("nonexistent:key")
    assert history == []


@pytest.mark.asyncio
async def test_list_session_history_respects_limit(store: InMemorySessionStore) -> None:
    for _ in range(5):
        await store.create_new_session("key3", "ws", "default")
        await asyncio.sleep(0.005)
    history = await store.list_session_history("key3", limit=3)
    assert len(history) == 3


@pytest.mark.asyncio
async def test_set_current_session_id_updates_pointer(store: InMemorySessionStore) -> None:
    await store.set_current_session_id("key4", "sess-1")
    await store.set_current_session_id("key4", "sess-2")
    current = await store.get_current_session_id("key4")
    assert current == "sess-2"


@pytest.mark.asyncio
async def test_delete_session_removes_tree_and_history(store: InMemorySessionStore) -> None:
    tree = await store.create_new_session("dk", "ws", "default")
    deleted = await store.delete_session(tree.header.id)
    assert deleted is True
    assert await store.load(tree.header.id) is None
    history = await store.list_session_history("dk")
    assert all(h.session_id != tree.header.id for h in history)


@pytest.mark.asyncio
async def test_delete_session_unsets_current_when_active(store: InMemorySessionStore) -> None:
    tree = await store.create_new_session("dk2", "ws", "default")
    await store.delete_session(tree.header.id)
    current = await store.get_current_session_id("dk2")
    assert current is None


@pytest.mark.asyncio
async def test_delete_session_returns_false_for_unknown(store: InMemorySessionStore) -> None:
    deleted = await store.delete_session("does-not-exist")
    assert deleted is False


@pytest.mark.asyncio
async def test_delete_session_keeps_other_sessions(store: InMemorySessionStore) -> None:
    t1 = await store.create_new_session("dk3", "ws", "default")
    t2 = await store.create_new_session("dk3", "ws", "default")
    await store.delete_session(t1.header.id)
    history = await store.list_session_history("dk3")
    assert any(h.session_id == t2.header.id for h in history)
    assert all(h.session_id != t1.header.id for h in history)
