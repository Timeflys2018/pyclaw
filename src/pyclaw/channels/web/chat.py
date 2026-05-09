from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
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
from pyclaw.core.agent.run_control import RunControl
from pyclaw.core.agent.runner import RunRequest, run_agent_stream, AgentRunnerDeps
from pyclaw.infra.settings import WebSettings
from pyclaw.infra.task_manager import TaskManager
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
    def __init__(self, task_manager: TaskManager | None = None) -> None:
        self._task_manager = task_manager
        self._queues: dict[str, asyncio.Queue[tuple[ChatSendMessage, MessageHandler]]] = {}
        self._consumers: dict[str, str] = {}
        self._busy: dict[str, bool] = {}
        self._abort_events: dict[str, asyncio.Event] = {}
        self._run_controls: dict[str, RunControl] = {}
        self._approval_decisions: dict[str, bool] = {}

    def set_task_manager(self, tm: TaskManager) -> None:
        self._task_manager = tm

    def _consumer_running(self, conversation_id: str) -> bool:
        tid = self._consumers.get(conversation_id)
        if tid is None or self._task_manager is None:
            return False
        state = self._task_manager.get_state(tid)
        return state == "running"

    def is_idle(self, conversation_id: str) -> bool:
        return not self._busy.get(conversation_id, False)

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

        if not self._consumer_running(conversation_id) and self._task_manager is not None:
            task_id = self._task_manager.spawn(
                f"web-consumer:{conversation_id}",
                self._consume(conversation_id),
                category="consumer",
            )
            self._consumers[conversation_id] = task_id

        return position

    def queue_position(self, conversation_id: str) -> int:
        q = self._queues.get(conversation_id)
        pending = q.qsize() if q else 0
        busy = self._busy.get(conversation_id, False)
        return pending + (1 if busy else 0)

    def get_abort_event(self, conversation_id: str) -> asyncio.Event:
        return self.get_run_control(conversation_id).abort_event

    def get_run_control(self, conversation_id: str) -> RunControl:
        rc = self._run_controls.get(conversation_id)
        if rc is None:
            event = self._abort_events.get(conversation_id) or asyncio.Event()
            self._abort_events[conversation_id] = event
            rc = RunControl(abort_event=event)
            self._run_controls[conversation_id] = rc
        return rc

    def reset_abort_event(self, conversation_id: str) -> None:
        rc = self._run_controls.get(conversation_id)
        if rc is not None:
            rc.abort_event.clear()
            return
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
        try:
            while True:
                try:
                    # Worker idle timeout: 5 min idle → consumer exits & frees ~1KB dict slots.
                    # NOT a session timeout — session history persists in SessionStore (Redis,
                    # 7-day TTL) regardless. Next user message rebuilds the consumer in ~20μs.
                    # Aligned with TaskManager._PRUNE_AGE_S=300 so completed handles get pruned.
                    msg, handler = await asyncio.wait_for(q.get(), timeout=300)
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
        finally:
            self._queues.pop(conversation_id, None)
            self._consumers.pop(conversation_id, None)
            self._busy.pop(conversation_id, None)
            self._abort_events.pop(conversation_id, None)
            self._run_controls.pop(conversation_id, None)

    def reset(self) -> None:
        if self._task_manager is not None:
            for tid in list(self._consumers.values()):
                handle = self._task_manager._tasks.get(tid)
                if handle is not None and not handle.asyncio_task.done():
                    handle.asyncio_task.cancel()
        self._queues.clear()
        self._consumers.clear()
        self._busy.clear()
        self._abort_events.clear()
        self._run_controls.clear()
        self._approval_decisions.clear()


_session_queue = SessionQueue()


def _get_session_queue(state: ConnectionState) -> SessionQueue:
    from pyclaw.channels.web.deps import WebDeps
    web_deps = getattr(state.ws.app.state, "web_deps", None)
    if isinstance(web_deps, WebDeps):
        return web_deps.session_queue
    return _session_queue


