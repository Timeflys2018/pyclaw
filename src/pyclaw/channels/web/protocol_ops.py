from __future__ import annotations

import logging

from pyclaw.channels.web.protocol import SERVER_CHAT_DONE
from pyclaw.channels.web.websocket import ConnectionState, send_event

logger = logging.getLogger(__name__)


async def handle_stop_command(state: ConnectionState, conversation_id: str) -> None:
    from pyclaw.channels.web.chat import _get_session_queue

    session_queue = _get_session_queue(state)
    rc = session_queue.get_run_control(conversation_id)

    if rc.is_active():
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
