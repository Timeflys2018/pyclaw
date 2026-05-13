from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pyclaw.channels.feishu.handler import (
    handle_btw_feishu,
    handle_steer_feishu,
)
from pyclaw.core.agent.run_control import RunControl


def _sync_ctx(rc: RunControl | None):
    ctx = AsyncMock()
    ctx.queue_registry = type(
        "_Reg",
        (),
        {"get_run_control": lambda self, sid: rc},
    )()
    ctx.feishu_client = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_handle_steer_feishu_active_appends_and_replies():
    rc = RunControl()
    rc.active = True
    ctx = _sync_ctx(rc)

    await handle_steer_feishu(ctx, "sess_a", "msg_1", "use Python 3.11")

    assert len(rc.pending_steers) == 1
    assert rc.pending_steers[0].kind == "steer"
    assert rc.pending_steers[0].text == "use Python 3.11"
    ctx.feishu_client.reply_text.assert_called_once()
    reply_text = ctx.feishu_client.reply_text.call_args[0][1]
    assert "已接收" in reply_text or "accepted" in reply_text.lower()


@pytest.mark.asyncio
async def test_handle_steer_feishu_inactive_warns():
    rc = RunControl()
    ctx = _sync_ctx(rc)

    await handle_steer_feishu(ctx, "sess_a", "msg_1", "anything")

    assert rc.pending_steers == []
    ctx.feishu_client.reply_text.assert_called_once()
    reply_text = ctx.feishu_client.reply_text.call_args[0][1]
    assert "没有正在运行" in reply_text


@pytest.mark.asyncio
async def test_handle_steer_feishu_empty_args_shows_usage():
    rc = RunControl()
    rc.active = True
    ctx = _sync_ctx(rc)

    await handle_steer_feishu(ctx, "sess_a", "msg_1", "")

    assert rc.pending_steers == []
    reply_text = ctx.feishu_client.reply_text.call_args[0][1]
    assert "/steer <message>" in reply_text or "需要参数" in reply_text


@pytest.mark.asyncio
async def test_handle_btw_feishu_active_appends_sidebar():
    rc = RunControl()
    rc.active = True
    ctx = _sync_ctx(rc)

    await handle_btw_feishu(ctx, "sess_a", "msg_1", "what is Redis?")

    assert len(rc.pending_steers) == 1
    assert rc.pending_steers[0].kind == "sidebar"
    assert rc.pending_steers[0].text == "what is Redis?"


@pytest.mark.asyncio
async def test_handle_btw_feishu_empty_args_shows_usage():
    rc = RunControl()
    rc.active = True
    ctx = _sync_ctx(rc)

    await handle_btw_feishu(ctx, "sess_a", "msg_1", "")

    assert rc.pending_steers == []
    reply_text = ctx.feishu_client.reply_text.call_args[0][1]
    assert "/btw <question>" in reply_text or "需要参数" in reply_text
