"""Tests for DistributedMutex (Phase A1).

Audit-trail anchors: A1.1, A1.3, A1.4, A1.5 map to tasks.md.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest
import redis.exceptions

from pyclaw.infra.task_manager import TaskManager
from pyclaw.storage.lock.mutex import DistributedMutex
from pyclaw.storage.lock.redis import LockAcquireError, LockLostError, RedisLockManager


def _mock_lock_manager(*, token: str = "tok_abc123") -> AsyncMock:
    mgr = AsyncMock(spec=RedisLockManager)
    mgr.acquire = AsyncMock(return_value=token)
    mgr.release = AsyncMock(return_value=True)
    mgr.renew = AsyncMock(return_value=True)
    return mgr


@pytest.fixture
def tm() -> TaskManager:
    """Per-test TaskManager; no explicit teardown (test intervals are tiny)."""
    return TaskManager()


class TestDistributedMutexValidation:
    """A1.5: constructor parameter validation."""

    def test_rejects_zero_ttl_ms(self, tm: TaskManager) -> None:
        mgr = _mock_lock_manager()
        with pytest.raises(ValueError, match="ttl_ms must be positive"):
            DistributedMutex(mgr, "my:lock", task_manager=tm, ttl_ms=0)

    def test_rejects_negative_ttl_ms(self, tm: TaskManager) -> None:
        mgr = _mock_lock_manager()
        with pytest.raises(ValueError, match="ttl_ms must be positive"):
            DistributedMutex(mgr, "my:lock", task_manager=tm, ttl_ms=-100)

    def test_rejects_zero_heartbeat_interval(self, tm: TaskManager) -> None:
        mgr = _mock_lock_manager()
        with pytest.raises(ValueError, match="heartbeat_interval_s must be positive"):
            DistributedMutex(mgr, "my:lock", task_manager=tm, heartbeat_interval_s=0)

    def test_rejects_negative_heartbeat_interval(self, tm: TaskManager) -> None:
        mgr = _mock_lock_manager()
        with pytest.raises(ValueError, match="heartbeat_interval_s must be positive"):
            DistributedMutex(mgr, "my:lock", task_manager=tm, heartbeat_interval_s=-5.0)

    def test_accepts_default_ttl_and_interval(self, tm: TaskManager) -> None:
        mgr = _mock_lock_manager()
        mutex = DistributedMutex(mgr, "my:lock", task_manager=tm)
        assert mutex is not None

    def test_rejects_interval_exceeding_half_ttl(self, tm: TaskManager) -> None:
        mgr = _mock_lock_manager()
        with pytest.raises(ValueError, match="must be less than ttl_ms/2000"):
            DistributedMutex(
                mgr,
                "my:lock",
                task_manager=tm,
                ttl_ms=30_000,
                heartbeat_interval_s=20.0,
            )

    def test_rejects_interval_equal_half_ttl(self, tm: TaskManager) -> None:
        mgr = _mock_lock_manager()
        with pytest.raises(ValueError, match="must be less than ttl_ms/2000"):
            DistributedMutex(
                mgr,
                "my:lock",
                task_manager=tm,
                ttl_ms=30_000,
                heartbeat_interval_s=15.0,
            )

    def test_rejects_interval_exceeding_small_ttl(self, tm: TaskManager) -> None:
        mgr = _mock_lock_manager()
        with pytest.raises(ValueError, match="must be less than ttl_ms/2000"):
            DistributedMutex(
                mgr,
                "my:lock",
                task_manager=tm,
                ttl_ms=5_000,
                heartbeat_interval_s=10.0,
            )


class TestDistributedMutexAcquireRelease:
    """A1.1: __aenter__/__aexit__ lifecycle."""

    @pytest.mark.asyncio
    async def test_context_manager_acquires_lock_and_spawns_heartbeat(
        self,
        tm: TaskManager,
    ) -> None:
        mgr = _mock_lock_manager(token="tok_xyz")
        mutex = DistributedMutex(
            mgr,
            "my:lock",
            task_manager=tm,
            heartbeat_interval_s=10.0,
        )
        async with mutex as entered:
            assert entered is mutex
            assert mutex.token == "tok_xyz"
            assert len(tm.list_tasks(category="heartbeat")) == 1
            mgr.acquire.assert_awaited_once()
            mgr.release.assert_not_awaited()

        mgr.release.assert_awaited_once()
        release_call = mgr.release.await_args
        assert release_call.args[0] == "my:lock"
        assert release_call.args[1] == "tok_xyz"
        assert tm.list_tasks(category="heartbeat") == []

    @pytest.mark.asyncio
    async def test_aenter_passes_ttl_ms_to_acquire(self, tm: TaskManager) -> None:
        mgr = _mock_lock_manager()
        mutex = DistributedMutex(
            mgr,
            "my:lock",
            task_manager=tm,
            ttl_ms=7_500,
            heartbeat_interval_s=1.0,
        )
        async with mutex:
            pass
        mgr.acquire.assert_awaited_once_with("my:lock", 7_500)

    @pytest.mark.asyncio
    async def test_acquire_failure_does_not_spawn_heartbeat(
        self,
        tm: TaskManager,
    ) -> None:
        mgr = _mock_lock_manager()
        mgr.acquire.side_effect = LockAcquireError("pyclaw:my:lock")

        mutex = DistributedMutex(mgr, "my:lock", task_manager=tm)
        with pytest.raises(LockAcquireError):
            async with mutex:
                pytest.fail("should not reach body on acquire failure")

        assert tm.list_tasks(category="heartbeat") == []
        mgr.release.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_aexit_on_exception_still_cleans_up(self, tm: TaskManager) -> None:
        mgr = _mock_lock_manager()
        mutex = DistributedMutex(
            mgr,
            "my:lock",
            task_manager=tm,
            heartbeat_interval_s=10.0,
        )

        with pytest.raises(RuntimeError, match="user code"):
            async with mutex:
                raise RuntimeError("user code")

        mgr.release.assert_awaited_once()
        assert tm.list_tasks(category="heartbeat") == []

    @pytest.mark.asyncio
    async def test_heartbeat_spawned_with_heartbeat_category(
        self,
        tm: TaskManager,
    ) -> None:
        """category='heartbeat' is required for /tasks kill protection."""
        mgr = _mock_lock_manager()
        mutex = DistributedMutex(
            mgr,
            "my:lock",
            task_manager=tm,
            heartbeat_interval_s=10.0,
        )
        async with mutex:
            hb_tasks = tm.list_tasks(category="heartbeat")
            assert len(hb_tasks) == 1
            assert not any(
                t.category == "generic"
                for t in tm.list_tasks(include_done=True)
                if t.name.startswith("mutex-heartbeat")
            )


class TestDistributedMutexCheckAlive:
    """A1.1: check_alive() semantics."""

    @pytest.mark.asyncio
    async def test_check_alive_noop_during_normal_hold(
        self,
        tm: TaskManager,
    ) -> None:
        mgr = _mock_lock_manager()
        mutex = DistributedMutex(
            mgr,
            "my:lock",
            task_manager=tm,
            heartbeat_interval_s=10.0,
        )
        async with mutex:
            mutex.check_alive()
            mutex.check_alive()
            mutex.check_alive()

    @pytest.mark.asyncio
    async def test_check_alive_raises_when_lost_event_set(
        self,
        tm: TaskManager,
    ) -> None:
        mgr = _mock_lock_manager()
        call_count = 0

        async def _renew_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return call_count < 2

        mgr.renew = AsyncMock(side_effect=_renew_side_effect)

        mutex = DistributedMutex(
            mgr,
            "my:lock",
            task_manager=tm,
            heartbeat_interval_s=0.02,
        )
        async with mutex:
            await asyncio.sleep(0.1)
            with pytest.raises(LockLostError, match="my:lock"):
                mutex.check_alive()

    @pytest.mark.asyncio
    async def test_check_alive_raises_when_heartbeat_task_done_without_event(
        self,
        tm: TaskManager,
    ) -> None:
        """Double fail-safe path: task done but event not set."""
        mgr = _mock_lock_manager()
        mutex = DistributedMutex(
            mgr,
            "my:lock",
            task_manager=tm,
            heartbeat_interval_s=10.0,
        )
        async with mutex:
            assert mutex._heartbeat_task_id is not None
            await tm.cancel(mutex._heartbeat_task_id)
            await asyncio.sleep(0.01)
            assert mutex._lock_lost_event is not None
            assert not mutex._lock_lost_event.is_set()
            with pytest.raises(LockLostError, match="my:lock"):
                mutex.check_alive()


class TestDistributedMutexPruningRace:
    """A1.1: fail-closed when heartbeat handle is pruned (behavior change vs legacy)."""

    @pytest.mark.asyncio
    async def test_check_alive_raises_when_handle_pruned(
        self,
        tm: TaskManager,
    ) -> None:
        mgr = _mock_lock_manager()
        mutex = DistributedMutex(
            mgr,
            "my:lock",
            task_manager=tm,
            heartbeat_interval_s=10.0,
        )
        async with mutex:
            assert mutex._heartbeat_task_id is not None
            hb_id = mutex._heartbeat_task_id
            await tm.cancel(hb_id)
            await asyncio.sleep(0.01)
            tm._tasks.pop(hb_id, None)
            with pytest.raises(LockLostError, match="my:lock"):
                mutex.check_alive()


class TestDistributedMutexHeartbeat:
    """A1.3 + A1.4: internal heartbeat coroutine behavior."""

    @pytest.mark.asyncio
    async def test_heartbeat_cas_failure_sets_lost_event(
        self,
        tm: TaskManager,
    ) -> None:
        mgr = _mock_lock_manager()
        mgr.renew = AsyncMock(return_value=False)

        mutex = DistributedMutex(
            mgr,
            "my:lock",
            task_manager=tm,
            heartbeat_interval_s=0.02,
        )
        async with mutex:
            await asyncio.sleep(0.08)
            assert mutex.lost is True

    @pytest.mark.asyncio
    async def test_heartbeat_connection_error_sets_lost_event(
        self,
        tm: TaskManager,
    ) -> None:
        mgr = _mock_lock_manager()
        mgr.renew = AsyncMock(
            side_effect=redis.exceptions.ConnectionError("redis down"),
        )

        mutex = DistributedMutex(
            mgr,
            "my:lock",
            task_manager=tm,
            heartbeat_interval_s=0.02,
        )
        async with mutex:
            await asyncio.sleep(0.08)
            assert mutex.lost is True

    @pytest.mark.asyncio
    async def test_heartbeat_timeout_error_sets_lost_event(
        self,
        tm: TaskManager,
    ) -> None:
        mgr = _mock_lock_manager()
        mgr.renew = AsyncMock(side_effect=TimeoutError())

        mutex = DistributedMutex(
            mgr,
            "my:lock",
            task_manager=tm,
            heartbeat_interval_s=0.02,
        )
        async with mutex:
            await asyncio.sleep(0.08)
            assert mutex.lost is True

    @pytest.mark.asyncio
    async def test_heartbeat_renews_periodically_when_alive(
        self,
        tm: TaskManager,
    ) -> None:
        mgr = _mock_lock_manager()
        mgr.renew = AsyncMock(return_value=True)

        mutex = DistributedMutex(
            mgr,
            "my:lock",
            task_manager=tm,
            heartbeat_interval_s=0.02,
        )
        async with mutex:
            await asyncio.sleep(0.1)
            assert mgr.renew.await_count >= 2
            assert mutex.lost is False

    @pytest.mark.asyncio
    async def test_heartbeat_clean_cancel_does_not_set_event(
        self,
        tm: TaskManager,
    ) -> None:
        mgr = _mock_lock_manager()
        mgr.renew = AsyncMock(return_value=True)

        mutex = DistributedMutex(
            mgr,
            "my:lock",
            task_manager=tm,
            heartbeat_interval_s=10.0,
        )
        async with mutex:
            assert mutex.lost is False

        assert mutex._lock_lost_event is not None
        assert not mutex._lock_lost_event.is_set()


class TestDistributedMutexAPI:
    """Public API surface sanity checks."""

    def test_token_is_none_before_aenter(self, tm: TaskManager) -> None:
        mgr = _mock_lock_manager()
        mutex = DistributedMutex(mgr, "my:lock", task_manager=tm)
        assert mutex.token is None

    def test_lost_is_false_before_aenter(self, tm: TaskManager) -> None:
        mgr = _mock_lock_manager()
        mutex = DistributedMutex(mgr, "my:lock", task_manager=tm)
        assert mutex.lost is False

    def test_constructor_signature(self) -> None:
        sig = inspect.signature(DistributedMutex.__init__)
        params = list(sig.parameters.keys())
        assert "lock_manager" in params
        assert "key" in params
        assert "task_manager" in params
        assert "ttl_ms" in params
        assert "heartbeat_interval_s" in params
        assert "owner_label" in params
        assert sig.parameters["task_manager"].kind == inspect.Parameter.KEYWORD_ONLY

    def test_check_alive_is_sync(self) -> None:
        """check_alive must be sync so it's cheap to call in tight loops (D2)."""
        assert not inspect.iscoroutinefunction(DistributedMutex.check_alive)
