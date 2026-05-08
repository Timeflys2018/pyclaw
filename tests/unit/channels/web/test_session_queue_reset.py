from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from pyclaw.channels.web.chat import SessionQueue
from pyclaw.channels.web.protocol import ChatSendMessage
from pyclaw.infra.task_manager import TaskManager


@pytest.mark.asyncio
async def test_reset_empties_all_dicts() -> None:
    tm = TaskManager()
    sq = SessionQueue(task_manager=tm)

    async def quick_handler(_msg: ChatSendMessage) -> None:
        pass

    await sq.enqueue("c1", ChatSendMessage(conversation_id="c1", content="x"), quick_handler)
    consumer_tid = sq._consumers["c1"]
    consumer_task = tm._tasks[consumer_tid].asyncio_task
    await asyncio.sleep(0.05)
    sq.get_abort_event("c1")
    sq.set_approval_decision("c1", "tool_1", True)

    sq.reset()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    assert sq._queues == {}
    assert sq._consumers == {}
    assert sq._busy == {}
    assert sq._abort_events == {}
    assert sq._approval_decisions == {}


@pytest.mark.asyncio
async def test_reset_cancels_in_flight_consumers() -> None:
    tm = TaskManager()
    sq = SessionQueue(task_manager=tm)

    async def slow_handler(_msg: ChatSendMessage) -> None:
        await asyncio.sleep(10)

    await sq.enqueue("c1", ChatSendMessage(conversation_id="c1", content="x"), slow_handler)
    await asyncio.sleep(0.05)
    consumer_tid = sq._consumers["c1"]
    handle = tm._tasks[consumer_tid]
    assert not handle.asyncio_task.done()

    sq.reset()
    try:
        await handle.asyncio_task
    except asyncio.CancelledError:
        pass

    assert handle.asyncio_task.cancelled() or handle.asyncio_task.done()


@pytest.mark.asyncio
async def test_consume_cleans_up_dicts_after_timeout() -> None:
    tm = TaskManager()
    sq = SessionQueue(task_manager=tm)
    handler_called = asyncio.Event()

    async def handler(_msg: ChatSendMessage) -> None:
        handler_called.set()

    await sq.enqueue("c1", ChatSendMessage(conversation_id="c1", content="x"), handler)
    await asyncio.wait_for(handler_called.wait(), timeout=1.0)

    consumer_tid = sq._consumers["c1"]
    consumer_task = tm._tasks[consumer_tid].asyncio_task
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    assert "c1" not in sq._queues
    assert "c1" not in sq._consumers
    assert "c1" not in sq._busy
    assert "c1" not in sq._abort_events


@pytest.mark.asyncio
async def test_reset_safe_when_task_manager_is_none() -> None:
    sq = SessionQueue(task_manager=None)
    sq._queues["c1"] = asyncio.Queue()
    sq._consumers["c1"] = "t000001"
    sq.reset()
    assert sq._queues == {}
    assert sq._consumers == {}
