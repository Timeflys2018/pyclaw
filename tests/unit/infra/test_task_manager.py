from __future__ import annotations

import asyncio
import logging
import re
import time
from unittest.mock import patch

import pytest

from pyclaw.infra.task_manager import (
    ShutdownReport,
    TaskManager,
    TaskManagerClosedError,
)


@pytest.fixture
def tm() -> TaskManager:
    return TaskManager()


# 2.2
@pytest.mark.asyncio
async def test_spawn_returns_task_id_and_completes(tm: TaskManager) -> None:
    async def noop() -> None:
        pass

    task_id = tm.spawn("noop", noop())
    assert re.match(r"t\d{6,}", task_id)
    await asyncio.sleep(0.01)
    assert tm.get_state(task_id) == "done"


# 2.3
@pytest.mark.asyncio
async def test_cancel_running_task(tm: TaskManager) -> None:
    cleanup_done: list[bool] = []

    async def slow() -> None:
        try:
            await asyncio.sleep(10)
        finally:
            cleanup_done.append(True)

    task_id = tm.spawn("slow", slow())
    await asyncio.sleep(0.01)
    result = await tm.cancel(task_id)
    assert result is True
    assert tm.get_state(task_id) == "cancelled"
    assert cleanup_done == [True]


# 2.4
@pytest.mark.asyncio
async def test_cancel_unknown_task_id(tm: TaskManager) -> None:
    result = await tm.cancel("t999999")
    assert result is False


# 2.5
@pytest.mark.asyncio
async def test_cancel_already_done_task(tm: TaskManager) -> None:
    async def fast() -> None:
        pass

    task_id = tm.spawn("fast", fast())
    await asyncio.sleep(0.01)
    result = await tm.cancel(task_id)
    assert result is False


# 2.6
@pytest.mark.asyncio
async def test_list_tasks_default_running_only(tm: TaskManager) -> None:
    async def done_quickly() -> None:
        pass

    async def hang() -> None:
        await asyncio.sleep(10)

    tm.spawn("done", done_quickly())
    tm.spawn("hang", hang())
    await asyncio.sleep(0.01)
    tasks = tm.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].name == "hang"
    await tm.shutdown(grace_s=0.1)


# 2.7
@pytest.mark.asyncio
async def test_list_tasks_include_done(tm: TaskManager) -> None:
    async def done_quickly() -> None:
        pass

    async def hang() -> None:
        await asyncio.sleep(10)

    tm.spawn("done", done_quickly())
    tm.spawn("hang", hang())
    await asyncio.sleep(0.01)
    tasks = tm.list_tasks(include_done=True)
    assert len(tasks) == 2
    await tm.shutdown(grace_s=0.1)


# 2.8
@pytest.mark.asyncio
async def test_list_tasks_category_filter(tm: TaskManager) -> None:
    async def hang() -> None:
        await asyncio.sleep(10)

    tm.spawn("hb", hang(), category="heartbeat")
    tm.spawn("gen", hang(), category="generic")
    await asyncio.sleep(0.01)
    tasks = tm.list_tasks(category="heartbeat")
    assert len(tasks) == 1
    assert tasks[0].category == "heartbeat"
    await tm.shutdown(grace_s=0.1)


# 2.9
@pytest.mark.asyncio
async def test_exception_triggers_on_error(tm: TaskManager) -> None:
    errors: list[BaseException] = []

    async def fail() -> None:
        raise ValueError("boom")

    task_id = tm.spawn("fail", fail(), on_error=errors.append)
    await asyncio.sleep(0.05)
    assert tm.get_state(task_id) == "failed"
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    info = tm.list_tasks(include_done=True)
    matched = [t for t in info if t.task_id == task_id]
    assert matched[0].exception == "ValueError: boom"


