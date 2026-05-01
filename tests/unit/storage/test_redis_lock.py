from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from pyclaw.storage.lock.redis import LockAcquireError, RedisLockManager


def _mock_client() -> AsyncMock:
    client = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_acquire_succeeds_when_key_free() -> None:
    client = _mock_client()
    client.set = AsyncMock(return_value=True)
    mgr = RedisLockManager(client, key_prefix="test:")
    token = await mgr.acquire("my-lock", ttl_ms=5000)
    assert isinstance(token, str) and len(token) == 32
    client.set.assert_awaited_once_with("test:my-lock", token, nx=True, px=5000)


@pytest.mark.asyncio
async def test_acquire_fails_when_key_held() -> None:
    client = _mock_client()
    client.set = AsyncMock(return_value=None)
    mgr = RedisLockManager(client, key_prefix="test:")
    with pytest.raises(LockAcquireError) as info:
        await mgr.acquire("my-lock")
    assert "my-lock" in str(info.value)


@pytest.mark.asyncio
async def test_release_correct_token_returns_true() -> None:
    client = _mock_client()
    client.eval = AsyncMock(return_value=1)
    mgr = RedisLockManager(client, key_prefix="test:")
    result = await mgr.release("my-lock", "abc123")
    assert result is True
    client.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_release_wrong_token_returns_false() -> None:
    client = _mock_client()
    client.eval = AsyncMock(return_value=0)
    mgr = RedisLockManager(client, key_prefix="test:")
    result = await mgr.release("my-lock", "wrong-token")
    assert result is False


@pytest.mark.asyncio
async def test_renew_extends_ttl_returns_true() -> None:
    client = _mock_client()
    client.eval = AsyncMock(return_value=1)
    mgr = RedisLockManager(client, key_prefix="test:")
    result = await mgr.renew("my-lock", "abc123", ttl_ms=10_000)
    assert result is True


@pytest.mark.asyncio
async def test_renew_wrong_token_returns_false() -> None:
    client = _mock_client()
    client.eval = AsyncMock(return_value=0)
    mgr = RedisLockManager(client, key_prefix="test:")
    result = await mgr.renew("my-lock", "wrong-token")
    assert result is False


def test_key_prefix_applied() -> None:
    mgr = RedisLockManager(AsyncMock(), key_prefix="pyclaw:")
    assert mgr._full_key("session:abc") == "pyclaw:session:abc"
