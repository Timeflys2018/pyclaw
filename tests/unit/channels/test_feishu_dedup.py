from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pyclaw.channels.feishu.dedup import FeishuDedup


@pytest.mark.asyncio
async def test_first_message_not_duplicate() -> None:
    dedup = FeishuDedup()
    assert not await dedup.is_duplicate("msg-001")


@pytest.mark.asyncio
async def test_duplicate_rejected() -> None:
    dedup = FeishuDedup()
    await dedup.is_duplicate("msg-002")
    assert await dedup.is_duplicate("msg-002")


@pytest.mark.asyncio
async def test_ttl_present_after_record() -> None:
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=None)
    dedup = FeishuDedup(redis_client=mock_redis, key_prefix="pyclaw:")
    result = await dedup.is_duplicate("msg-003")
    assert result is True
    mock_redis.set.assert_awaited_once_with(
        "pyclaw:feishu:dedup:msg-003", "1", nx=True, ex=43200
    )


@pytest.mark.asyncio
async def test_fallback_without_redis() -> None:
    dedup = FeishuDedup(redis_client=None)
    assert not await dedup.is_duplicate("msg-004")
    assert await dedup.is_duplicate("msg-004")