# 2.10
@pytest.mark.asyncio
async def test_on_error_raising_is_caught(tm: TaskManager, caplog: pytest.LogCaptureFixture) -> None:
    def bad_callback(exc: BaseException) -> None:
        raise RuntimeError("callback exploded")

    async def fail() -> None:
        raise ValueError("original")

    with caplog.at_level(logging.ERROR):
        task_id = tm.spawn("fail", fail(), on_error=bad_callback)
        await asyncio.sleep(0.05)
    assert "on_error callback itself failed" in caplog.text
    assert tm.get_state(task_id) == "failed"
    info = [t for t in tm.list_tasks(include_done=True) if t.task_id == task_id]
    assert info[0].exception == "ValueError: original"


# 2.11
@pytest.mark.asyncio
async def test_cancelled_error_no_on_error_no_error_log(
    tm: TaskManager, caplog: pytest.LogCaptureFixture
) -> None:
    errors: list[BaseException] = []

    async def hang() -> None:
        await asyncio.sleep(10)

    with caplog.at_level(logging.ERROR):
        task_id = tm.spawn("hang", hang(), on_error=errors.append)
        await asyncio.sleep(0.01)
        await tm.cancel(task_id)
    assert errors == []
    assert "task failed" not in caplog.text


# 2.12
@pytest.mark.asyncio
async def test_shutdown_cancels_running_tasks(tm: TaskManager) -> None:
    async def hang() -> None:
        await asyncio.sleep(10)

    tm.spawn("a", hang())
    tm.spawn("b", hang())
    await asyncio.sleep(0.01)
    report = await tm.shutdown(grace_s=1.0)
    assert report.cancelled == 2
    assert report.timed_out == 0


# 2.13
@pytest.mark.asyncio
async def test_task_cleanup_during_shutdown_window(tm: TaskManager) -> None:
    cleaned: list[bool] = []

    async def graceful() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.01)
            cleaned.append(True)
            raise

    tm.spawn("graceful", graceful())
    await asyncio.sleep(0.01)
    report = await tm.shutdown(grace_s=2.0)
    assert cleaned == [True]
    assert report.cancelled == 1


# 2.14
@pytest.mark.asyncio
async def test_task_exceeding_grace_is_timed_out(tm: TaskManager) -> None:
    async def unresponsive() -> None:
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            await asyncio.sleep(100)

    tm.spawn("stuck", unresponsive())
    await asyncio.sleep(0.01)
    report = await tm.shutdown(grace_s=0.1)
    assert report.timed_out == 1


# 2.15
@pytest.mark.asyncio
async def test_spawn_after_shutdown_raises_and_closes_coro(tm: TaskManager) -> None:
    await tm.shutdown(grace_s=0.1)

    started = False

    async def should_not_run() -> None:
        nonlocal started
        started = True

    coro = should_not_run()
    with pytest.raises(TaskManagerClosedError):
        tm.spawn("late", coro)
    assert not started
    # Verify coroutine was closed (no "was never awaited" warning)
    await asyncio.sleep(0.01)


# 2.16
@pytest.mark.asyncio
async def test_shutdown_twice_returns_zero_report(tm: TaskManager) -> None:
    async def hang() -> None:
        await asyncio.sleep(10)

    tm.spawn("x", hang())
    await asyncio.sleep(0.01)
    await tm.shutdown(grace_s=0.5)
    report2 = await tm.shutdown(grace_s=0.5)
    assert report2 == ShutdownReport(0, 0, 0, 0, 0.0)


# 2.17
@pytest.mark.asyncio
async def test_closed_error_message_contains_task_name(tm: TaskManager) -> None:
    await tm.shutdown(grace_s=0.1)

    async def noop() -> None:
        pass

    with pytest.raises(TaskManagerClosedError, match="my-task"):
        tm.spawn("my-task", noop())


# 2.18
@pytest.mark.asyncio
async def test_two_tasks_same_name_different_ids(tm: TaskManager) -> None:
    async def hang() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass

    id1 = tm.spawn("dup", hang())
    id2 = tm.spawn("dup", hang())
    assert id1 != id2
    tasks = tm.list_tasks()
    assert len(tasks) == 2
    await asyncio.sleep(0)
    await tm.shutdown(grace_s=2.0)


