from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from pyclaw.core.agent.run_control import RunControl
from pyclaw.infra.task_manager import TaskManager

logger = logging.getLogger(__name__)


class FeishuQueueRegistry:
    def __init__(self, task_manager: TaskManager) -> None:
        self._task_manager = task_manager
        self._entries: dict[str, tuple[asyncio.Queue[Coroutine[Any, Any, None]], str]] = {}
        self._busy: dict[str, bool] = {}
        self._run_controls: dict[str, RunControl] = {}
        self._last_usage: dict[str, dict[str, int]] = {}

    async def enqueue(
        self,
        session_id: str,
        coro: Coroutine[Any, Any, None],
        *,
        owner: str | None = None,
    ) -> None:
        if session_id not in self._entries:
            q: asyncio.Queue[Coroutine[Any, Any, None]] = asyncio.Queue()
            task_id = self._task_manager.spawn(
                f"feishu-consumer:{session_id}",
                self._consume(session_id, q),
                category="consumer",
                owner=owner,
            )
            self._entries[session_id] = (q, task_id)
        queue, _tid = self._entries[session_id]
        await queue.put(coro)

    def is_idle(self, session_id: str) -> bool:
        return not self._busy.get(session_id, False)

    def queue_position(self, session_id: str) -> int:
        entry = self._entries.get(session_id)
        pending = entry[0].qsize() if entry is not None else 0
        busy = self._busy.get(session_id, False)
        return pending + (1 if busy else 0)

    def set_last_usage(self, session_id: str, usage: dict[str, int] | None) -> None:
        if not usage:
            return
        self._last_usage[session_id] = {
            str(k): int(v) for k, v in usage.items() if isinstance(v, (int, float))
        }

    def get_last_usage(self, session_id: str) -> dict[str, int] | None:
        return self._last_usage.get(session_id)

    def get_run_control(self, session_id: str) -> RunControl:
        rc = self._run_controls.get(session_id)
        if rc is None:
            rc = RunControl()
            self._run_controls[session_id] = rc
        return rc

    async def cleanup_session(self, session_id: str) -> None:
        entry = self._entries.pop(session_id, None)
        self._busy.pop(session_id, None)
        self._run_controls.pop(session_id, None)
        self._last_usage.pop(session_id, None)
        if entry is not None:
            _q, task_id = entry
            await self._task_manager.cancel(task_id)

    async def _consume(self, session_id: str, q: asyncio.Queue[Coroutine[Any, Any, None]]) -> None:
        while True:
            coro = await q.get()
            self._busy[session_id] = True
            try:
                await coro
            except Exception:
                logger.exception("error in serial queue consumer for session %s", session_id)
            finally:
                self._busy[session_id] = False
                try:
                    q.task_done()
                except ValueError:
                    pass
