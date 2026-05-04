"""TaskManager — centralized asyncio background-task registry.

Pure-asyncio scope: manages ``asyncio.Task`` objects spawned from the main
event loop only.  Thread-based background work (e.g. the Feishu WebSocket
client forced into a ``threading.Thread`` by lark-oapi) is explicitly
excluded and managed by its owning plugin's ``start()``/``stop()`` contract.

Precondition: ``spawn()`` MUST be called from within the running event loop.
Calling from a non-event-loop thread is undefined and may raise
``RuntimeError: no running event loop``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

TaskCategory = Literal["heartbeat", "consumer", "archive", "nudge", "evolution", "generic"]
TaskState = Literal["running", "done", "cancelled", "failed"]
_PRUNE_AGE_S = 300


@dataclass
class TaskInfo:
    """Snapshot of a managed task's state."""
    task_id: str
    name: str
    category: TaskCategory
    state: TaskState
    created_at: float
    duration_s: float
    exception: str | None


@dataclass
class TaskHandle:
    """Internal bookkeeping for a spawned task."""
    task_id: str
    name: str
    category: TaskCategory
    asyncio_task: asyncio.Task  # type: ignore[type-arg]
    created_at: float
    exception: str | None = None


@dataclass
class ShutdownReport:
    """Summary of shutdown outcome."""
    completed: int
    cancelled: int
    timed_out: int
    failed: int
    total_duration_s: float


class TaskManagerClosedError(RuntimeError):
    """Raised when spawning on a closed TaskManager."""


class TaskManager:
    """Manages the lifecycle of background asyncio tasks."""

    def __init__(self, default_shutdown_grace_s: float = 30.0) -> None:
        self._tasks: dict[str, TaskHandle] = {}
        self._closed: bool = False
        self._next_id: int = 0
        self._default_shutdown_grace_s = default_shutdown_grace_s

    def spawn(
        self, name: str, coro: Coroutine,  # type: ignore[type-arg]
        *, category: TaskCategory = "generic",
        on_error: Callable[[BaseException], None] | None = None,
    ) -> str:
        """Spawn a coroutine as a managed task; returns task_id."""
        self._maybe_prune()
        if self._closed:
            coro.close()
            raise TaskManagerClosedError(f"TaskManager closed, cannot spawn '{name}'")
        task_id = f"t{self._next_id:06d}"
        self._next_id += 1
        asyncio_task = asyncio.create_task(self._wrap(task_id, coro, on_error), name=name)
        self._tasks[task_id] = TaskHandle(
            task_id=task_id, name=name, category=category,
            asyncio_task=asyncio_task, created_at=time.monotonic(),
        )
        logger.debug("spawned task_id=%s name=%s category=%s", task_id, name, category)
        return task_id

    async def _wrap(
        self, task_id: str, coro: Coroutine,  # type: ignore[type-arg]
        on_error: Callable[[BaseException], None] | None,
    ) -> object:
        try:
            return await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            handle = self._tasks.get(task_id)
            if handle:
                handle.exception = f"{type(exc).__name__}: {exc}"
            logger.exception("task failed id=%s name=%s", task_id, handle.name if handle else "?")
            if on_error is not None:
                try:
                    on_error(exc)
                except Exception:
                    logger.exception("on_error callback itself failed for task %s", task_id)
            raise

    async def cancel(self, task_id: str, *, timeout: float = 5.0) -> bool:
        """Cancel a running task; returns True if cancellation was initiated."""
        handle = self._tasks.get(task_id)
        if handle is None or handle.asyncio_task.done():
            return False
        handle.asyncio_task.cancel()
        await asyncio.wait({handle.asyncio_task}, timeout=timeout)
        if not handle.asyncio_task.done():
            logger.warning("cancel timeout id=%s name=%s", task_id, handle.name)
        return True

    def get_state(self, task_id: str) -> TaskState | None:
        """Return task state or None if unknown."""
        if task_id not in self._tasks:
            return None
        return self._task_state(self._tasks[task_id])

    def _maybe_prune(self) -> None:
        now = time.monotonic()
        to_remove = [
            tid for tid, h in self._tasks.items()
            if self._task_state(h) != "running" and h.created_at < now - _PRUNE_AGE_S
        ]
        for tid in to_remove:
            del self._tasks[tid]

    def list_tasks(
        self, *, category: TaskCategory | None = None, include_done: bool = False,
    ) -> list[TaskInfo]:
        """List managed tasks with optional category/state filtering."""
        now = time.monotonic()
        result: list[TaskInfo] = []
        for handle in self._tasks.values():
            state = self._task_state(handle)
            if not include_done and state != "running":
                continue
            if category is not None and handle.category != category:
                continue
            result.append(TaskInfo(
                task_id=handle.task_id, name=handle.name, category=handle.category,
                state=state, created_at=handle.created_at,
                duration_s=now - handle.created_at, exception=handle.exception,
            ))
        return result

    def _task_state(self, handle: TaskHandle) -> TaskState:
        task = handle.asyncio_task
        if not task.done():
            return "running"
        if task.cancelled():
            return "cancelled"
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return "cancelled"
        if exc is not None:
            return "failed"
        return "done"

    async def shutdown(self, grace_s: float | None = None) -> ShutdownReport:
        """Cancel all running tasks and wait up to grace_s for completion."""
        if self._closed:
            return ShutdownReport(0, 0, 0, 0, 0.0)
        self._closed = True
        grace = grace_s if grace_s is not None else self._default_shutdown_grace_s
        running = [h for h in self._tasks.values() if not h.asyncio_task.done()]
        for h in running:
            h.asyncio_task.cancel()
        start = time.monotonic()
        if running:
            await asyncio.wait(
                {h.asyncio_task for h in running},
                timeout=grace,
                return_when=asyncio.ALL_COMPLETED,
            )
        completed = cancelled = failed = timed_out = 0
        for h in running:
            t = h.asyncio_task
            if not t.done():
                timed_out += 1
            elif t.cancelled():
                cancelled += 1
            else:
                try:
                    exc = t.exception()
                except asyncio.CancelledError:
                    cancelled += 1
                    continue
                if exc is not None:
                    failed += 1
                else:
                    completed += 1
        return ShutdownReport(
            completed=completed, cancelled=cancelled,
            timed_out=timed_out, failed=failed,
            total_duration_s=time.monotonic() - start,
        )
