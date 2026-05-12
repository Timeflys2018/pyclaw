"""Tests for /tasks slash command (Phase B)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.infra.settings import Settings
from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.tasks import cmd_tasks
from pyclaw.infra.task_manager import TaskManager


@pytest.fixture
async def tm():
    tm = TaskManager()
    yield tm
    await tm.shutdown(grace_s=1.0)


def _ctx(
    *,
    task_manager=None,
    user_id="web:user_x",
    session_key="web:user_x",
    admin_user_ids=None,
    reply=None,
) -> CommandContext:
    deps = MagicMock()
    deps.task_manager = task_manager

    return CommandContext(
        session_id="s1",
        session_key=session_key,
        workspace_id="ws",
        user_id=user_id,
        channel="web",
        deps=deps,
        session_router=MagicMock(),
        workspace_base=Path("/tmp"),
        reply=reply or AsyncMock(),
        dispatch_user_message=AsyncMock(),
        raw={},
        settings=Settings(),
        admin_user_ids=admin_user_ids or [],
    )


@pytest.mark.asyncio
async def test_tasks_usage_without_args() -> None:
    reply = AsyncMock()
    ctx = _ctx(reply=reply)
    await cmd_tasks("", ctx)
    reply.assert_awaited_once()
    assert "用法" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_tasks_list_empty(tm: TaskManager) -> None:
    reply = AsyncMock()
    ctx = _ctx(task_manager=tm, reply=reply)
    await cmd_tasks("list", ctx)
    reply.assert_awaited_once()
    assert "没有运行中" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_tasks_list_shows_owner_tasks(tm: TaskManager) -> None:
    async def hang():
        await asyncio.sleep(10)

    tm.spawn("mine", hang(), owner="web:user_x")
    tm.spawn("other", hang(), owner="web:user_y")

    reply = AsyncMock()
    ctx = _ctx(task_manager=tm, session_key="web:user_x", reply=reply)
    await cmd_tasks("list", ctx)

    msg = reply.await_args[0][0]
    assert "mine" in msg
    assert "other" not in msg


@pytest.mark.asyncio
async def test_tasks_list_all_requires_admin(tm: TaskManager) -> None:
    async def hang():
        await asyncio.sleep(10)

    tm.spawn("sys", hang())

    reply = AsyncMock()
    ctx = _ctx(task_manager=tm, admin_user_ids=[], reply=reply)
    await cmd_tasks("list --all", ctx)
    assert "--all 仅管理员可用" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_tasks_list_all_admin_sees_system_tasks(tm: TaskManager) -> None:
    async def hang():
        await asyncio.sleep(10)

    tm.spawn("sys", hang())
    tm.spawn("user", hang(), owner="web:user_y")

    reply = AsyncMock()
    ctx = _ctx(
        task_manager=tm, user_id="admin_user", admin_user_ids=["admin_user"],
        reply=reply,
    )
    await cmd_tasks("list --all", ctx)
    msg = reply.await_args[0][0]
    assert "sys" in msg
    assert "user" in msg


@pytest.mark.asyncio
async def test_tasks_kill_non_admin_rejected(tm: TaskManager) -> None:
    reply = AsyncMock()
    ctx = _ctx(task_manager=tm, admin_user_ids=[], reply=reply)
    await cmd_tasks("kill t000001", ctx)
    assert "仅管理员可用" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_tasks_kill_nonexistent(tm: TaskManager) -> None:
    reply = AsyncMock()
    ctx = _ctx(
        task_manager=tm, user_id="admin", admin_user_ids=["admin"], reply=reply,
    )
    await cmd_tasks("kill missing_id --confirm", ctx)
    assert "任务不存在" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_tasks_kill_preview_first(tm: TaskManager) -> None:
    async def hang():
        await asyncio.sleep(10)

    tid = tm.spawn("safe", hang(), category="generic", owner="web:user_y")

    reply = AsyncMock()
    ctx = _ctx(
        task_manager=tm, user_id="admin", admin_user_ids=["admin"], reply=reply,
    )
    await cmd_tasks(f"kill {tid}", ctx)
    msg = reply.await_args[0][0]
    assert "将终止" in msg
    assert "--confirm" in msg


@pytest.mark.asyncio
async def test_tasks_kill_protected_category_rejected(tm: TaskManager) -> None:
    async def hang():
        await asyncio.sleep(10)

    tid = tm.spawn("hb", hang(), category="heartbeat")

    reply = AsyncMock()
    ctx = _ctx(
        task_manager=tm, user_id="admin", admin_user_ids=["admin"], reply=reply,
    )
    await cmd_tasks(f"kill {tid} --confirm", ctx)
    msg = reply.await_args[0][0]
    assert "拒绝" in msg
    assert "heartbeat" in msg


@pytest.mark.asyncio
async def test_tasks_kill_safe_category_cancels(tm: TaskManager) -> None:
    async def hang():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return

    tid = tm.spawn("ok", hang(), category="generic")
    await asyncio.sleep(0)

    reply = AsyncMock()
    ctx = _ctx(
        task_manager=tm, user_id="admin", admin_user_ids=["admin"], reply=reply,
    )
    await cmd_tasks(f"kill {tid} --confirm", ctx)
    msg = reply.await_args[0][0]
    assert "已取消" in msg


@pytest.mark.asyncio
async def test_tasks_list_handles_task_manager_none() -> None:
    reply = AsyncMock()
    ctx = _ctx(task_manager=None, reply=reply)
    await cmd_tasks("list", ctx)
    assert "TaskManager 未初始化" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_tasks_kill_handles_task_manager_none() -> None:
    reply = AsyncMock()
    ctx = _ctx(
        task_manager=None, user_id="admin", admin_user_ids=["admin"], reply=reply,
    )
    await cmd_tasks("kill t000001 --confirm", ctx)
    assert "TaskManager 未初始化" in reply.await_args[0][0]
