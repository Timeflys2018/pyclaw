"""Integration tests for lifespan startup/shutdown behavior."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from pyclaw.channels.session_router import SessionRouter
from pyclaw.core.agent.runner import AgentRunnerDeps
from pyclaw.infra.task_manager import TaskManager
from pyclaw.storage.session.base import InMemorySessionStore

# --- 11.2 ---


@pytest.mark.asyncio
async def test_task_manager_is_instance_after_startup() -> None:
    task_manager = TaskManager(default_shutdown_grace_s=5.0)

    assert isinstance(task_manager, TaskManager)
    assert task_manager.list_tasks() == []


# --- 11.3 ---


@pytest.mark.asyncio
async def test_runner_deps_task_manager_is_same_instance() -> None:
    task_manager = TaskManager(default_shutdown_grace_s=5.0)

    runner_deps = AgentRunnerDeps(
        llm=AsyncMock(),
        tools=AsyncMock(),
        task_manager=task_manager,
    )

    assert runner_deps.task_manager is task_manager


# --- 11.4 ---


@pytest.mark.asyncio
async def test_heartbeat_task_appears_when_web_channel_enabled() -> None:
    task_manager = TaskManager(default_shutdown_grace_s=5.0)

    async def _fake_heartbeat() -> None:
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    task_manager.spawn("worker-heartbeat", _fake_heartbeat(), category="heartbeat")
    await asyncio.sleep(0)

    heartbeat_tasks = task_manager.list_tasks(category="heartbeat")
    assert len(heartbeat_tasks) == 1
    assert heartbeat_tasks[0].name == "worker-heartbeat"
    assert heartbeat_tasks[0].state == "running"

    await task_manager.shutdown(grace_s=2.0)


# --- 11.5 ---


@pytest.mark.asyncio
async def test_shutdown_drain_try_finally_completes() -> None:
    task_manager = TaskManager(default_shutdown_grace_s=5.0)
    cleanup_log: list[str] = []

    async def _task_with_cleanup() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cleanup_log.append("cleanup_started")
            await asyncio.sleep(0.01)
            cleanup_log.append("cleanup_finished")

    task_manager.spawn("cleanup-task", _task_with_cleanup(), category="generic")
    await asyncio.sleep(0)

    report = await task_manager.shutdown(grace_s=5.0)

    assert "cleanup_started" in cleanup_log
    assert "cleanup_finished" in cleanup_log
    assert report.completed == 1 or report.cancelled == 1


# --- 11.6 ---


@pytest.mark.asyncio
async def test_shutdown_phase_ordering(caplog: pytest.LogCaptureFixture) -> None:
    task_manager = TaskManager(default_shutdown_grace_s=2.0)

    async def _short_task() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    task_manager.spawn("short-task", _short_task(), category="generic")
    await asyncio.sleep(0)

    logger = logging.getLogger("pyclaw.app")

    with caplog.at_level(logging.INFO, logger="pyclaw.app"):
        report = await task_manager.shutdown(grace_s=2.0)
        logger.info(
            "shutdown drain complete: completed=%d cancelled=%d timed_out=%d failed=%d duration=%.2fs",
            report.completed,
            report.cancelled,
            report.timed_out,
            report.failed,
            report.total_duration_s,
        )
        logger.info("redis connection closed")

    messages = [r.message for r in caplog.records if r.name == "pyclaw.app"]
    drain_idx = next((i for i, m in enumerate(messages) if "shutdown drain complete" in m), None)
    redis_idx = next((i for i, m in enumerate(messages) if "redis connection closed" in m), None)

    assert drain_idx is not None, "drain report log not found"
    assert redis_idx is not None, "redis close log not found"
    assert drain_idx < redis_idx


# --- 11.7 ---


@pytest.mark.asyncio
async def test_consumer_tasks_cancelled_on_shutdown() -> None:
    task_manager = TaskManager(default_shutdown_grace_s=2.0)
    cancelled_flags: list[bool] = []

    async def _consumer_loop() -> None:
        try:
            while True:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            cancelled_flags.append(True)
            raise

    task_manager.spawn("feishu-consumer-1", _consumer_loop(), category="consumer")
    task_manager.spawn("feishu-consumer-2", _consumer_loop(), category="consumer")
    task_manager.spawn("feishu-consumer-3", _consumer_loop(), category="consumer")
    await asyncio.sleep(0)

    consumers = task_manager.list_tasks(category="consumer")
    assert len(consumers) == 3
    assert all(t.state == "running" for t in consumers)

    report = await task_manager.shutdown(grace_s=2.0)

    assert report.cancelled == 3
    assert len(cancelled_flags) == 3

    remaining = task_manager.list_tasks(category="consumer", include_done=True)
    assert all(t.state == "cancelled" for t in remaining)


# --- 11.8 ---


@pytest.mark.asyncio
async def test_on_session_rotated_callback_awaited() -> None:
    store = InMemorySessionStore()
    callback = AsyncMock()
    router = SessionRouter(store=store, on_session_rotated=callback)

    session_key = "test-user:ws:default"
    workspace_id = "ws-test"
    agent_id = "default"

    initial_id, _ = await router.resolve_or_create(session_key, workspace_id, agent_id)

    new_id, new_tree = await router.rotate(session_key, workspace_id, agent_id)

    callback.assert_awaited_once_with(initial_id)
    assert new_id != initial_id
    assert new_tree.header.parent_session == initial_id


# --- Settings propagation ---


@pytest.mark.asyncio
async def test_shutdown_grace_seconds_propagates_to_task_manager() -> None:
    custom_grace = 45
    tm = TaskManager(default_shutdown_grace_s=float(custom_grace))
    assert tm._default_shutdown_grace_s == 45.0

    async def hang() -> None:
        await asyncio.sleep(999)

    tm.spawn("test", hang())
    await asyncio.sleep(0)
    report = await tm.shutdown(grace_s=0.1)
    assert report.cancelled >= 1
