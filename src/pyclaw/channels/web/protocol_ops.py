from __future__ import annotations

import logging
from typing import Literal

from pyclaw.channels.web.protocol import SERVER_CHAT_DONE
from pyclaw.channels.web.websocket import ConnectionState, send_event
from pyclaw.core.agent.run_control import RunControl, SteerMessage
from pyclaw.core.commands.steering import enforce_cap

logger = logging.getLogger(__name__)


async def handle_stop_command(state: ConnectionState, conversation_id: str) -> None:
    from pyclaw.channels.web.chat import _get_session_queue

    session_queue = _get_session_queue(state)
    rc = session_queue.get_run_control(conversation_id)

    if rc.is_active():
        rc.chat_done_handled_externally = True
        rc.stop()
        await send_event(
            state,
            SERVER_CHAT_DONE,
            conversation_id,
            {"final_message": "🛑 已停止", "usage": {}, "aborted": True},
        )
    else:
        await send_event(
            state,
            SERVER_CHAT_DONE,
            conversation_id,
            {"final_message": "⚠️ 没有正在运行的任务", "usage": {}, "aborted": False},
        )


async def _handle_injection(
    state: ConnectionState,
    conversation_id: str,
    args: str,
    kind: Literal["steer", "sidebar"],
    command: str,
    arg_hint: str,
    accepted_msg: str,
) -> None:
    from pyclaw.channels.web.chat import _get_session_queue

    payload = args.strip()
    if not payload:
        await send_event(
            state,
            SERVER_CHAT_DONE,
            conversation_id,
            {
                "final_message": f"⚠ {command} 需要参数：{command} {arg_hint}",
                "usage": {},
                "aborted": False,
            },
        )
        return

    session_queue = _get_session_queue(state)
    rc: RunControl = session_queue.get_run_control(conversation_id)

    if not rc.is_active():
        await send_event(
            state,
            SERVER_CHAT_DONE,
            conversation_id,
            {
                "final_message": "⚠ 没有正在运行的 agent",
                "usage": {},
                "aborted": False,
            },
        )
        return

    ok, warning = enforce_cap(rc, payload)
    if not ok:
        await send_event(
            state,
            SERVER_CHAT_DONE,
            conversation_id,
            {"final_message": warning, "usage": {}, "aborted": False},
        )
        return

    rc.pending_steers.append(SteerMessage(kind=kind, text=payload))

    final = accepted_msg if not warning else f"{accepted_msg}\n{warning}"
    await send_event(
        state,
        SERVER_CHAT_DONE,
        conversation_id,
        {"final_message": final, "usage": {}, "aborted": False},
    )


async def handle_steer_command(state: ConnectionState, conversation_id: str, args: str) -> None:
    await _handle_injection(
        state,
        conversation_id,
        args,
        kind="steer",
        command="/steer",
        arg_hint="<message>",
        accepted_msg="✓ 已接收 steer 指令 (将在下一轮生效)",
    )


async def handle_btw_command(state: ConnectionState, conversation_id: str, args: str) -> None:
    await _handle_injection(
        state,
        conversation_id,
        args,
        kind="sidebar",
        command="/btw",
        arg_hint="<question>",
        accepted_msg="✓ 已接收 side question (将在下一轮简短作答)",
    )