async def enqueue_chat(
    state: ConnectionState,
    msg: ChatSendMessage,
    settings: WebSettings,
) -> None:
    from pyclaw.channels.web.message_classifier import classify
    from pyclaw.channels.web.protocol_ops import handle_stop_command

    if classify(msg.content or "") == "protocol_op":
        await handle_stop_command(state, msg.conversation_id)
        return

    session_queue = _get_session_queue(state)
    if session_queue._task_manager is None:
        tm = getattr(getattr(state.ws, "app", None), "state", None)
        if tm is not None:
            tm = getattr(tm, "task_manager", None)
        if tm is not None:
            session_queue.set_task_manager(tm)

    conversation_id = msg.conversation_id

    async def _handle(m: ChatSendMessage) -> None:
        await _run_chat(state, m, settings)

    position = await session_queue.enqueue(conversation_id, msg, _handle)
    if position > 0:
        await send_event(state, SERVER_CHAT_QUEUED, conversation_id, {
            "position": position,
        })


async def _try_slash_command(
    state: ConnectionState,
    msg: ChatSendMessage,
    session_id: str,
) -> bool:
    content = (msg.content or "").strip()
    if not content.startswith("/"):
        return False

    from pyclaw.channels.web.command_adapter import WebCommandAdapter
    from pyclaw.channels.web.deps import WebDeps

    web_deps = getattr(state.ws.app.state, "web_deps", None)
    if isinstance(web_deps, WebDeps):
        adapter = WebCommandAdapter()
        return await adapter.handle(
            text=content,
            state=state,
            conversation_id=msg.conversation_id,
            session_id=session_id,
            deps=web_deps.runner_deps,
            session_router=web_deps.session_router,
            workspace_base=web_deps.workspace_base,
            redis_client=web_deps.redis_client,
            memory_store=web_deps.memory_store,
            evolution_settings=web_deps.evolution_settings,
            nudge_hook=web_deps.nudge_hook,
            session_queue=web_deps.session_queue,
            agent_settings=web_deps.agent_settings,
        )

    from pyclaw.channels.web.routes import (
        _evolution_settings,
        _get_router,
        _llm_client,
        _memory_store,
        _nudge_hook,
        _redis_client,
    )

    try:
        router = _get_router()
    except RuntimeError:
        return False

    deps = _get_runner_deps(state)
    workspace_base: Path = state.ws.app.state.workspace_base

    adapter = WebCommandAdapter()
    return await adapter.handle(
        text=content,
        state=state,
        conversation_id=msg.conversation_id,
        session_id=session_id,
        deps=deps,
        session_router=router,
        workspace_base=workspace_base,
        redis_client=_redis_client,
        memory_store=_memory_store,
        evolution_settings=_evolution_settings,
        nudge_hook=_nudge_hook,
        session_queue=_session_queue,
    )


async def _run_chat(
    state: ConnectionState,
    msg: ChatSendMessage,
    settings: WebSettings,
) -> None:
    session_queue = _get_session_queue(state)
    rc = session_queue.get_run_control(msg.conversation_id)

    # Task 10.1: enforce conversation_id ownership for "web:" prefixed ids
    if msg.conversation_id.startswith("web:"):
        expected_prefix = f"web:{state.user_id}:"
        if not msg.conversation_id.startswith(expected_prefix):
            await send_event(state, SERVER_ERROR, msg.conversation_id, {
                "message": "Access denied: invalid conversation_id",
            })
            return
        session_id = msg.conversation_id
    else:
        session_id = f"web:{state.user_id}:{msg.conversation_id}"

    if await _try_slash_command(state, msg, session_id):
        return

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

    # Task 10.2: per-user workspace isolation
    workspace_base: Path = state.ws.app.state.workspace_base
    user_workspace = workspace_base / f"web_{state.user_id}"
    user_workspace.mkdir(parents=True, exist_ok=True)

    rc.active = True
    try:
        async for event in run_agent_stream(
            request, deps, tool_workspace_path=user_workspace, control=rc,
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
                    if rc.chat_done_handled_externally:
                        rc.chat_done_handled_externally = False
                    else:
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
    finally:
        rc.active = False


def _get_runner_deps(state: ConnectionState) -> AgentRunnerDeps:
    return state.ws.app.state.runner_deps


async def handle_abort(
    state: ConnectionState,
    msg: ChatAbortMessage,
) -> None:
    session_queue = _get_session_queue(state)
    abort_ev = session_queue.get_abort_event(msg.conversation_id)
    abort_ev.set()


async def handle_tool_approve(
    state: ConnectionState,
    msg: ToolApproveMessage,
) -> None:
    session_queue = _get_session_queue(state)
    session_queue.set_approval_decision(
        msg.conversation_id, msg.tool_call_id, msg.approved
    )
