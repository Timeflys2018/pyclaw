"""Tests for /curator slash command (Phase D)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.infra.settings import Settings
from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.curator import cmd_curator
from pyclaw.core.curator_admin import (
    ArchivedSopRow,
    RestoreResult,
    SopRow,
)


def _ctx(
    *,
    deps=None,
    reply=None,
    redis_client=None,
    session_key="test:user_x",
    session_id="s1",
    channel="web",
) -> CommandContext:
    if deps is None:
        deps = MagicMock()
        deps.task_manager = None
        deps.lock_manager = None
        deps.settings = None
        deps.llm = None

    return CommandContext(
        session_id=session_id,
        session_key=session_key,
        workspace_id="ws",
        user_id="user_x",
        channel=channel,
        deps=deps,
        session_router=MagicMock(),
        workspace_base=Path("/tmp/ws"),
        reply=reply or AsyncMock(),
        dispatch_user_message=AsyncMock(),
        raw={},
        settings=Settings(),
        redis_client=redis_client,
    )


def _mock_settings(tmp_path):
    s = MagicMock()
    s.memory.base_dir = str(tmp_path)
    s.evolution.curator.stale_after_days = 30
    s.evolution.curator.promotion_min_use_count = 5
    s.evolution.curator.promotion_min_days = 7
    return s


@pytest.mark.asyncio
async def test_curator_usage_without_args() -> None:
    reply = AsyncMock()
    await cmd_curator("", _ctx(reply=reply))
    assert "用法" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_curator_unknown_subcommand() -> None:
    reply = AsyncMock()
    await cmd_curator("unknown_thing", _ctx(reply=reply))
    assert "未知子命令" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_curator_list_requires_flag(tmp_path) -> None:
    reply = AsyncMock()
    deps = MagicMock()
    deps.settings = _mock_settings(tmp_path)
    await cmd_curator("list", _ctx(deps=deps, reply=reply))
    assert "--auto" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_curator_list_auto_empty(tmp_path) -> None:
    reply = AsyncMock()
    deps = MagicMock()
    deps.settings = _mock_settings(tmp_path)

    with patch("pyclaw.core.commands.curator.list_auto_sops", return_value=[]):
        await cmd_curator("list --auto", _ctx(deps=deps, reply=reply))
    assert "无活跃自动 SOP" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_curator_list_auto_with_results(tmp_path) -> None:
    reply = AsyncMock()
    deps = MagicMock()
    deps.settings = _mock_settings(tmp_path)

    rows = [
        SopRow(
            entry_id="abcdef1234", session_key="test:user_x",
            content="some auto SOP content here", use_count=3,
            last_used_at=time.time(),
        ),
    ]
    with patch("pyclaw.core.commands.curator.list_auto_sops", return_value=rows):
        await cmd_curator("list --auto", _ctx(deps=deps, reply=reply))

    msg = reply.await_args[0][0]
    assert "abcdef12" in msg
    assert "auto SOP content" in msg


@pytest.mark.asyncio
async def test_curator_list_stale(tmp_path) -> None:
    reply = AsyncMock()
    deps = MagicMock()
    deps.settings = _mock_settings(tmp_path)

    with patch("pyclaw.core.commands.curator.list_stale_sops", return_value=[]):
        await cmd_curator("list --stale", _ctx(deps=deps, reply=reply))
    assert "无过期" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_curator_list_archived_with_rows(tmp_path) -> None:
    reply = AsyncMock()
    deps = MagicMock()
    deps.settings = _mock_settings(tmp_path)

    rows = [
        ArchivedSopRow(
            entry_id="xxxxxx01", session_key="test:user_x",
            content="archived content", archived_at=time.time(),
            archive_reason="curator:90d",
        ),
    ]
    with patch("pyclaw.core.commands.curator.list_archived_sops", return_value=rows):
        await cmd_curator("list --archived", _ctx(deps=deps, reply=reply))

    msg = reply.await_args[0][0]
    assert "xxxxxx01" in msg
    assert "curator:90d" in msg


@pytest.mark.asyncio
async def test_curator_preview_empty(tmp_path) -> None:
    reply = AsyncMock()
    deps = MagicMock()
    deps.settings = _mock_settings(tmp_path)

    with patch("pyclaw.core.commands.curator.preview_graduation", return_value=[]):
        await cmd_curator("preview", _ctx(deps=deps, reply=reply))
    assert "无符合毕业" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_curator_restore_usage() -> None:
    reply = AsyncMock()
    await cmd_curator("restore", _ctx(reply=reply))
    assert "用法" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_curator_restore_preview_first(tmp_path) -> None:
    reply = AsyncMock()
    deps = MagicMock()
    deps.settings = _mock_settings(tmp_path)

    await cmd_curator("restore abc123", _ctx(deps=deps, reply=reply))
    msg = reply.await_args[0][0]
    assert "将恢复" in msg
    assert "--confirm" in msg


@pytest.mark.asyncio
async def test_curator_restore_confirmed(tmp_path) -> None:
    reply = AsyncMock()
    deps = MagicMock()
    deps.settings = _mock_settings(tmp_path)

    with patch(
        "pyclaw.core.commands.curator.restore_sop",
        return_value=RestoreResult(count=1, dbs_affected=1),
    ):
        await cmd_curator("restore abc123 --confirm", _ctx(deps=deps, reply=reply))

    assert "已恢复" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_curator_restore_not_found(tmp_path) -> None:
    reply = AsyncMock()
    deps = MagicMock()
    deps.settings = _mock_settings(tmp_path)

    with patch(
        "pyclaw.core.commands.curator.restore_sop",
        return_value=RestoreResult(count=0, dbs_affected=0),
    ):
        await cmd_curator("restore abc123 --confirm", _ctx(deps=deps, reply=reply))
    assert "未找到" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_curator_review_status_no_history() -> None:
    reply = AsyncMock()
    redis_client = AsyncMock()
    redis_client.get = AsyncMock(return_value=None)
    await cmd_curator("review-status", _ctx(reply=reply, redis_client=redis_client))
    assert "尚无" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_curator_review_status_with_ts() -> None:
    reply = AsyncMock()
    redis_client = AsyncMock()
    redis_client.get = AsyncMock(return_value="1700000000")
    await cmd_curator("review-status", _ctx(reply=reply, redis_client=redis_client))
    assert "上次 LLM review" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_curator_review_trigger_needs_task_manager(tmp_path) -> None:
    reply = AsyncMock()
    deps = MagicMock()
    deps.task_manager = None
    deps.lock_manager = MagicMock()
    deps.settings = _mock_settings(tmp_path)

    await cmd_curator("review-trigger --confirm", _ctx(deps=deps, reply=reply))
    assert "TaskManager 未初始化" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_curator_review_trigger_needs_lock_manager(tmp_path) -> None:
    reply = AsyncMock()
    deps = MagicMock()
    deps.task_manager = MagicMock()
    deps.lock_manager = None
    deps.settings = _mock_settings(tmp_path)

    await cmd_curator("review-trigger --confirm", _ctx(deps=deps, reply=reply))
    assert "LockManager 未初始化" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_curator_review_trigger_preview_without_confirm(tmp_path) -> None:
    reply = AsyncMock()
    deps = MagicMock()
    deps.task_manager = MagicMock()
    deps.lock_manager = MagicMock()
    deps.settings = _mock_settings(tmp_path)

    await cmd_curator("review-trigger", _ctx(deps=deps, reply=reply))
    msg = reply.await_args[0][0]
    assert "--confirm" in msg
    assert "手动触发" in msg or "LLM tokens" in msg


@pytest.mark.asyncio
async def test_curator_review_trigger_spawns_with_correct_params(tmp_path) -> None:
    reply = AsyncMock()
    deps = MagicMock()

    spawned_coros = []

    def _capture_spawn(name, coro, **kwargs):
        spawned_coros.append((name, coro, kwargs))
        coro.close()
        return "t000042"

    deps.task_manager = MagicMock()
    deps.task_manager.spawn = MagicMock(side_effect=_capture_spawn)
    deps.lock_manager = MagicMock()
    deps.llm = MagicMock()
    deps.settings = _mock_settings(tmp_path)

    await cmd_curator(
        "review-trigger --confirm",
        _ctx(deps=deps, reply=reply, session_key="test:user_x"),
    )

    assert len(spawned_coros) == 1
    name, _coro, kwargs = spawned_coros[0]
    assert "curator-review-manual" in name
    assert "test:user_x" in name
    assert kwargs.get("category") == "curator"
    assert kwargs.get("owner") == "test:user_x"

    assert "t000042" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_curator_review_trigger_passes_force_review_true(tmp_path) -> None:
    """Regression guard: force_review=True must reach run_curator_cycle, else interval gate silently skips."""
    reply = AsyncMock()
    deps = MagicMock()

    captured_kwargs: dict = {}

    async def _capture_cycle(**kwargs):
        captured_kwargs.update(kwargs)
        from pyclaw.core.curator import CycleReport

        return CycleReport(acquired=True)

    captured_coro: dict = {}

    def _capture_spawn(name, coro, **kwargs):
        captured_coro["coro"] = coro
        return "tX"

    deps.task_manager = MagicMock()
    deps.task_manager.spawn = MagicMock(side_effect=_capture_spawn)
    deps.lock_manager = MagicMock()
    deps.llm = MagicMock()
    deps.settings = _mock_settings(tmp_path)

    with patch("pyclaw.core.curator.run_curator_cycle", new=_capture_cycle):
        await cmd_curator(
            "review-trigger --confirm",
            _ctx(deps=deps, reply=reply, session_key="test:user_x"),
        )
        await captured_coro["coro"]

    assert captured_kwargs.get("force_review") is True, (
        "force_review MUST be True for manual trigger to bypass interval gate"
    )
    assert captured_kwargs.get("mode") == "review_only"
    assert captured_kwargs.get("task_manager") is deps.task_manager, (
        "task_manager MUST be passed for heartbeat spawn"
    )
    assert captured_kwargs.get("lock_manager") is deps.lock_manager
    assert captured_kwargs.get("owner_label") == "manual:test:user_x"
