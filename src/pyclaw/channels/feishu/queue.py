from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from pyclaw.infra.task_manager import TaskManager

logger = logging.getLogger(__name__)


class FeishuQueueRegistry:
    def __init__(self, task_manager: TaskManager) -> None:
        self._task_manager = task_manager
        self._entries: dict[str, tuple[asyncio.Queue[Coroutine[Any, Any, None]], str]] = {}

    async def enqueue(self, session_id: str, coro: Coroutine[Any, Any, None]) -> None:
        if session_id not in self._entries:
            q: asyncio.Queue[Coroutine[Any, Any, None]] = asyncio.Queue()
            task_id = self._task_manager.spawn(
                f"feishu-consumer:{session_id}",
                self._consume(session_id, q),
                category="consumer",
            )
            self._entries[session_id] = (q, task_id)
        queue, _tid = self._entries[session_id]
        await queue.put(coro)

    async def cleanup_session(self, session_id: str) -> None:
        entry = self._entries.pop(session_id, None)
        if entry is not None:
            _q, task_id = entry
            await self._task_manager.cancel(task_id)

    async def _consume(self, session_id: str, q: asyncio.Queue[Coroutine[Any, Any, None]]) -> None:
        while True:
            coro = await q.get()
            try:
                await coro
            except Exception:
                logger.exception("error in serial queue consumer for session %s", session_id)
            finally:
                try:
                    q.task_done()
                except ValueError:
                    pass
