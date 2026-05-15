from __future__ import annotations

import re
import time
from unittest.mock import AsyncMock

import pytest

from pyclaw.gateway.worker_registry import WorkerRegistry, generate_worker_id


class TestGenerateWorkerId:
    def test_format_matches_spec(self) -> None:
        wid = generate_worker_id()
        assert re.match(r"^worker:[^:]+:\d+:[0-9a-f]{4}$", wid), f"unexpected format: {wid}"

    def test_two_calls_return_unique_ids(self) -> None:
        a = generate_worker_id()
        b = generate_worker_id()
        assert a != b


class TestWorkerRegistryWithoutRedis:
    def test_available_false_without_redis(self) -> None:
        reg = WorkerRegistry(worker_id="w1")
        assert reg.available is False

    def test_worker_id_property(self) -> None:
        reg = WorkerRegistry(worker_id="my-worker")
        assert reg.worker_id == "my-worker"

    @pytest.mark.asyncio
    async def test_register_noop_without_redis(self) -> None:
        reg = WorkerRegistry(worker_id="w1")
        await reg.register()

    @pytest.mark.asyncio
    async def test_deregister_noop_without_redis(self) -> None:
        reg = WorkerRegistry(worker_id="w1")
        await reg.deregister()

    @pytest.mark.asyncio
    async def test_active_workers_returns_self_without_redis(self) -> None:
        reg = WorkerRegistry(worker_id="w1")
        workers = await reg.active_workers()
        assert len(workers) == 1
        assert workers[0]["id"] == "w1"
        assert workers[0]["status"] == "healthy"


class TestWorkerRegistryWithRedis:
    def _mock_redis(self) -> AsyncMock:
        return AsyncMock()

    def test_available_true_with_redis(self) -> None:
        redis = self._mock_redis()
        reg = WorkerRegistry(redis_client=redis, worker_id="w1")
        assert reg.available is True

    @pytest.mark.asyncio
    async def test_register_calls_zadd(self) -> None:
        redis = self._mock_redis()
        reg = WorkerRegistry(redis_client=redis, worker_id="w1", key="pyclaw:workers")
        await reg.register()
        redis.zadd.assert_called_once()
        args = redis.zadd.call_args
        assert args[0][0] == "pyclaw:workers"
        mapping = args[0][1]
        assert "w1" in mapping

    @pytest.mark.asyncio
    async def test_heartbeat_delegates_to_register(self) -> None:
        redis = self._mock_redis()
        reg = WorkerRegistry(redis_client=redis, worker_id="w1")
        await reg.heartbeat()
        redis.zadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_deregister_calls_zrem(self) -> None:
        redis = self._mock_redis()
        reg = WorkerRegistry(redis_client=redis, worker_id="w1", key="pyclaw:workers")
        await reg.deregister()
        redis.zrem.assert_called_once_with("pyclaw:workers", "w1")

    @pytest.mark.asyncio
    async def test_active_workers_healthy_status(self) -> None:
        redis = self._mock_redis()
        now = time.time()
        redis.zrangebyscore.return_value = [("w1", now - 5), ("w2", now - 30)]
        reg = WorkerRegistry(redis_client=redis, worker_id="w1")
        workers = await reg.active_workers()
        assert len(workers) == 2
        assert workers[0]["id"] == "w1"
        assert workers[0]["status"] == "healthy"
        assert workers[1]["id"] == "w2"
        assert workers[1]["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_active_workers_stale_status(self) -> None:
        redis = self._mock_redis()
        now = time.time()
        redis.zrangebyscore.return_value = [("w1", now - 100)]
        reg = WorkerRegistry(redis_client=redis, worker_id="w1")
        workers = await reg.active_workers()
        assert workers[0]["status"] == "stale"

    @pytest.mark.asyncio
    async def test_active_workers_dead_status(self) -> None:
        redis = self._mock_redis()
        now = time.time()
        redis.zrangebyscore.return_value = [("w1", now - 140)]
        reg = WorkerRegistry(redis_client=redis, worker_id="w1")
        workers = await reg.active_workers()
        assert workers[0]["status"] == "dead"

    @pytest.mark.asyncio
    async def test_active_workers_bytes_member(self) -> None:
        redis = self._mock_redis()
        now = time.time()
        redis.zrangebyscore.return_value = [(b"w1", now - 5)]
        reg = WorkerRegistry(redis_client=redis, worker_id="w1")
        workers = await reg.active_workers()
        assert workers[0]["id"] == "w1"

    @pytest.mark.asyncio
    async def test_active_workers_empty_redis(self) -> None:
        redis = self._mock_redis()
        redis.zrangebyscore.return_value = []
        reg = WorkerRegistry(redis_client=redis, worker_id="w1")
        workers = await reg.active_workers()
        assert workers == []

    @pytest.mark.asyncio
    async def test_active_workers_honors_stale_threshold(self) -> None:
        redis = self._mock_redis()
        now = time.time()
        redis.zrangebyscore.return_value = [
            ("w1", now - 30),
            ("w2", now - 150),
            ("w3", now - 200),
        ]
        reg = WorkerRegistry(redis_client=redis, worker_id="w1")
        workers = await reg.active_workers(stale_threshold=120)
        assert workers[0]["status"] == "healthy"
        assert workers[1]["status"] == "stale"
        assert workers[2]["status"] == "dead"
