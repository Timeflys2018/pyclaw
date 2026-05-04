from __future__ import annotations

import asyncio

import pytest

from pyclaw.channels.feishu.queue import FeishuQueueRegistry
from pyclaw.infra.task_manager import TaskManager


@pytest.fixture
def tm() -> TaskManager:
    return TaskManager()


@pytest.fixture
def registry(tm: TaskManager) -> FeishuQueueRegistry:
    return FeishuQueueRegistry(task_manager=tm)


@pytest.mark.asyncio
async def test_enqueue_spawns_consumer_and_executes(
    registry: FeishuQueueRegistry, tm: TaskManager,
) -> None:
    results: list[str] = []

    async def job() -> None:
        results.append("done")

    await registry.enqueue("sess-1", job())
    await asyncio.sleep(0.05)
    assert results == ["done"]
    tasks = tm.list_tasks(category="consumer")
    assert any("feishu-consumer:sess-1" in t.name for t in tasks)


@pytest.mark.asyncio
async def test_enqueue_serial_execution(
    registry: FeishuQueueRegistry,
) -> None:
    order: list[int] = []

    async def job(n: int) -> None:
        await asyncio.sleep(0.01)
        order.append(n)

    await registry.enqueue("sess-1", job(1))
    await registry.enqueue("sess-1", job(2))
    await registry.enqueue("sess-1", job(3))
    await asyncio.sleep(0.15)
    assert order == [1, 2, 3]


@pytest.mark.asyncio
async def test_cleanup_session_cancels_consumer(
    registry: FeishuQueueRegistry, tm: TaskManager,
) -> None:
    gate = asyncio.Event()

    async def block() -> None:
        await gate.wait()

    await registry.enqueue("sess-1", block())
    await asyncio.sleep(0.01)
    tasks_before = tm.list_tasks(category="consumer")
    assert len(tasks_before) == 1

    await registry.cleanup_session("sess-1")
    tasks_after = tm.list_tasks(category="consumer")
    assert len(tasks_after) == 0


@pytest.mark.asyncio
async def test_cleanup_unknown_session_is_noop(
    registry: FeishuQueueRegistry,
) -> None:
    await registry.cleanup_session("nonexistent")


@pytest.mark.asyncio
async def test_consume_task_done_valueerror_guard(
    registry: FeishuQueueRegistry, tm: TaskManager,
) -> None:
    """_consume catches ValueError from task_done() when cancelled mid-get()."""
    started = asyncio.Event()

    async def slow_job() -> None:
        started.set()
        await asyncio.sleep(10)

    await registry.enqueue("sess-1", slow_job())
    await started.wait()

    await registry.cleanup_session("sess-1")
    tasks = tm.list_tasks(category="consumer", include_done=True)
    cancelled = [t for t in tasks if t.state == "cancelled"]
    assert len(cancelled) == 1


@pytest.mark.asyncio
async def test_enqueue_reuses_existing_consumer(
    registry: FeishuQueueRegistry, tm: TaskManager,
) -> None:
    results: list[int] = []

    async def job(n: int) -> None:
        results.append(n)

    await registry.enqueue("sess-1", job(1))
    await registry.enqueue("sess-1", job(2))
    await asyncio.sleep(0.05)
    assert results == [1, 2]
    consumers = tm.list_tasks(category="consumer", include_done=True)
    sess1_consumers = [t for t in consumers if "sess-1" in t.name]
    assert len(sess1_consumers) == 1


@pytest.mark.asyncio
async def test_consume_exception_does_not_kill_consumer(
    registry: FeishuQueueRegistry,
) -> None:
    results: list[str] = []

    async def fail() -> None:
        raise ValueError("boom")

    async def succeed() -> None:
        results.append("ok")

    await registry.enqueue("sess-1", fail())
    await registry.enqueue("sess-1", succeed())
    await asyncio.sleep(0.1)
    assert results == ["ok"]
