from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
import redis.exceptions

from pyclaw.storage.lock.redis import LockLostError


@pytest.mark.asyncio
async def test_heartbeat_normal_renew_called_multiple_times() -> None:
    from pyclaw.core.curator import _heartbeat

    lock_manager = AsyncMock()
    lock_manager.renew = AsyncMock(return_value=True)
    event = asyncio.Event()

    task = asyncio.create_task(
        _heartbeat(lock_manager, "curator:cycle", "tok123", event, interval_s=0.05, ttl_ms=30_000)
    )
    await asyncio.sleep(0.18)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert lock_manager.renew.await_count >= 2
    assert not event.is_set()


@pytest.mark.asyncio
async def test_heartbeat_cancel_clean_exit_no_event_set() -> None:
    from pyclaw.core.curator import _heartbeat

    lock_manager = AsyncMock()
    lock_manager.renew = AsyncMock(return_value=True)
    event = asyncio.Event()

    task = asyncio.create_task(
        _heartbeat(lock_manager, "curator:cycle", "tok123", event, interval_s=0.5, ttl_ms=30_000)
    )
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not event.is_set()


@pytest.mark.asyncio
async def test_heartbeat_cas_failure_sets_event_and_raises() -> None:
    from pyclaw.core.curator import _heartbeat

    call_count = 0

    async def _renew_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            return False
        return True

    lock_manager = AsyncMock()
    lock_manager.renew = AsyncMock(side_effect=_renew_side_effect)
    event = asyncio.Event()

    task = asyncio.create_task(
        _heartbeat(lock_manager, "curator:cycle", "tok123", event, interval_s=0.05, ttl_ms=30_000)
    )

    with pytest.raises(LockLostError) as exc_info:
        await task

    assert "curator:cycle" in str(exc_info.value)
    assert event.is_set()


@pytest.mark.asyncio
async def test_heartbeat_network_error_sets_event_and_raises() -> None:
    from pyclaw.core.curator import _heartbeat

    call_count = 0

    async def _renew_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            raise redis.exceptions.ConnectionError("redis down")
        return True

    lock_manager = AsyncMock()
    lock_manager.renew = AsyncMock(side_effect=_renew_side_effect)
    event = asyncio.Event()

    task = asyncio.create_task(
        _heartbeat(lock_manager, "curator:cycle", "tok123", event, interval_s=0.05, ttl_ms=30_000)
    )

    with pytest.raises(LockLostError) as exc_info:
        await task

    assert "curator:cycle" in str(exc_info.value)
    assert event.is_set()
    assert exc_info.value.__cause__ is not None


@pytest.mark.asyncio
async def test_heartbeat_timeout_error_sets_event_and_raises() -> None:
    from pyclaw.core.curator import _heartbeat

    call_count = 0

    async def _renew_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            raise asyncio.TimeoutError()
        return True

    lock_manager = AsyncMock()
    lock_manager.renew = AsyncMock(side_effect=_renew_side_effect)
    event = asyncio.Event()

    task = asyncio.create_task(
        _heartbeat(lock_manager, "curator:cycle", "tok123", event, interval_s=0.05, ttl_ms=30_000)
    )

    with pytest.raises(LockLostError) as exc_info:
        await task

    assert "curator:cycle" in str(exc_info.value)
    assert event.is_set()


@pytest.mark.asyncio
async def test_heartbeat_signature() -> None:
    import inspect
    from pyclaw.core.curator import _heartbeat

    sig = inspect.signature(_heartbeat)
    params = list(sig.parameters.keys())
    assert params == ["lock_manager", "key", "token", "lock_lost_event", "interval_s", "ttl_ms"]
    assert sig.parameters["interval_s"].default == 10.0
    assert sig.parameters["ttl_ms"].default == 30_000


@pytest.mark.asyncio
async def test_heartbeat_propagation_event_observable_by_other_coro() -> None:
    from pyclaw.core.curator import _heartbeat

    lock_manager = AsyncMock()
    lock_manager.renew = AsyncMock(return_value=False)
    event = asyncio.Event()

    task = asyncio.create_task(
        _heartbeat(lock_manager, "curator:cycle", "tok123", event, interval_s=0.05, ttl_ms=30_000)
    )

    await asyncio.sleep(0.1)
    assert event.is_set()
    assert task.done()
