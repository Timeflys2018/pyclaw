from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from pyclaw.channels.web.auth import create_jwt
from pyclaw.channels.web.protocol import SERVER_HELLO, SERVER_READY, SERVER_ERROR
from pyclaw.channels.web.websocket import (
    ConnectionRegistry,
    ConnectionState,
    send_event,
    ws_router,
)
from pyclaw.infra.settings import WebSettings
from pyclaw.infra.task_manager import TaskManager


def _make_app(settings: WebSettings | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(ws_router)
    s = settings or WebSettings(jwt_secret="test-secret", heartbeat_interval=60, pong_timeout=10)
    app.state.web_settings = s
    app.state.task_manager = TaskManager()
    return app


def _valid_token(secret: str = "test-secret", user_id: str = "u1") -> str:
    return create_jwt(user_id, secret)


class TestConnectionRegistry:
    def test_connect_and_count(self) -> None:
        reg = ConnectionRegistry()
        ws = object()
        reg.connect("u1", ws)
        assert reg.count("u1") == 1

    def test_disconnect_decrements(self) -> None:
        reg = ConnectionRegistry()
        ws = object()
        reg.connect("u1", ws)
        reg.disconnect("u1", ws)
        assert reg.count("u1") == 0

    def test_count_unknown_user_is_zero(self) -> None:
        reg = ConnectionRegistry()
        assert reg.count("nobody") == 0

    def test_get_connections(self) -> None:
        reg = ConnectionRegistry()
        ws1, ws2 = object(), object()
        reg.connect("u1", ws1)
        reg.connect("u1", ws2)
        conns = reg.get_connections("u1")
        assert ws1 in conns and ws2 in conns
        assert len(conns) == 2

    def test_disconnect_missing_ws_is_safe(self) -> None:
        reg = ConnectionRegistry()
        reg.disconnect("u1", object())
        assert reg.count("u1") == 0

    def test_clear_empties_registry(self) -> None:
        reg = ConnectionRegistry()
        reg.connect("u1", object())
        reg.connect("u2", object())
        reg.clear()
        assert reg.count("u1") == 0
        assert reg.count("u2") == 0


class TestConnectionState:
    def test_initial_state(self) -> None:
        ws = object()
        state = ConnectionState(ws=ws, ws_session_id="abc123")
        assert state.user_id is None
        assert state.seq == 0
        assert state.authenticated is False
        assert state.ws is ws
        assert state.ws_session_id == "abc123"


class TestSendEvent:
    @pytest.mark.asyncio
    async def test_increments_seq(self) -> None:
        mock_ws = AsyncMock()
        state = ConnectionState(ws=mock_ws, ws_session_id="s1")
        seq1 = await send_event(state, "test.event", "conv-1", {"hello": "world"})
        seq2 = await send_event(state, "test.event", "conv-1", {"hello": "again"})
        assert seq1 == 1
        assert seq2 == 2
        assert state.seq == 2

    @pytest.mark.asyncio
    async def test_envelope_shape(self) -> None:
        mock_ws = AsyncMock()
        state = ConnectionState(ws=mock_ws, ws_session_id="s1")
        before = int(time.time() * 1000)
        await send_event(state, "chat.delta", "conv-1", {"text": "hi"})
        after = int(time.time() * 1000)

        call_args = mock_ws.send_json.call_args[0][0]
        assert call_args["type"] == "chat.delta"
        assert call_args["conversation_id"] == "conv-1"
        assert call_args["seq"] == 1
        assert call_args["data"] == {"text": "hi"}
        assert before <= call_args["ts"] <= after + 1


class TestWebSocketEndpoint:
    @pytest.fixture(autouse=True)
    def _reset_registry(self) -> None:
        from pyclaw.channels.web.websocket import registry
        registry._connections.clear()

    def test_hello_then_identify_then_ready(self) -> None:
        app = _make_app()
        client = TestClient(app)
        token = _valid_token()
        with client.websocket_connect("/api/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == SERVER_HELLO
            assert "heartbeat_interval" in hello["data"]

            ws.send_json({"type": "identify", "token": token})
            ready = ws.receive_json()
            assert ready["type"] == SERVER_READY
            assert ready["data"]["user_id"] == "u1"
            assert "ws_session_id" in ready["data"]

    def test_bad_token_closes_4003(self) -> None:
        app = _make_app()
        client = TestClient(app)
        with client.websocket_connect("/api/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "identify", "token": "bad.token.here"})
            with pytest.raises(Exception):
                ws.receive_json()

    def test_non_identify_first_closes_4002(self) -> None:
        app = _make_app()
        client = TestClient(app)
        with client.websocket_connect("/api/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "chat.send", "content": "hi", "conversation_id": "c1"})
            with pytest.raises(Exception):
                ws.receive_json()

    def test_max_connections_closes_4004(self) -> None:
        settings = WebSettings(
            jwt_secret="test-secret",
            heartbeat_interval=60,
            pong_timeout=10,
            max_connections_per_user=1,
        )
        app = _make_app(settings)
        client = TestClient(app)
        token = _valid_token()

        with client.websocket_connect("/api/ws") as ws1:
            ws1.receive_json()
            ws1.send_json({"type": "identify", "token": token})
            ready = ws1.receive_json()
            assert ready["type"] == SERVER_READY

            with client.websocket_connect("/api/ws") as ws2:
                ws2.receive_json()
                ws2.send_json({"type": "identify", "token": token})
                with pytest.raises(Exception):
                    ws2.receive_json()

    def test_pong_keeps_connection_alive(self) -> None:
        app = _make_app()
        client = TestClient(app)
        token = _valid_token()
        with client.websocket_connect("/api/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "identify", "token": token})
            ws.receive_json()
            ws.send_json({"type": "pong"})
            ws.send_json({"type": "pong"})

    def test_unknown_message_sends_error(self) -> None:
        app = _make_app()
        client = TestClient(app)
        token = _valid_token()
        with client.websocket_connect("/api/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "identify", "token": token})
            ws.receive_json()
            ws.send_json({"type": "totally.unknown"})
            err = ws.receive_json()
            assert err["type"] == SERVER_ERROR

    def test_seq_is_monotonic(self) -> None:
        app = _make_app()
        client = TestClient(app)
        token = _valid_token()
        with client.websocket_connect("/api/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "identify", "token": token})
            ready = ws.receive_json()
            assert ready["seq"] == 1

            ws.send_json({"type": "totally.unknown"})
            err = ws.receive_json()
            assert err["seq"] == 2
