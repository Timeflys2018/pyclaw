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
    registry: FeishuQueueRegistry,
    tm: TaskManager,
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
    registry: FeishuQueueRegistry,
    tm: TaskManager,
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
    registry: FeishuQueueRegistry,
    tm: TaskManager,
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
    registry: FeishuQueueRegistry,
    tm: TaskManager,
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


def test_queue_position_unknown_session_returns_zero(
    registry: FeishuQueueRegistry,
) -> None:
    assert registry.queue_position("never-seen") == 0


@pytest.mark.asyncio
async def test_queue_position_idle_returns_zero(
    registry: FeishuQueueRegistry,
) -> None:
    async def job() -> None:
        return None

    await registry.enqueue("sess-1", job())
    await asyncio.sleep(0.05)
    assert registry.queue_position("sess-1") == 0


@pytest.mark.asyncio
async def test_queue_position_busy_no_pending_returns_one(
    registry: FeishuQueueRegistry,
) -> None:
    running_started = asyncio.Event()
    release = asyncio.Event()

    async def long_job() -> None:
        running_started.set()
        await release.wait()

    await registry.enqueue("sess-1", long_job())
    await running_started.wait()
    try:
        assert registry.queue_position("sess-1") == 1
    finally:
        release.set()
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_queue_position_busy_with_pending_returns_n_plus_one(
    registry: FeishuQueueRegistry,
) -> None:
    running_started = asyncio.Event()
    release = asyncio.Event()

    async def long_job() -> None:
        running_started.set()
        await release.wait()

    async def quick_job() -> None:
        return None

    await registry.enqueue("sess-1", long_job())
    await running_started.wait()
    await registry.enqueue("sess-1", quick_job())
    await registry.enqueue("sess-1", quick_job())
    try:
        assert registry.queue_position("sess-1") == 3
    finally:
        release.set()
        await asyncio.sleep(0.1)


def test_last_usage_unset_returns_none(registry: FeishuQueueRegistry) -> None:
    assert registry.get_last_usage("sess-never-ran") is None


def test_last_usage_set_and_get_roundtrip(registry: FeishuQueueRegistry) -> None:
    usage = {"input": 12000, "output": 1500, "cache_creation": 500, "cache_read": 8000}
    registry.set_last_usage("sess-1", usage)
    assert registry.get_last_usage("sess-1") == usage


def test_last_usage_set_twice_overwrites(registry: FeishuQueueRegistry) -> None:
    registry.set_last_usage(
        "sess-1", {"input": 100, "output": 10, "cache_creation": 0, "cache_read": 0}
    )
    registry.set_last_usage(
        "sess-1", {"input": 200, "output": 20, "cache_creation": 5, "cache_read": 80}
    )
    result = registry.get_last_usage("sess-1")
    assert result is not None
    assert result["input"] == 200
    assert result["output"] == 20


def test_last_usage_scoped_per_session(registry: FeishuQueueRegistry) -> None:
    registry.set_last_usage(
        "sess-A", {"input": 1, "output": 2, "cache_creation": 3, "cache_read": 4}
    )
    assert registry.get_last_usage("sess-B") is None
    assert registry.get_last_usage("sess-A") == {
        "input": 1,
        "output": 2,
        "cache_creation": 3,
        "cache_read": 4,
    }


@pytest.mark.asyncio
async def test_cleanup_session_clears_last_usage(registry: FeishuQueueRegistry) -> None:
    registry.set_last_usage(
        "sess-1", {"input": 1, "output": 2, "cache_creation": 0, "cache_read": 0}
    )
    await registry.cleanup_session("sess-1")
    assert registry.get_last_usage("sess-1") is None
