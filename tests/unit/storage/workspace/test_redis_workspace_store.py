from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.storage.workspace.redis import RedisWorkspaceStore


def _make_store(data: dict | None = None) -> tuple[RedisWorkspaceStore, MagicMock]:
    stored: dict[str, str] = data or {}
    client = MagicMock()

    async def _get(key: str) -> str | None:
        return stored.get(key)

    async def _set(key: str, value: str) -> None:
        stored[key] = value

    client.get = AsyncMock(side_effect=_get)
    client.set = AsyncMock(side_effect=_set)
    store = RedisWorkspaceStore(client, key_prefix="test:")
    return store, client


@pytest.mark.asyncio
async def test_get_file_returns_none_when_missing() -> None:
    store, _ = _make_store()
    result = await store.get_file("ws-1", "AGENTS.md")
    assert result is None


@pytest.mark.asyncio
async def test_put_then_get_roundtrip() -> None:
    store, _ = _make_store()
    await store.put_file("ws-1", "AGENTS.md", "hello agent")
    result = await store.get_file("ws-1", "AGENTS.md")
    assert result == "hello agent"


@pytest.mark.asyncio
async def test_put_overwrites_existing() -> None:
    store, _ = _make_store()
    await store.put_file("ws-1", "AGENTS.md", "v1")
    await store.put_file("ws-1", "AGENTS.md", "v2")
    result = await store.get_file("ws-1", "AGENTS.md")
    assert result == "v2"


@pytest.mark.asyncio
async def test_key_format() -> None:
    store, client = _make_store()
    await store.get_file("my-workspace", "SOUL.md")
    client.get.assert_called_once_with("test:workspace:my-workspace:SOUL.md")


@pytest.mark.asyncio
async def test_keys_isolated_per_workspace_id() -> None:
    store, _ = _make_store()
    await store.put_file("ws-A", "AGENTS.md", "A content")
    await store.put_file("ws-B", "AGENTS.md", "B content")
    assert await store.get_file("ws-A", "AGENTS.md") == "A content"
    assert await store.get_file("ws-B", "AGENTS.md") == "B content"


@pytest.mark.asyncio
async def test_put_calls_set_without_ex() -> None:
    store, client = _make_store()
    await store.put_file("ws-1", "AGENTS.md", "content")
    call_kwargs = client.set.call_args
    assert "ex" not in (call_kwargs.kwargs if call_kwargs else {})
