from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pyclaw.channels.web.chat import (
    SessionQueue,
    enqueue_chat,
    handle_abort,
    handle_tool_approve,
)
from pyclaw.channels.web.protocol import (
    ChatAbortMessage,
    ChatSendMessage,
    ToolApproveMessage,
    SERVER_CHAT_DELTA,
    SERVER_CHAT_DONE,
    SERVER_CHAT_QUEUED,
    SERVER_CHAT_TOOL_START,
    SERVER_CHAT_TOOL_END,
    SERVER_TOOL_APPROVE_REQUEST,
)
from pyclaw.channels.web.websocket import ConnectionState
from pyclaw.infra.settings import WebSettings
from pyclaw.infra.task_manager import TaskManager
from pyclaw.models.agent import (
    Done,
    ErrorEvent,
    TextChunk,
    ToolApprovalRequest,
    ToolCallEnd,
    ToolCallStart,
    ToolResult,
    TextBlock,
)


class TestSessionQueue:
    def test_fresh_queue_has_no_consumers(self) -> None:
        sq = SessionQueue()
        assert sq.is_idle("conv-1")

    @pytest.mark.asyncio
    async def test_enqueue_starts_consumer(self) -> None:
        tm = TaskManager()
        sq = SessionQueue(task_manager=tm)
        consumed: list[str] = []

        async def handler(msg: ChatSendMessage) -> None:
            consumed.append(msg.content)

        await sq.enqueue("conv-1", ChatSendMessage(content="hello"), handler)
        await asyncio.sleep(0.05)
        assert consumed == ["hello"]

    @pytest.mark.asyncio
    async def test_serial_execution(self) -> None:
        tm = TaskManager()
        sq = SessionQueue(task_manager=tm)
        order: list[int] = []

        async def slow_handler(msg: ChatSendMessage) -> None:
            idx = int(msg.content)
            await asyncio.sleep(0.02)
            order.append(idx)

        await sq.enqueue("conv-1", ChatSendMessage(content="1"), slow_handler)
        await sq.enqueue("conv-1", ChatSendMessage(content="2"), slow_handler)
        await asyncio.sleep(0.15)
        assert order == [1, 2]

    @pytest.mark.asyncio
    async def test_queue_position(self) -> None:
        tm = TaskManager()
        sq = SessionQueue(task_manager=tm)
        gate = asyncio.Event()

        async def blocking_handler(msg: ChatSendMessage) -> None:
            await gate.wait()

        await sq.enqueue("conv-1", ChatSendMessage(content="a"), blocking_handler)
        await asyncio.sleep(0.01)
        assert sq.queue_position("conv-1") == 1

        await sq.enqueue("conv-1", ChatSendMessage(content="b"), blocking_handler)
        await asyncio.sleep(0.01)
        assert sq.queue_position("conv-1") == 2

        gate.set()
        await asyncio.sleep(0.05)

    def test_get_abort_event(self) -> None:
        sq = SessionQueue()
        ev = sq.get_abort_event("conv-1")
        assert isinstance(ev, asyncio.Event)
        assert not ev.is_set()

    def test_get_abort_event_same_conversation(self) -> None:
        sq = SessionQueue()
        ev1 = sq.get_abort_event("conv-1")
        ev2 = sq.get_abort_event("conv-1")
        assert ev1 is ev2

    def test_set_approval_decision(self) -> None:
        sq = SessionQueue()
        sq.set_approval_decision("conv-1", "tc-1", True)
        decision = sq.get_approval_decision("conv-1", "tc-1")
        assert decision is True

    @pytest.mark.asyncio
    async def test_consumer_running_returns_true_for_active(self) -> None:
        tm = TaskManager()
        sq = SessionQueue(task_manager=tm)
        gate = asyncio.Event()

        async def blocking(msg: ChatSendMessage) -> None:
            await gate.wait()

        await sq.enqueue("conv-1", ChatSendMessage(content="a"), blocking)
        await asyncio.sleep(0.01)
        assert sq._consumer_running("conv-1") is True
        gate.set()
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_consumer_running_returns_false_when_no_tm(self) -> None:
        sq = SessionQueue()
        assert sq._consumer_running("conv-1") is False

    @pytest.mark.asyncio
    async def test_consumer_running_returns_false_for_unknown(self) -> None:
        tm = TaskManager()
        sq = SessionQueue(task_manager=tm)
        assert sq._consumer_running("nonexistent") is False


