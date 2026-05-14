from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.gateway.forwarder import ForwardConsumer, ForwardPublisher


def _make_redis() -> AsyncMock:
    return AsyncMock()


class TestForwardPublisher:
    @pytest.mark.asyncio
    async def test_returns_true_when_subscriber_active(self) -> None:
        redis = _make_redis()
        redis.publish.return_value = 1
        pub = ForwardPublisher(redis)
        result = await pub.forward("w2", {"type": "feishu_event", "session_key": "s1"})
        assert result is True
        redis.publish.assert_called_once()
        args = redis.publish.call_args[0]
        assert args[0] == "pyclaw:forward:w2"
        decoded = json.loads(args[1])
        assert decoded["type"] == "feishu_event"
        assert decoded["session_key"] == "s1"

    @pytest.mark.asyncio
    async def test_returns_false_when_no_subscriber(self) -> None:
        redis = _make_redis()
        redis.publish.return_value = 0
        pub = ForwardPublisher(redis)
        result = await pub.forward("w2", {"type": "feishu_event"})
        assert result is False

    @pytest.mark.asyncio
    async def test_custom_prefix(self) -> None:
        redis = _make_redis()
        redis.publish.return_value = 1
        pub = ForwardPublisher(redis, prefix="custom:fwd:")
        await pub.forward("w2", {})
        assert redis.publish.call_args[0][0] == "custom:fwd:w2"

    def test_channel_for(self) -> None:
        pub = ForwardPublisher(_make_redis())
        assert pub.channel_for("w-abc") == "pyclaw:forward:w-abc"


class TestForwardConsumer:
    @pytest.mark.asyncio
    async def test_calls_handler_on_message(self) -> None:
        redis = MagicMock()
        pubsub = MagicMock()
        redis.pubsub = MagicMock(return_value=pubsub)
        pubsub.subscribe = AsyncMock()
        pubsub.unsubscribe = AsyncMock()
        pubsub.close = AsyncMock()

        async def fake_listen():
            yield {"type": "subscribe"}
            yield {"type": "message", "data": json.dumps({"session_key": "s1", "payload": "x"})}
            yield {"type": "message", "data": b'{"session_key": "s2"}'}

        pubsub.listen = fake_listen

        received: list[dict] = []

        async def handler(payload: dict) -> None:
            received.append(payload)
            if len(received) == 2:
                consumer._stopping = True

        consumer = ForwardConsumer(redis, "w1", handler)
        try:
            await asyncio.wait_for(consumer.start(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        assert len(received) == 2
        assert received[0]["session_key"] == "s1"
        assert received[1]["session_key"] == "s2"
        pubsub.subscribe.assert_called_with("pyclaw:forward:w1")

    @pytest.mark.asyncio
    async def test_invalid_json_skipped(self) -> None:
        redis = MagicMock()
        pubsub = MagicMock()
        redis.pubsub = MagicMock(return_value=pubsub)
        pubsub.subscribe = AsyncMock()
        pubsub.unsubscribe = AsyncMock()
        pubsub.close = AsyncMock()

        call_count = [0]

        async def fake_listen():
            call_count[0] += 1
            yield {"type": "message", "data": "not json"}
            yield {"type": "message", "data": json.dumps({"session_key": "ok"})}

        pubsub.listen = fake_listen

        received: list[dict] = []

        async def handler(payload: dict) -> None:
            received.append(payload)
            consumer._stopping = True

        consumer = ForwardConsumer(redis, "w1", handler)
        try:
            await asyncio.wait_for(consumer.start(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        assert len(received) == 1
        assert received[0]["session_key"] == "ok"

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_kill_loop(self) -> None:
        redis = MagicMock()
        pubsub = MagicMock()
        redis.pubsub = MagicMock(return_value=pubsub)
        pubsub.subscribe = AsyncMock()
        pubsub.unsubscribe = AsyncMock()
        pubsub.close = AsyncMock()

        async def fake_listen():
            yield {"type": "message", "data": json.dumps({"n": 1})}
            yield {"type": "message", "data": json.dumps({"n": 2})}

        pubsub.listen = fake_listen

        seen: list[int] = []

        async def handler(payload: dict) -> None:
            seen.append(payload["n"])
            if payload["n"] == 1:
                raise RuntimeError("boom")
            consumer._stopping = True

        consumer = ForwardConsumer(redis, "w1", handler)
        try:
            await asyncio.wait_for(consumer.start(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        assert seen == [1, 2]

    @pytest.mark.asyncio
    async def test_stop_closes_pubsub(self) -> None:
        redis = MagicMock()
        pubsub = MagicMock()
        redis.pubsub = MagicMock(return_value=pubsub)
        pubsub.subscribe = AsyncMock()
        pubsub.unsubscribe = AsyncMock()
        pubsub.close = AsyncMock()

        async def fake_listen():
            await asyncio.sleep(0.01)
            return
            yield  # unreachable, makes it a generator

        pubsub.listen = fake_listen

        consumer = ForwardConsumer(redis, "w1", AsyncMock())
        consumer._pubsub = pubsub
        await consumer.stop()

        pubsub.unsubscribe.assert_called_with("pyclaw:forward:w1")
        pubsub.close.assert_called_once()
        assert consumer._stopping is True

    def test_channel_property(self) -> None:
        consumer = ForwardConsumer(_make_redis(), "w-abc", AsyncMock())
        assert consumer.channel == "pyclaw:forward:w-abc"

    def test_channel_with_custom_prefix(self) -> None:
        consumer = ForwardConsumer(_make_redis(), "w-abc", AsyncMock(), prefix="custom:fwd:")
        assert consumer.channel == "custom:fwd:w-abc"
