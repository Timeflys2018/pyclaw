from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

_queues: dict[str, asyncio.Queue[Coroutine[Any, Any, None]]] = {}
_consumers: dict[str, asyncio.Task[None]] = {}


async def enqueue(session_id: str, coro: Coroutine[Any, Any, None]) -> None:
    if session_id not in _queues:
        _queues[session_id] = asyncio.Queue()
        _consumers[session_id] = asyncio.create_task(_consume(session_id))
    await _queues[session_id].put(coro)


def cleanup_session(session_id: str) -> None:
    task = _consumers.pop(session_id, None)
    if task is not None and not task.done():
        task.cancel()
    _queues.pop(session_id, None)


async def _consume(session_id: str) -> None:
    q = _queues[session_id]
    while True:
        coro = await q.get()
        try:
            await coro
        except Exception:
            logger.exception("error in serial queue consumer for session %s", session_id)
        finally:
            q.task_done()