class TestEnqueueChat:
    @pytest.fixture(autouse=True)
    def _inject_task_manager(self) -> None:
        from pyclaw.channels.web import chat as chat_mod
        chat_mod._session_queue.set_task_manager(TaskManager())

    @pytest.mark.asyncio
    async def test_sends_delta_events(self) -> None:
        mock_ws = AsyncMock()
        state = ConnectionState(ws=mock_ws, ws_session_id="s1", user_id="u1", authenticated=True)
        settings = WebSettings(jwt_secret="s", heartbeat_interval=60, pong_timeout=10)
        msg = ChatSendMessage(conversation_id="c1", content="hi")

        events_to_yield = [
            TextChunk(text="Hello"),
            Done(final_message="Hello", usage={"input": 10, "output": 5}),
        ]

        with _patch_agent_stream(events_to_yield):
            await enqueue_chat(state, msg, settings)
            await asyncio.sleep(0.1)

        sent_types = [call[0][0]["type"] for call in mock_ws.send_json.call_args_list]
        assert SERVER_CHAT_DELTA in sent_types
        assert SERVER_CHAT_DONE in sent_types

    @pytest.mark.asyncio
    async def test_sends_tool_events(self) -> None:
        mock_ws = AsyncMock()
        state = ConnectionState(ws=mock_ws, ws_session_id="s1", user_id="u1", authenticated=True)
        settings = WebSettings(jwt_secret="s", heartbeat_interval=60, pong_timeout=10)
        msg = ChatSendMessage(conversation_id="c1", content="run tool")

        events_to_yield = [
            ToolCallStart(tool_call_id="tc1", name="bash", arguments={"cmd": "ls"}),
            ToolCallEnd(
                tool_call_id="tc1",
                result=ToolResult(
                    tool_call_id="tc1",
                    content=[TextBlock(text="file.txt")],
                ),
            ),
            Done(final_message="done", usage={}),
        ]

        with _patch_agent_stream(events_to_yield):
            await enqueue_chat(state, msg, settings)
            await asyncio.sleep(0.1)

        sent_types = [call[0][0]["type"] for call in mock_ws.send_json.call_args_list]
        assert SERVER_CHAT_TOOL_START in sent_types
        assert SERVER_CHAT_TOOL_END in sent_types
        assert SERVER_CHAT_DONE in sent_types

    @pytest.mark.asyncio
    async def test_sends_tool_approval_request(self) -> None:
        mock_ws = AsyncMock()
        state = ConnectionState(ws=mock_ws, ws_session_id="s1", user_id="u1", authenticated=True)
        settings = WebSettings(jwt_secret="s", heartbeat_interval=60, pong_timeout=10)
        msg = ChatSendMessage(conversation_id="c1", content="write file")

        events_to_yield = [
            ToolApprovalRequest(tool_call_id="tc1", tool_name="write", args={"path": "/tmp/x"}),
            Done(final_message="done", usage={}),
        ]

        with _patch_agent_stream(events_to_yield):
            await enqueue_chat(state, msg, settings)
            await asyncio.sleep(0.1)

        sent_types = [call[0][0]["type"] for call in mock_ws.send_json.call_args_list]
        assert SERVER_TOOL_APPROVE_REQUEST in sent_types

    @pytest.mark.asyncio
    async def test_queued_notification(self) -> None:
        mock_ws = AsyncMock()
        state = ConnectionState(ws=mock_ws, ws_session_id="s1", user_id="u1", authenticated=True)
        settings = WebSettings(jwt_secret="s", heartbeat_interval=60, pong_timeout=10)

        gate = asyncio.Event()
        events_slow = [Done(final_message="done", usage={})]

        with _patch_agent_stream(events_slow, gate=gate):
            cid = f"queued-test-{id(self)}"
            await enqueue_chat(state, ChatSendMessage(conversation_id=cid, content="first"), settings)
            await asyncio.sleep(0.02)
            await enqueue_chat(state, ChatSendMessage(conversation_id=cid, content="second"), settings)
            await asyncio.sleep(0.02)

            sent_types = [call[0][0]["type"] for call in mock_ws.send_json.call_args_list]
            assert SERVER_CHAT_QUEUED in sent_types

            gate.set()
            await asyncio.sleep(0.1)


class TestHandleAbort:
    @pytest.mark.asyncio
    async def test_abort_sets_event(self) -> None:
        mock_ws = AsyncMock()
        state = ConnectionState(ws=mock_ws, ws_session_id="s1", user_id="u1", authenticated=True)

        from pyclaw.channels.web.chat import _session_queue
        abort_ev = _session_queue.get_abort_event("c1")
        assert not abort_ev.is_set()

        await handle_abort(state, ChatAbortMessage(conversation_id="c1"))
        assert abort_ev.is_set()


class TestHandleToolApprove:
    @pytest.mark.asyncio
    async def test_stores_decision(self) -> None:
        mock_ws = AsyncMock()
        state = ConnectionState(ws=mock_ws, ws_session_id="s1", user_id="u1", authenticated=True)

        await handle_tool_approve(
            state,
            ToolApproveMessage(conversation_id="c1", tool_call_id="tc1", approved=True),
        )

        from pyclaw.channels.web.chat import _session_queue
        decision = _session_queue.get_approval_decision("c1", "tc1")
        assert decision is True


def _patch_agent_stream(events: list[Any], gate: asyncio.Event | None = None):
    from contextlib import contextmanager
    from unittest.mock import patch, MagicMock

    async def _fake_stream(*args: Any, **kwargs: Any):
        if gate is not None:
            await gate.wait()
        for ev in events:
            yield ev

    fake_deps = MagicMock(spec=["llm", "tools", "config"])

    @contextmanager
    def _combined():
        with patch("pyclaw.channels.web.chat.run_agent_stream", side_effect=_fake_stream), \
             patch("pyclaw.channels.web.chat._get_runner_deps", return_value=fake_deps):
            yield

    return _combined()
