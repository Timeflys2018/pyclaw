from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.feishu.handler import FeishuContext, handle_stop_feishu
from pyclaw.channels.feishu.queue import FeishuQueueRegistry
from pyclaw.channels.session_router import SessionRouter
from pyclaw.infra.settings import FeishuSettings, Settings
from pyclaw.infra.task_manager import TaskManager
from pyclaw.storage.session.base import InMemorySessionStore


def _make_ctx(queue_registry: FeishuQueueRegistry) -> FeishuContext:
    store = InMemorySessionStore()
    settings = FeishuSettings(enabled=True, app_id="cli_x", app_secret="s")
    feishu_client = MagicMock()
    feishu_client.reply_text = AsyncMock(return_value=None)
    deps = MagicMock()
    deps.session_store = store
    return FeishuContext(
        settings=settings,
        settings_full=Settings(),
        feishu_client=feishu_client,
        deps=deps,
        dedup=MagicMock(),
        workspace_store=MagicMock(),
        bot_open_id="bot",
        session_router=SessionRouter(store=store),
        workspace_base=Path("/tmp/test"),
        queue_registry=queue_registry,
    )


@pytest.mark.asyncio
async def test_stop_with_active_run_sets_abort_and_replies() -> None:
    tm = TaskManager()
    qr = FeishuQueueRegistry(task_manager=tm)
    rc = qr.get_run_control("sess-active")
    rc.active = True

    ctx = _make_ctx(qr)

    await handle_stop_feishu(ctx, "sess-active", "msg-1")

    assert rc.abort_event.is_set() is True
    ctx.feishu_client.reply_text.assert_awaited_once_with("msg-1", "🛑 已停止")

    await tm.shutdown(grace_s=0.5)


@pytest.mark.asyncio
async def test_stop_with_no_active_run_replies_friendly() -> None:
    tm = TaskManager()
    qr = FeishuQueueRegistry(task_manager=tm)

    ctx = _make_ctx(qr)

    await handle_stop_feishu(ctx, "sess-idle", "msg-2")

    rc = qr.get_run_control("sess-idle")
    assert rc.abort_event.is_set() is False
    ctx.feishu_client.reply_text.assert_awaited_once_with("msg-2", "⚠️ 没有正在运行的任务")

    await tm.shutdown(grace_s=0.5)
