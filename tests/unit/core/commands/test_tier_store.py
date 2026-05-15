from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pyclaw.core.commands.tier_store import (
    get_session_tier,
    parse_tier_arg,
    set_session_tier,
)


class TestParseTierArg:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("read-only", "read-only"),
            ("READ-ONLY", "read-only"),
            ("readonly", "read-only"),
            ("ro", "read-only"),
            ("r", "read-only"),
            ("approval", "approval"),
            ("ap", "approval"),
            ("a", "approval"),
            ("yolo", "yolo"),
            ("YOLO", "yolo"),
            ("y", "yolo"),
            ("  yolo  ", "yolo"),
        ],
    )
    def test_valid_inputs(self, raw: str, expected: str) -> None:
        assert parse_tier_arg(raw) == expected

    @pytest.mark.parametrize("raw", ["", "bogus", "yolol", "approve"])
    def test_invalid_inputs_return_none(self, raw: str) -> None:
        assert parse_tier_arg(raw) is None


class TestGetSessionTier:
    @pytest.mark.asyncio
    async def test_returns_none_when_redis_none(self) -> None:
        assert await get_session_tier(None, "feishu:app:ou_a") is None

    @pytest.mark.asyncio
    async def test_returns_none_when_session_key_empty(self) -> None:
        redis = AsyncMock()
        assert await get_session_tier(redis, "") is None
        redis.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_when_key_missing(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        assert await get_session_tier(redis, "feishu:app:ou_a") is None
        redis.get.assert_awaited_once_with("pyclaw:feishu:tier:feishu:app:ou_a")

    @pytest.mark.asyncio
    async def test_decodes_bytes(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=b"yolo")
        assert await get_session_tier(redis, "feishu:app:ou_a") == "yolo"

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_stored_value(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=b"junk")
        assert await get_session_tier(redis, "feishu:app:ou_a") is None

    @pytest.mark.asyncio
    async def test_swallows_redis_errors(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=ConnectionError("nope"))
        assert await get_session_tier(redis, "feishu:app:ou_a") is None


class TestSetSessionTier:
    @pytest.mark.asyncio
    async def test_returns_false_when_redis_none(self) -> None:
        assert await set_session_tier(None, "feishu:app:ou_a", "yolo") is False

    @pytest.mark.asyncio
    async def test_returns_false_for_invalid_tier(self) -> None:
        redis = AsyncMock()
        assert await set_session_tier(redis, "feishu:app:ou_a", "bogus") is False  # type: ignore[arg-type]
        redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_writes_with_default_ttl(self) -> None:
        redis = AsyncMock()
        redis.setex = AsyncMock()
        ok = await set_session_tier(redis, "feishu:app:ou_a", "yolo")
        assert ok is True
        redis.setex.assert_awaited_once()
        args = redis.setex.await_args
        assert args.args[0] == "pyclaw:feishu:tier:feishu:app:ou_a"
        assert args.args[1] == 7 * 24 * 3600
        assert args.args[2] == "yolo"

    @pytest.mark.asyncio
    async def test_writes_with_custom_ttl(self) -> None:
        redis = AsyncMock()
        redis.setex = AsyncMock()
        await set_session_tier(redis, "k", "approval", ttl_seconds=300)
        args = redis.setex.await_args
        assert args.args[1] == 300

    @pytest.mark.asyncio
    async def test_swallows_redis_errors(self) -> None:
        redis = AsyncMock()
        redis.setex = AsyncMock(side_effect=ConnectionError("nope"))
        assert await set_session_tier(redis, "k", "yolo") is False
