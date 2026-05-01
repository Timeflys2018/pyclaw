from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from pyclaw.channels.web.protocol import (
    ChatAbortMessage,
    ChatSendMessage,
    ToolApproveMessage,
    SERVER_CHAT_DELTA,
    SERVER_CHAT_DONE,
    SERVER_CHAT_QUEUED,
    SERVER_CHAT_TOOL_END,
    SERVER_CHAT_TOOL_START,
    SERVER_ERROR,
    SERVER_TOOL_APPROVE_REQUEST,
)
from pyclaw.channels.web.websocket import ConnectionState, send_event
from pyclaw.core.agent.runner import RunRequest, run_agent_stream, AgentRunnerDeps
from pyclaw.infra.settings import WebSettings
from pyclaw.models.agent import (
    Done,
    ErrorEvent,
    TextChunk,
    ToolApprovalRequest,
    ToolCallEnd,
    ToolCallStart,
)

logger = logging.getLogger(__name__)

MessageHandler = Callable[[ChatSendMessage], Coroutine[Any, Any, None]]


class SessionQueue:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[tuple[ChatSendMessage, MessageHandler]]] = {}
        self._consumers: dict[str, asyncio.Task[None]] = {}
        self._busy: dict[str, bool] = {}
        self._abort_events: dict[str, asyncio.Event] = {}
        self._approval_decisions: dict[str, bool] = {}

    def is_idle(self, conversation_id: str) -> bool:
        if self._busy.get(conversation_id, False):
            return False
        return conversation_id not in self._consumers or self._consumers[conversation_id].done()

    async def enqueue(
        self,
        conversation_id: str,
        msg: ChatSendMessage,
        handler: MessageHandler,
    ) -> int:
        if conversation_id not in self._queues:
            self._queues[conversation_id] = asyncio.Queue()

        pending = self._queues[conversation_id].qsize()
        busy = self._busy.get(conversation_id, False)
        position = pending + (1 if busy else 0)

        await self._queues[conversation_id].put((msg, handler))

        if conversation_id not in self._consumers or self._consumers[conversation_id].done():
            self._consumers[conversation_id] = asyncio.create_task(
                self._consume(conversation_id)
            )

        return position

    def queue_position(self, conversation_id: str) -> int:
        q = self._queues.get(conversation_id)
        pending = q.qsize() if q else 0
        busy = self._busy.get(conversation_id, False)
        return pending + (1 if busy else 0)

    def get_abort_event(self, conversation_id: str) -> asyncio.Event:
        if conversation_id not in self._abort_events:
            self._abort_events[conversation_id] = asyncio.Event()
        return self._abort_events[conversation_id]

    def reset_abort_event(self, conversation_id: str) -> None:
        if conversation_id in self._abort_events:
            self._abort_events[conversation_id].clear()

    def set_approval_decision(
        self, conversation_id: str, tool_call_id: str, approved: bool
    ) -> None:
        key = f"{conversation_id}:{tool_call_id}"
        self._approval_decisions[key] = approved

    def get_approval_decision(
        self, conversation_id: str, tool_call_id: str
    ) -> bool | None:
        key = f"{conversation_id}:{tool_call_id}"
        return self._approval_decisions.get(key)

    async def _consume(self, conversation_id: str) -> None:
        q = self._queues[conversation_id]
        while True:
            try:
                msg, handler = await asyncio.wait_for(q.get(), timeout=60)
            except asyncio.TimeoutError:
                break
            self._busy[conversation_id] = True
            try:
                self.reset_abort_event(conversation_id)
                await handler(msg)
            except Exception:
                logger.exception(
                    "error in chat consumer for conversation %s", conversation_id
                )
            finally:
                self._busy[conversation_id] = False
                q.task_done()


_session_queue = SessionQueue()


async def enqueue_chat(
    state: ConnectionState,
    msg: ChatSendMessage,
    settings: WebSettings,
) -> None:
    conversation_id = msg.conversation_id

    async def _handle(m: ChatSendMessage) -> None:
        await _run_chat(state, m, settings)

    position = await _session_queue.enqueue(conversation_id, msg, _handle)
    if position > 0:
        await send_event(state, SERVER_CHAT_QUEUED, conversation_id, {
            "position": position,
        })


async def _run_chat(
    state: ConnectionState,
    msg: ChatSendMessage,
    settings: WebSettings,
) -> None:
    abort_event = _session_queue.get_abort_event(msg.conversation_id)

    session_id = msg.conversation_id if msg.conversation_id.startswith("web:") else f"web:{state.user_id}:{msg.conversation_id}"
    request = RunRequest(
        session_id=session_id,
        workspace_id="default",
        agent_id="default",
        user_message=msg.content,
        attachments=msg.attachments,
    )

    try:
        deps = _get_runner_deps(state)
    except AttributeError:
        deps = None

    if deps is None:
        await send_event(state, SERVER_ERROR, msg.conversation_id, {
            "message": "Agent runner not configured",
        })
        return

    try:
        async for event in run_agent_stream(
            request, deps, tool_workspace_path=".", abort=abort_event,
        ):
            if isinstance(event, TextChunk):
                await send_event(state, SERVER_CHAT_DELTA, msg.conversation_id, {
                    "text": event.text,
                })
            elif isinstance(event, ToolCallStart):
                await send_event(state, SERVER_CHAT_TOOL_START, msg.conversation_id, {
                    "tool_call_id": event.tool_call_id,
                    "name": event.name,
                    "arguments": event.arguments,
                })
            elif isinstance(event, ToolCallEnd):
                result_text = ""
                if event.result and event.result.content:
                    parts = []
                    for block in event.result.content:
                        if hasattr(block, "text"):
                            parts.append(block.text)
                        elif isinstance(block, dict) and "text" in block:
                            parts.append(block["text"])
                    result_text = "\n".join(parts)

                await send_event(state, SERVER_CHAT_TOOL_END, msg.conversation_id, {
                    "tool_call_id": event.tool_call_id,
                    "result": result_text,
                })
            elif isinstance(event, ToolApprovalRequest):
                await send_event(
                    state, SERVER_TOOL_APPROVE_REQUEST, msg.conversation_id, {
                        "tool_call_id": event.tool_call_id,
                        "tool_name": event.tool_name,
                        "args": event.args,
                        "reason": event.reason,
                    }
                )
            elif isinstance(event, Done):
                await send_event(state, SERVER_CHAT_DONE, msg.conversation_id, {
                    "final_message": event.final_message,
                    "usage": event.usage,
                    "aborted": False,
                })
            elif isinstance(event, ErrorEvent):
                aborted = event.error_code == "aborted"
                if aborted:
                    await send_event(state, SERVER_CHAT_DONE, msg.conversation_id, {
                        "final_message": "",
                        "usage": {},
                        "aborted": True,
                    })
                else:
                    await send_event(state, SERVER_ERROR, msg.conversation_id, {
                        "message": event.message,
                    })
    except Exception:
        logger.exception("error running chat for conversation %s", msg.conversation_id)
        await send_event(state, SERVER_ERROR, msg.conversation_id, {
            "message": "Internal error",
        })


def _get_runner_deps(state: ConnectionState) -> AgentRunnerDeps:
    return state.ws.app.state.runner_deps


async def handle_abort(
    state: ConnectionState,
    msg: ChatAbortMessage,
) -> None:
    abort_ev = _session_queue.get_abort_event(msg.conversation_id)
    abort_ev.set()


async def handle_tool_approve(
    state: ConnectionState,
    msg: ToolApproveMessage,
) -> None:
    _session_queue.set_approval_decision(
        msg.conversation_id, msg.tool_call_id, msg.approved
    )
