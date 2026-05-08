from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from pyclaw.channels.web.auth import verify_jwt
from pyclaw.channels.web.protocol import (
    ChatAbortMessage,
    ChatSendMessage,
    IdentifyMessage,
    PongMessage,
    ToolApproveMessage,
    parse_client_message,
    SERVER_ERROR,
    SERVER_HELLO,
    SERVER_PING,
    SERVER_READY,
)
from pyclaw.infra.settings import WebSettings
from pyclaw.infra.task_manager import TaskManager

logger = logging.getLogger(__name__)

ws_router = APIRouter()


@dataclass
class ConnectionState:
    ws: Any
    ws_session_id: str
    user_id: str | None = None
    seq: int = 0
    last_pong: float = field(default_factory=time.time)
    authenticated: bool = False


class ConnectionRegistry:
    def __init__(self) -> None:
        self._connections: dict[str, set[Any]] = {}

    def connect(self, user_id: str, ws: Any) -> None:
        self._connections.setdefault(user_id, set()).add(ws)

    def disconnect(self, user_id: str, ws: Any) -> None:
        conns = self._connections.get(user_id)
        if conns is not None:
            conns.discard(ws)
            if not conns:
                del self._connections[user_id]

    def count(self, user_id: str) -> int:
        return len(self._connections.get(user_id, set()))

    def get_connections(self, user_id: str) -> set[Any]:
        return set(self._connections.get(user_id, set()))

    def clear(self) -> None:
        self._connections.clear()


registry = ConnectionRegistry()


def _get_connection_registry(websocket: WebSocket) -> ConnectionRegistry:
    from pyclaw.channels.web.deps import WebDeps
    web_deps = getattr(websocket.app.state, "web_deps", None)
    if isinstance(web_deps, WebDeps):
        return web_deps.connection_registry
    return registry


async def send_event(
    state: ConnectionState,
    event_type: str,
    conversation_id: str,
    data: dict[str, Any],
) -> int:
    state.seq += 1
    envelope = {
        "type": event_type,
        "conversation_id": conversation_id,
        "seq": state.seq,
        "ts": int(time.time() * 1000),
        "data": data,
    }
    await state.ws.send_json(envelope)
    return state.seq


def _get_settings(websocket: WebSocket) -> WebSettings:
    return websocket.app.state.web_settings


def _get_task_manager(websocket: WebSocket) -> TaskManager:
    return websocket.app.state.task_manager


@ws_router.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    settings: WebSettings = _get_settings(websocket)
    await websocket.accept()
    state = ConnectionState(ws=websocket, ws_session_id=secrets.token_hex(8))

    await websocket.send_json({
        "type": SERVER_HELLO,
        "data": {"heartbeat_interval": settings.heartbeat_interval * 1000},
    })

    try:
        raw = await asyncio.wait_for(websocket.receive_json(), timeout=30)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await websocket.close(4001, "Identify timeout")
        return

    parsed = parse_client_message(raw)
    if not isinstance(parsed, IdentifyMessage):
        await websocket.close(4002, "First message must be identify")
        return

    user_id = verify_jwt(parsed.token, settings.jwt_secret)
    if user_id is None:
        await websocket.close(4003, "Invalid token")
        return

    conn_registry = _get_connection_registry(websocket)
    if conn_registry.count(user_id) >= settings.max_connections_per_user:
        await websocket.close(4004, "Max connections exceeded")
        return

    state.user_id = user_id
    state.authenticated = True
    conn_registry.connect(user_id, websocket)

    await send_event(state, SERVER_READY, "", {
        "user_id": user_id,
        "ws_session_id": state.ws_session_id,
        "conversations": [],
    })

    task_manager = _get_task_manager(websocket)
    heartbeat_task_id = task_manager.spawn(
        f"ws-heartbeat:{state.ws_session_id}",
        _heartbeat_loop(state, settings),
        category="heartbeat",
    )

    try:
        await _dispatch_loop(state, settings, task_manager)
    except WebSocketDisconnect:
        pass
    finally:
        await task_manager.cancel(heartbeat_task_id)
        conn_registry.disconnect(user_id, websocket)


async def _heartbeat_loop(state: ConnectionState, settings: WebSettings) -> None:
    try:
        while True:
            await asyncio.sleep(settings.heartbeat_interval)
            try:
                await state.ws.send_json({"type": SERVER_PING})
            except Exception:
                return
            if time.time() - state.last_pong > settings.pong_timeout + settings.heartbeat_interval:
                logger.warning("pong timeout for user %s, closing", state.user_id)
                try:
                    await state.ws.close(4005, "Pong timeout")
                except Exception:
                    pass
                return
    except asyncio.CancelledError:
        return


async def _dispatch_loop(
    state: ConnectionState, settings: WebSettings, task_manager: TaskManager,
) -> None:
    while True:
        raw = await state.ws.receive_json()
        msg = parse_client_message(raw)

        if isinstance(msg, PongMessage):
            state.last_pong = time.time()

        elif isinstance(msg, ChatSendMessage):
            from pyclaw.channels.web.chat import enqueue_chat
            task_manager.spawn(
                f"ws-enqueue:{state.ws_session_id}",
                enqueue_chat(state, msg, settings),
                category="generic",
                on_error=lambda e: logger.warning("ws-enqueue failed: %s", e),
            )

        elif isinstance(msg, ChatAbortMessage):
            from pyclaw.channels.web.chat import handle_abort
            await handle_abort(state, msg)

        elif isinstance(msg, ToolApproveMessage):
            from pyclaw.channels.web.chat import handle_tool_approve
            await handle_tool_approve(state, msg)

        else:
            await send_event(state, SERVER_ERROR, "", {
                "message": f"Unknown message type: {raw.get('type', '?')}",
            })
