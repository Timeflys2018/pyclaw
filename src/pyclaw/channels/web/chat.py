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
        if self._busy.get(conversation_id, False):
            return False
        return not self._consumer_running(conversation_id)

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
    if _session_queue._task_manager is None:
        tm = getattr(getattr(state.ws, "app", None), "state", None)
        if tm is not None:
            tm = getattr(tm, "task_manager", None)
        if tm is not None:
            _session_queue.set_task_manager(tm)

    conversation_id = msg.conversation_id

    async def _handle(m: ChatSendMessage) -> None:
        await _run_chat(state, m, settings)

    position = await _session_queue.enqueue(conversation_id, msg, _handle)
    if position > 0:
        await send_event(state, SERVER_CHAT_QUEUED, conversation_id, {
            "position": position,
        })


_EXTRACT_TIMEOUT_SECONDS = 15.0


async def _try_slash_command(
    state: ConnectionState,
    msg: ChatSendMessage,
    session_id: str,
) -> bool:
    """Return True if the message was handled as a slash command."""
    lower = (msg.content or "").strip().lower()

    if lower.startswith("/new") or lower.startswith("/reset"):
        from pyclaw.channels.web.routes import _get_router

        router = _get_router()
        await router.rotate(
            f"web:{state.user_id}", "default",
        )
        await send_event(state, SERVER_CHAT_DONE, msg.conversation_id, {
            "final_message": "✨ 新会话已开始，之前的对话已归档。",
            "usage": {},
            "aborted": False,
        })
        return True

    if lower.startswith("/extract") or lower.startswith("/learn"):
        from pyclaw.channels.web.routes import (
            _redis_client,
            _memory_store,
            _evolution_settings,
            _llm_client,
            _nudge_hook,
            _get_router,
        )

        router = _get_router()
        redis_client = _redis_client
        memory_store = _memory_store
        evolution_settings = _evolution_settings
        llm_client = _llm_client
        session_store = router.store
        nudge_hook = _nudge_hook

        if not all([redis_client, memory_store, evolution_settings, llm_client, session_store]):
            await send_event(state, SERVER_CHAT_DONE, msg.conversation_id, {
                "final_message": "⚠️ 自我进化功能未启用。",
                "usage": {},
                "aborted": False,
            })
            return True

        from pyclaw.core.sop_extraction import (
            _check_user_ratelimit,
            _derive_session_key,
            extract_sops_sync,
            format_extraction_result_zh,
        )

        session_key = _derive_session_key(session_id)
        if not await _check_user_ratelimit(redis_client, session_key):
            await send_event(state, SERVER_CHAT_DONE, msg.conversation_id, {
                "final_message": "⏱ 学习触发过于频繁，请 1 分钟后再试。",
                "usage": {},
                "aborted": False,
            })
            return True

        try:
            result = await asyncio.wait_for(
                extract_sops_sync(
                    memory_store=memory_store,
                    session_store=session_store,
                    redis_client=redis_client,
                    llm_client=llm_client,
                    session_id=session_id,
                    settings=evolution_settings,
                    min_tool_calls=1,
                    nudge_hook=nudge_hook,
                ),
                timeout=_EXTRACT_TIMEOUT_SECONDS,
            )
            reply = format_extraction_result_zh(result)
        except TimeoutError:
            reply = "⏳ 学习超时（>15 秒）已中止，候选数据已保留，1 分钟后可再次 /extract。"

        await send_event(state, SERVER_CHAT_DONE, msg.conversation_id, {
            "final_message": reply,
            "usage": {},
            "aborted": False,
        })
        return True

    return False


async def _run_chat(
    state: ConnectionState,
    msg: ChatSendMessage,
    settings: WebSettings,
) -> None:
    abort_event = _session_queue.get_abort_event(msg.conversation_id)

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

    try:
        async for event in run_agent_stream(
            request, deps, tool_workspace_path=user_workspace, abort=abort_event,
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
