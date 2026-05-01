from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.web.buffer import MessageBuffer


class TestMessageBufferAvailability:
    def test_available_when_redis_provided(self) -> None:
        buf = MessageBuffer(redis_client=MagicMock())
        assert buf.available is True

    def test_not_available_when_no_redis(self) -> None:
        buf = MessageBuffer(redis_client=None)
        assert buf.available is False


class TestMessageBufferNoRedis:
    """All operations should be no-ops when Redis is unavailable."""

    @pytest.mark.asyncio
    async def test_publish_returns_none(self) -> None:
        buf = MessageBuffer()
        result = await buf.publish("user1", {"type": "chat.delta", "text": "hi"})
        assert result is None

    @pytest.mark.asyncio
    async def test_replay_returns_empty(self) -> None:
        buf = MessageBuffer()
        result = await buf.replay("user1")
        assert result == []

    @pytest.mark.asyncio
    async def test_cleanup_is_noop(self) -> None:
        buf = MessageBuffer()
        await buf.cleanup("user1")


class TestMessageBufferWithMockRedis:
    def _make_redis(self) -> AsyncMock:
        redis = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_publish_calls_xadd_and_expire(self) -> None:
        redis = self._make_redis()
        redis.xadd.return_value = "1234-0"
        buf = MessageBuffer(redis_client=redis, max_entries=500, ttl_seconds=120)

        msg = {"type": "chat.delta", "text": "hello"}
        entry_id = await buf.publish("user1", msg)

        assert entry_id == "1234-0"
        redis.xadd.assert_awaited_once_with(
            "pyclaw:ws_stream:user1",
            {"data": json.dumps(msg)},
            maxlen=500,
        )
        redis.expire.assert_awaited_once_with("pyclaw:ws_stream:user1", 120)

    @pytest.mark.asyncio
    async def test_replay_returns_decoded_messages(self) -> None:
        redis = self._make_redis()
        msg1 = {"type": "chat.delta", "text": "one"}
        msg2 = {"type": "chat.done"}
        redis.xread.return_value = [
            (
                b"pyclaw:ws_stream:user1",
                [
                    ("1-0", {b"data": json.dumps(msg1).encode()}),
                    ("2-0", {b"data": json.dumps(msg2).encode()}),
                ],
            )
        ]

        buf = MessageBuffer(redis_client=redis, max_entries=1000)
        messages = await buf.replay("user1", last_id="0-0")

        assert messages == [msg1, msg2]
        redis.xread.assert_awaited_once_with(
            {"pyclaw:ws_stream:user1": "0-0"}, count=1000
        )

    @pytest.mark.asyncio
    async def test_replay_with_string_data_field(self) -> None:
        """When Redis returns str keys (not bytes), still works."""
        redis = self._make_redis()
        msg1 = {"type": "chat.delta", "text": "hi"}
        redis.xread.return_value = [
            (
                "pyclaw:ws_stream:user1",
                [
                    ("1-0", {"data": json.dumps(msg1)}),
                ],
            )
        ]
        buf = MessageBuffer(redis_client=redis)
        messages = await buf.replay("user1")
        assert messages == [msg1]

    @pytest.mark.asyncio
    async def test_replay_skips_malformed_entries(self) -> None:
        redis = self._make_redis()
        redis.xread.return_value = [
            (
                b"pyclaw:ws_stream:user1",
                [
                    ("1-0", {b"data": b"not-json"}),
                    ("2-0", {b"data": json.dumps({"ok": True}).encode()}),
                ],
            )
        ]
        buf = MessageBuffer(redis_client=redis)
        messages = await buf.replay("user1")
        assert messages == [{"ok": True}]

    @pytest.mark.asyncio
    async def test_replay_returns_empty_when_xread_returns_none(self) -> None:
        redis = self._make_redis()
        redis.xread.return_value = None
        buf = MessageBuffer(redis_client=redis)
        messages = await buf.replay("user1")
        assert messages == []

    @pytest.mark.asyncio
    async def test_cleanup_deletes_key(self) -> None:
        redis = self._make_redis()
        buf = MessageBuffer(redis_client=redis)
        await buf.cleanup("user1")
        redis.delete.assert_awaited_once_with("pyclaw:ws_stream:user1")

    def test_key_format(self) -> None:
        buf = MessageBuffer(redis_client=MagicMock())
        assert buf._key("user42") == "pyclaw:ws_stream:user42"

    @pytest.mark.asyncio
    async def test_custom_max_entries_and_ttl(self) -> None:
        redis = self._make_redis()
        redis.xadd.return_value = "99-0"
        buf = MessageBuffer(redis_client=redis, max_entries=50, ttl_seconds=60)
        await buf.publish("u", {"x": 1})
        redis.xadd.assert_awaited_once_with(
            "pyclaw:ws_stream:u", {"data": json.dumps({"x": 1})}, maxlen=50
        )
        redis.expire.assert_awaited_once_with("pyclaw:ws_stream:u", 60)