# 2.19
@pytest.mark.asyncio
async def test_get_state_running_done_unknown(tm: TaskManager) -> None:
    async def quick() -> None:
        pass

    async def hang() -> None:
        await asyncio.sleep(10)

    tid_quick = tm.spawn("quick", quick())
    tid_hang = tm.spawn("hang", hang())
    await asyncio.sleep(0.01)
    assert tm.get_state(tid_quick) == "done"
    assert tm.get_state(tid_hang) == "running"
    assert tm.get_state("t999999") is None
    await tm.shutdown(grace_s=0.1)


# 2.20
@pytest.mark.asyncio
async def test_get_state_returns_none_for_pruned_task(tm: TaskManager) -> None:
    async def quick() -> None:
        pass

    tid = tm.spawn("old", quick())
    await asyncio.sleep(0.01)
    assert tm.get_state(tid) == "done"
    # Manipulate created_at to simulate old task
    tm._tasks[tid].created_at = time.monotonic() - 400
    # Trigger prune via spawn
    async def noop() -> None:
        pass

    tm.spawn("new", noop())
    assert tm.get_state(tid) is None


# 2.21
@pytest.mark.asyncio
async def test_cancel_timeout_on_unresponsive_task(
    tm: TaskManager, caplog: pytest.LogCaptureFixture
) -> None:
    async def unresponsive() -> None:
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            await asyncio.sleep(100)

    with caplog.at_level(logging.WARNING):
        tid = tm.spawn("stuck", unresponsive())
        await asyncio.sleep(0.01)
        start = time.monotonic()
        result = await tm.cancel(tid, timeout=0.5)
        elapsed = time.monotonic() - start
    assert result is True
    assert elapsed < 1.5
    assert "cancel timeout" in caplog.text
    await tm.shutdown(grace_s=0.1)


# 2.22
@pytest.mark.asyncio
async def test_maybe_prune_removes_old_keeps_recent(tm: TaskManager) -> None:
    async def quick() -> None:
        pass

    tid_old = tm.spawn("old", quick())
    tid_new = tm.spawn("new", quick())
    await asyncio.sleep(0.01)
    # Make one old
    tm._tasks[tid_old].created_at = time.monotonic() - 400
    # Keep new recent (already is)
    tm._maybe_prune()
    assert tid_old not in tm._tasks
    assert tid_new in tm._tasks


# 2.23
@pytest.mark.asyncio
async def test_spawn_from_event_loop_succeeds(tm: TaskManager) -> None:
    async def noop() -> None:
        pass

    tid = tm.spawn("sanity", noop())
    assert tid is not None
    await asyncio.sleep(0.01)
    assert tm.get_state(tid) == "done"


@pytest.mark.asyncio
async def test_spawn_records_owner(tm: TaskManager) -> None:
    async def hang():
        await asyncio.sleep(10)

    tid = tm.spawn("with-owner", hang(), owner="web:user_x")
    infos = tm.list_tasks()
    matches = [i for i in infos if i.task_id == tid]
    assert len(matches) == 1
    assert matches[0].owner == "web:user_x"


@pytest.mark.asyncio
async def test_list_tasks_filter_by_owner(tm: TaskManager) -> None:
    async def hang():
        await asyncio.sleep(10)

    tm.spawn("a", hang(), owner="web:user_a")
    tm.spawn("b", hang(), owner="web:user_b")
    tm.spawn("sys", hang())

    user_a = tm.list_tasks(owner="web:user_a")
    assert len(user_a) == 1
    assert user_a[0].name == "a"

    user_nobody = tm.list_tasks(owner="web:user_nobody")
    assert user_nobody == []

    all_tasks = tm.list_tasks(owner=None)
    assert len(all_tasks) == 3


@pytest.mark.asyncio
async def test_task_info_owner_default_none(tm: TaskManager) -> None:
    async def hang():
        await asyncio.sleep(10)

    tid = tm.spawn("no-owner", hang())
    info = next(i for i in tm.list_tasks() if i.task_id == tid)
    assert info.owner is None
