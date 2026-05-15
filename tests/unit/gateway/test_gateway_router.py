from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pyclaw.gateway.affinity import AffinityRegistry
from pyclaw.gateway.forwarder import ForwardPublisher
from pyclaw.gateway.router import GatewayRouter


def _make_affinity(worker_id: str) -> AffinityRegistry:
    redis = AsyncMock()
    return AffinityRegistry(redis, worker_id=worker_id)


def _make_forwarder() -> ForwardPublisher:
    return ForwardPublisher(AsyncMock())


class TestRouteLocal:
    @pytest.mark.asyncio
    async def test_local_when_owner_is_self(self) -> None:
        affinity = _make_affinity("w1")
        affinity._redis.get.return_value = "w1"
        forwarder = _make_forwarder()
        router = GatewayRouter(affinity, forwarder)

        result = await router.route("sess1", {"type": "feishu_event"})

        assert result == "local"
        affinity._redis.expire.assert_called_once_with("pyclaw:affinity:sess1", 300)
        forwarder._redis.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_local_when_unclaimed_and_we_win(self) -> None:
        affinity = _make_affinity("w1")
        affinity._redis.get.return_value = None
        affinity._redis.set.return_value = True
        forwarder = _make_forwarder()
        router = GatewayRouter(affinity, forwarder)

        result = await router.route("sess1", {})

        assert result == "local"
        affinity._redis.set.assert_called_once_with("pyclaw:affinity:sess1", "w1", nx=True, ex=300)
        forwarder._redis.publish.assert_not_called()


class TestRouteForwarded:
    @pytest.mark.asyncio
    async def test_forwarded_to_other_worker(self) -> None:
        affinity = _make_affinity("w1")
        affinity._redis.get.return_value = "w2"
        forwarder = _make_forwarder()
        forwarder._redis.publish.return_value = 1
        router = GatewayRouter(affinity, forwarder)

        result = await router.route("sess1", {"type": "feishu_event"})

        assert result == "forwarded"
        forwarder._redis.publish.assert_called_once()
        assert forwarder._redis.publish.call_args[0][0] == "pyclaw:forward:w2"

    @pytest.mark.asyncio
    async def test_fallback_local_when_forward_fails(self) -> None:
        affinity = _make_affinity("w1")
        affinity._redis.get.return_value = "w2"
        forwarder = _make_forwarder()
        forwarder._redis.publish.return_value = 0
        router = GatewayRouter(affinity, forwarder)

        result = await router.route("sess1", {})

        assert result == "local"
        affinity._redis.set.assert_called_with("pyclaw:affinity:sess1", "w1", ex=300)


class TestRaceLoser:
    @pytest.mark.asyncio
    async def test_loser_re_resolves_and_forwards(self) -> None:
        affinity = _make_affinity("w1")
        affinity._redis.get.side_effect = [None, "w2"]
        affinity._redis.set.return_value = None
        forwarder = _make_forwarder()
        forwarder._redis.publish.return_value = 1
        router = GatewayRouter(affinity, forwarder)

        result = await router.route("sess1", {})

        assert result == "forwarded"
        assert affinity._redis.get.call_count == 2
        forwarder._redis.publish.assert_called_once()
        assert forwarder._redis.publish.call_args[0][0] == "pyclaw:forward:w2"

    @pytest.mark.asyncio
    async def test_loser_proceeds_local_if_re_resolve_returns_self(self) -> None:
        affinity = _make_affinity("w1")
        affinity._redis.get.side_effect = [None, "w1"]
        affinity._redis.set.return_value = None
        forwarder = _make_forwarder()
        router = GatewayRouter(affinity, forwarder)

        result = await router.route("sess1", {})

        assert result == "local"
        forwarder._redis.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_loser_proceeds_local_if_re_resolve_returns_none(self) -> None:
        affinity = _make_affinity("w1")
        affinity._redis.get.side_effect = [None, None]
        affinity._redis.set.return_value = None
        forwarder = _make_forwarder()
        router = GatewayRouter(affinity, forwarder)

        result = await router.route("sess1", {})

        assert result == "local"
        forwarder._redis.publish.assert_not_called()


class TestRedisErrorHandling:
    @pytest.mark.asyncio
    async def test_returns_local_on_connection_error(self) -> None:
        affinity = _make_affinity("w1")
        affinity._redis.get.side_effect = ConnectionError("redis down")
        forwarder = _make_forwarder()
        router = GatewayRouter(affinity, forwarder)

        result = await router.route("sess1", {})

        assert result == "local"

    @pytest.mark.asyncio
    async def test_returns_local_on_timeout(self) -> None:
        affinity = _make_affinity("w1")
        affinity._redis.get.side_effect = TimeoutError("redis timeout")
        forwarder = _make_forwarder()
        router = GatewayRouter(affinity, forwarder)

        result = await router.route("sess1", {})

        assert result == "local"

    @pytest.mark.asyncio
    async def test_returns_local_on_os_error(self) -> None:
        affinity = _make_affinity("w1")
        affinity._redis.get.side_effect = OSError("network unreachable")
        forwarder = _make_forwarder()
        router = GatewayRouter(affinity, forwarder)

        result = await router.route("sess1", {})

        assert result == "local"


class TestProperties:
    def test_worker_id_passthrough(self) -> None:
        affinity = _make_affinity("w-abc")
        router = GatewayRouter(affinity, _make_forwarder())
        assert router.worker_id == "w-abc"

    def test_affinity_property(self) -> None:
        affinity = _make_affinity("w1")
        router = GatewayRouter(affinity, _make_forwarder())
        assert router.affinity is affinity
