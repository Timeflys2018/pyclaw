from __future__ import annotations

import asyncio

import pytest

from pyclaw.channels.feishu.queue import FeishuQueueRegistry
from pyclaw.core.agent.run_control import RunControl
from pyclaw.infra.task_manager import TaskManager


@pytest.fixture
def tm() -> TaskManager:
    return TaskManager()


@pytest.fixture
def registry(tm: TaskManager) -> FeishuQueueRegistry:
    return FeishuQueueRegistry(task_manager=tm)


def test_is_idle_default_true_for_unknown_session(registry: FeishuQueueRegistry) -> None:
    assert registry.is_idle("never-seen") is True


def test_get_run_control_lazy_creates_and_idempotent(registry: FeishuQueueRegistry) -> None:
    rc1 = registry.get_run_control("sess-1")
    rc2 = registry.get_run_control("sess-1")
    assert rc1 is rc2
    assert isinstance(rc1, RunControl)


def test_distinct_sessions_get_distinct_run_controls(registry: FeishuQueueRegistry) -> None:
    rc_a = registry.get_run_control("sess-A")
    rc_b = registry.get_run_control("sess-B")
    assert rc_a is not rc_b


@pytest.mark.asyncio
async def test_is_idle_true_when_consumer_blocked_on_q_get(
    registry: FeishuQueueRegistry,
    tm: TaskManager,
) -> None:
    started = asyncio.Event()

    async def first_job() -> None:
        started.set()
        await asyncio.sleep(0)

    await registry.enqueue("sess-block", first_job())
    await started.wait()
    await asyncio.sleep(0.05)

    assert registry.is_idle("sess-block") is True


@pytest.mark.asyncio
async def test_is_idle_false_during_coro_execution(
    registry: FeishuQueueRegistry,
    tm: TaskManager,
) -> None:
    in_coro = asyncio.Event()
    release = asyncio.Event()

    async def long_job() -> None:
        in_coro.set()
        await release.wait()

    await registry.enqueue("sess-busy", long_job())
    await in_coro.wait()

    assert registry.is_idle("sess-busy") is False

    release.set()
    await asyncio.sleep(0.05)

    assert registry.is_idle("sess-busy") is True


@pytest.mark.asyncio
async def test_is_idle_recovers_after_coro_exception(
    registry: FeishuQueueRegistry,
    tm: TaskManager,
) -> None:
    in_coro = asyncio.Event()

    async def boom() -> None:
        in_coro.set()
        raise RuntimeError("boom")

    await registry.enqueue("sess-err", boom())
    await in_coro.wait()
    await asyncio.sleep(0.05)

    assert registry.is_idle("sess-err") is True


@pytest.mark.asyncio
async def test_cleanup_session_clears_run_control_and_busy(
    registry: FeishuQueueRegistry,
    tm: TaskManager,
) -> None:
    rc = registry.get_run_control("sess-X")
    registry._busy["sess-X"] = True
    assert registry.is_idle("sess-X") is False

    await registry.cleanup_session("sess-X")

    assert "sess-X" not in registry._run_controls
    assert "sess-X" not in registry._busy
    assert registry.is_idle("sess-X") is True


def test_is_idle_does_not_inspect_entries(registry: FeishuQueueRegistry) -> None:
    registry._entries["sess-Y"] = (asyncio.Queue(), "fake-task-id")
    assert registry.is_idle("sess-Y") is True
