from __future__ import annotations

import asyncio
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.channels.web.chat import (
    SessionQueue,
    _run_chat,
    enqueue_chat,
)
from pyclaw.channels.web.protocol import (
    SERVER_CHAT_DONE,
    SERVER_CHAT_QUEUED,
    ChatSendMessage,
)
from pyclaw.channels.web.websocket import ConnectionState
from pyclaw.infra.settings import WebSettings
from pyclaw.infra.task_manager import TaskManager
from pyclaw.models.agent import ErrorEvent


def _setup_state(
    tm: TaskManager, sq: SessionQueue
) -> tuple[ConnectionState, AsyncMock, WebSettings]:
    workspace_base = Path(tempfile.mkdtemp())
    mock_ws = AsyncMock()
    mock_ws.app.state.workspace_base = workspace_base
    mock_ws.app.state.runner_deps = MagicMock()
    settings = WebSettings(jwt_secret="s", heartbeat_interval=60, pong_timeout=10)
    mock_ws.app.state.web_settings = settings
    mock_ws.app.state.task_manager = tm
    state = ConnectionState(
        ws=mock_ws,
        ws_session_id="ws-1",
        user_id="me",
        authenticated=True,
    )
    return state, mock_ws, settings


@pytest.mark.asyncio
async def test_stop_bypass_aborts_inflight_run_within_200ms() -> None:
    tm = TaskManager()
    sq = SessionQueue(task_manager=tm)
    state, mock_ws, settings = _setup_state(tm, sq)

    abort_observed_at: list[float] = []

    async def long_running_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        control = kwargs.get("control")
        assert control is not None, "_run_chat must pass control= to run_agent_stream"
        for _ in range(200):
            if control.abort_event.is_set():
                abort_observed_at.append(time.monotonic())
                yield ErrorEvent(error_code="aborted", message="run aborted")
                return
            await asyncio.sleep(0.01)

    long_msg = ChatSendMessage(
        type="chat.send",
        conversation_id="conv-1",
        content="long task",
        attachments=[],
    )

    with (
        patch("pyclaw.channels.web.chat._get_session_queue", return_value=sq),
        patch("pyclaw.channels.web.chat.run_agent_stream", side_effect=long_running_stream),
    ):
        run_task = asyncio.create_task(_run_chat(state, long_msg, settings))
        await asyncio.sleep(0.1)

        rc = sq.get_run_control("conv-1")
        assert rc.is_active() is True, "Long running task should be active"

        stop_msg = ChatSendMessage(
            type="chat.send",
            conversation_id="conv-1",
            content="/stop",
            attachments=[],
        )

        send_t = time.monotonic()
        await enqueue_chat(state, stop_msg, settings)

        try:
            await asyncio.wait_for(run_task, timeout=1.0)
        except TimeoutError:
            run_task.cancel()
            pytest.fail("Long task did not abort within 1s of /stop")

    assert rc.abort_event.is_set(), "abort_event must be set after /stop"
    assert abort_observed_at, "Stream must have observed the abort signal"
    elapsed_ms = (abort_observed_at[0] - send_t) * 1000
    assert elapsed_ms < 200, f"Abort took {elapsed_ms:.1f}ms (expected <200ms)"

    sent_payloads = [c[0][0] for c in mock_ws.send_json.call_args_list]
    stop_replies = [
        p
        for p in sent_payloads
        if p["type"] == SERVER_CHAT_DONE and "已停止" in p["data"]["final_message"]
    ]
    assert len(stop_replies) == 1, f"expected 1 '🛑 已停止' reply, got {stop_replies!r}"
    assert stop_replies[0]["data"]["aborted"] is True

    chat_done_payloads = [p for p in sent_payloads if p["type"] == SERVER_CHAT_DONE]
    assert len(chat_done_payloads) == 1, (
        f"expected exactly 1 chat.done after /stop (no duplicate from runner abort path), "
        f"got {len(chat_done_payloads)}: {chat_done_payloads!r}"
    )

    queued_replies = [p for p in sent_payloads if p["type"] == SERVER_CHAT_QUEUED]
    assert len(queued_replies) == 0, "/stop must NOT be enqueued"

    await tm.shutdown(grace_s=0.5)


@pytest.mark.asyncio
async def test_stop_with_no_active_run_replies_friendly() -> None:
    tm = TaskManager()
    sq = SessionQueue(task_manager=tm)
    state, mock_ws, settings = _setup_state(tm, sq)

    stop_msg = ChatSendMessage(
        type="chat.send",
        conversation_id="conv-idle",
        content="/stop",
        attachments=[],
    )
    with patch("pyclaw.channels.web.chat._get_session_queue", return_value=sq):
        await enqueue_chat(state, stop_msg, settings)

    sent_payloads = [c[0][0] for c in mock_ws.send_json.call_args_list]
    matching = [
        p
        for p in sent_payloads
        if p["type"] == SERVER_CHAT_DONE and "没有正在运行" in p["data"]["final_message"]
    ]
    assert len(matching) == 1
    assert matching[0]["data"]["aborted"] is False

    await tm.shutdown(grace_s=0.5)
