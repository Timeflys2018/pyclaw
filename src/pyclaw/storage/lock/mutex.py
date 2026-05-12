"""DistributedMutex: async context manager wrapping RedisLockManager.

Usage:
    async with DistributedMutex(lock_mgr, "my:key", task_manager=tm) as mutex:
        do_critical_work()
        mutex.check_alive()
        more_work()

Replaces the hand-rolled ``_heartbeat`` + ``_check_lock_alive`` +
``_heartbeat_task_done`` triplet in ``pyclaw.core.curator``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import redis.exceptions

from pyclaw.storage.lock.redis import LockLostError

if TYPE_CHECKING:
    from pyclaw.infra.task_manager import TaskManager
    from pyclaw.storage.lock.redis import RedisLockManager

logger = logging.getLogger(__name__)


class DistributedMutex:
    """Async context manager for a redis-backed distributed lock with heartbeat.

    Lifecycle:
        - ``__aenter__``: acquire lock (may raise ``LockAcquireError``) and spawn
          background heartbeat task.
        - critical section: caller invokes ``check_alive()`` at loop boundaries.
        - ``__aexit__``: cancel heartbeat (idempotent) and CAS-release lock.

    Loss detection uses **double fail-safe**:
        1. Primary signal: heartbeat sets ``_lock_lost_event`` on CAS failure or
           network error and raises ``LockLostError``.
        2. Secondary signal: if heartbeat task terminates unexpectedly without
           setting the event (defense-in-depth), ``check_alive`` treats the task
           as done -> raises ``LockLostError``.
        3. Pruning fail-closed: if the heartbeat task handle disappears from
           TaskManager (aggressive pruning), ``check_alive`` raises
           ``LockLostError`` rather than assuming the lock is still alive. This
           is a deliberate **behavior change** vs. the legacy
           ``_heartbeat_task_done`` helper which returned False (= alive) for
           the missing-handle case.
    """

    def __init__(
        self,
        lock_manager: "RedisLockManager",
        key: str,
        *,
        task_manager: "TaskManager",
        ttl_ms: int = 30_000,
        heartbeat_interval_s: float = 10.0,
        owner_label: str = "",
    ) -> None:
        if ttl_ms <= 0:
            raise ValueError(f"ttl_ms must be positive, got {ttl_ms}")
        if heartbeat_interval_s <= 0:
            raise ValueError(
                f"heartbeat_interval_s must be positive, got {heartbeat_interval_s}"
            )
        interval_ms = heartbeat_interval_s * 1000
        if interval_ms >= ttl_ms / 2:
            raise ValueError(
                f"heartbeat_interval_s ({heartbeat_interval_s}) must be less than "
                f"ttl_ms/2000 ({ttl_ms / 2000}) so renewals outpace expiration"
            )

        self._lock_manager = lock_manager
        self._key = key
        self._task_manager = task_manager
        self._ttl_ms = ttl_ms
        self._heartbeat_interval_s = heartbeat_interval_s
        self._owner_label = owner_label

        self._token: str | None = None
        self._lock_lost_event: asyncio.Event | None = None
        self._heartbeat_task_id: str | None = None

    @property
    def token(self) -> str | None:
        """Opaque token returned by the underlying lock manager on acquire."""
        return self._token

    @property
    def lost(self) -> bool:
        """True iff heartbeat detected CAS failure OR the heartbeat task is done.

        Before ``__aenter__`` and after clean ``__aexit__`` this returns False;
        only mid-context can it become True.
        """
        if self._lock_lost_event is None:
            return False
        if self._lock_lost_event.is_set():
            return True
        return self._heartbeat_task_finished()

    def check_alive(self) -> None:
        """Raise ``LockLostError`` if the lock has been lost; else no-op.

        Synchronous and O(1) — safe for tight loops. See design D2.
        """
        if self.lost:
            raise LockLostError(self._key)

    async def __aenter__(self) -> "DistributedMutex":
        self._token = await self._lock_manager.acquire(self._key, self._ttl_ms)
        self._lock_lost_event = asyncio.Event()
        hb_name = f"mutex-heartbeat:{self._key}"
        self._heartbeat_task_id = self._task_manager.spawn(
            hb_name,
            self._heartbeat(),
            category="heartbeat",
        )
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._heartbeat_task_id is not None:
            try:
                await self._task_manager.cancel(self._heartbeat_task_id)
            except Exception:
                logger.debug(
                    "mutex heartbeat cancel errored (benign) key=%s owner=%s",
                    self._key, self._owner_label, exc_info=True,
                )
            self._consume_heartbeat_exception()
        if self._token is not None:
            try:
                await self._lock_manager.release(self._key, self._token)
            except Exception:
                logger.debug(
                    "mutex release errored (benign) key=%s owner=%s",
                    self._key, self._owner_label, exc_info=True,
                )

    def _consume_heartbeat_exception(self) -> None:
        """Retrieve the heartbeat task's exception (if any) so asyncio does
        not emit a ``Task exception was never retrieved`` warning when the
        task object is garbage-collected. Heartbeat exceptions are already
        logged by ``_heartbeat`` itself, so this is purely a cleanup no-op.
        """
        assert self._heartbeat_task_id is not None
        handle = self._task_manager._tasks.get(self._heartbeat_task_id)
        if handle is None:
            return
        task = handle.asyncio_task
        if not task.done() or task.cancelled():
            return
        try:
            task.exception()
        except Exception:
            pass

    async def _heartbeat(self) -> None:
        """Background task: periodically renew the lock until cancelled.

        On CAS failure or network error: set the lost event, log, and raise
        ``LockLostError`` (which terminates the task). On cancellation: re-raise
        ``CancelledError`` so TaskManager can observe clean shutdown.
        """
        assert self._lock_lost_event is not None
        assert self._token is not None
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval_s)
                try:
                    ok = await self._lock_manager.renew(
                        self._key, self._token, self._ttl_ms,
                    )
                except (
                    redis.exceptions.ConnectionError,
                    redis.exceptions.TimeoutError,
                    asyncio.TimeoutError,
                    OSError,
                ) as exc:
                    self._lock_lost_event.set()
                    logger.error(
                        "mutex heartbeat renew network error key=%s owner=%s",
                        self._key, self._owner_label, exc_info=True,
                    )
                    raise LockLostError(self._key) from exc
                if not ok:
                    self._lock_lost_event.set()
                    logger.error(
                        "mutex heartbeat renew CAS failed key=%s owner=%s",
                        self._key, self._owner_label,
                    )
                    raise LockLostError(self._key)
        except asyncio.CancelledError:
            raise

    def _heartbeat_task_finished(self) -> bool:
        """Return True if the heartbeat task is done OR its handle has been pruned.

        Fail-closed: a missing handle means we cannot prove liveness, so we
        assume the heartbeat has terminated (see class docstring, rule 3).
        """
        if self._heartbeat_task_id is None:
            return False
        state = self._task_manager.get_state(self._heartbeat_task_id)
        if state is None:
            return True
        return state != "running"
