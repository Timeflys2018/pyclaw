from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pyclaw.gateway.affinity import AffinityRegistry


def _make_redis() -> AsyncMock:
    return AsyncMock()


class TestResolve:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_affinity(self) -> None:
        redis = _make_redis()
        redis.get.return_value = None
        reg = AffinityRegistry(redis, worker_id="w1")
        assert await reg.resolve("sess1") is None

    @pytest.mark.asyncio
    async def test_decodes_bytes_value(self) -> None:
        redis = _make_redis()
        redis.get.return_value = b"w2"
        reg = AffinityRegistry(redis, worker_id="w1")
        assert await reg.resolve("sess1") == "w2"

    @pytest.mark.asyncio
    async def test_returns_string_value(self) -> None:
        redis = _make_redis()
        redis.get.return_value = "w2"
        reg = AffinityRegistry(redis, worker_id="w1")
        assert await reg.resolve("sess1") == "w2"


class TestClaim:
    @pytest.mark.asyncio
    async def test_succeeds_when_no_key(self) -> None:
        redis = _make_redis()
        redis.set.return_value = True
        reg = AffinityRegistry(redis, worker_id="w1", ttl_seconds=300)
        assert await reg.claim("sess1") is True
        redis.set.assert_called_once_with(
            "pyclaw:affinity:sess1", "w1", nx=True, ex=300
        )

    @pytest.mark.asyncio
    async def test_fails_when_key_exists_for_other(self) -> None:
        redis = _make_redis()
        redis.set.return_value = None
        reg = AffinityRegistry(redis, worker_id="w1")
        assert await reg.claim("sess1") is False


class TestRenew:
    @pytest.mark.asyncio
    async def test_extends_ttl(self) -> None:
        redis = _make_redis()
        reg = AffinityRegistry(redis, worker_id="w1", ttl_seconds=300)
        await reg.renew("sess1")
        redis.expire.assert_called_once_with("pyclaw:affinity:sess1", 300)


class TestRelease:
    @pytest.mark.asyncio
    async def test_only_removes_own_key(self) -> None:
        redis = _make_redis()
        redis.eval.return_value = 1
        reg = AffinityRegistry(redis, worker_id="w1")
        result = await reg.release("sess1")
        assert result is True
        args = redis.eval.call_args[0]
        assert args[1] == 1
        assert args[2] == "pyclaw:affinity:sess1"
        assert args[3] == "w1"

    @pytest.mark.asyncio
    async def test_returns_false_when_owned_by_other(self) -> None:
        redis = _make_redis()
        redis.eval.return_value = 0
        reg = AffinityRegistry(redis, worker_id="w1")
        assert await reg.release("sess1") is False


class TestForceClaim:
    @pytest.mark.asyncio
    async def test_overwrites(self) -> None:
        redis = _make_redis()
        reg = AffinityRegistry(redis, worker_id="w1", ttl_seconds=300)
        await reg.force_claim("sess1")
        redis.set.assert_called_once_with(
            "pyclaw:affinity:sess1", "w1", ex=300
        )


class TestIsMine:
    def test_true_for_self(self) -> None:
        reg = AffinityRegistry(_make_redis(), worker_id="w1")
        assert reg.is_mine("w1") is True

    def test_false_for_other(self) -> None:
        reg = AffinityRegistry(_make_redis(), worker_id="w1")
        assert reg.is_mine("w2") is False

    def test_false_for_none(self) -> None:
        reg = AffinityRegistry(_make_redis(), worker_id="w1")
        assert reg.is_mine(None) is False


class TestKeyPrefix:
    @pytest.mark.asyncio
    async def test_custom_prefix(self) -> None:
        redis = _make_redis()
        redis.set.return_value = True
        reg = AffinityRegistry(redis, worker_id="w1", key_prefix="myapp:")
        await reg.claim("sess1")
        redis.set.assert_called_once_with(
            "myapp:affinity:sess1", "w1", nx=True, ex=300
        )
