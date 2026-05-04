from __future__ import annotations

import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from pyclaw.channels.session_router import SessionRouter
from pyclaw.channels.web.auth import create_jwt
from pyclaw.channels.web.auth_routes import auth_router
from pyclaw.channels.web.openai_compat import openai_router, set_openai_deps
from pyclaw.channels.web.protocol import SERVER_HELLO, SERVER_READY
from pyclaw.channels.web.routes import set_web_deps, web_router
from pyclaw.channels.web.websocket import ws_router
from pyclaw.infra.settings import WebSettings, WebUserConfig
from pyclaw.infra.task_manager import TaskManager
from pyclaw.models.agent import Done, TextChunk
from pyclaw.storage.session.base import InMemorySessionStore


JWT_SECRET = "integration-test-secret"
ADMIN_TOKEN = "admin-token"


def _make_app() -> tuple[FastAPI, InMemorySessionStore]:
    app = FastAPI()
    settings = WebSettings(
        jwt_secret=JWT_SECRET,
        admin_token=ADMIN_TOKEN,
        heartbeat_interval=60,
        pong_timeout=10,
        users=[WebUserConfig(id="testuser", password="testpass")],
    )
    app.state.web_settings = settings
    app.state.task_manager = TaskManager()

    store = InMemorySessionStore()
    session_router = SessionRouter(store=store)

    set_web_deps(store=store, session_router=session_router)

    deps = MagicMock()
    deps.session_store = store
    set_openai_deps(deps=deps, session_router=session_router)

    app.include_router(auth_router)
    app.include_router(web_router)
    app.include_router(ws_router)
    app.include_router(openai_router)
    return app, store


def _auth_header(user_id: str = "testuser") -> dict[str, str]:
    token = create_jwt(user_id, JWT_SECRET)
    return {"Authorization": f"Bearer {token}"}


class TestAuthToSessionFlow:
    def test_login_then_create_session_then_list(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/auth/token",
            json={"user_id": "testuser", "password": "testpass"},
        )
        assert resp.status_code == 200
        token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post("/api/sessions", headers=headers)
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]
        assert session_id

        resp = client.get("/api/sessions", headers=headers)
        assert resp.status_code == 200
        sessions = resp.json()
        assert any(s["id"] == session_id for s in sessions)

    def test_invalid_credentials_rejected(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/auth/token",
            json={"user_id": "testuser", "password": "wrong"},
        )
        assert resp.status_code == 401


class TestWebSocketIntegration:
    def test_ws_connect_identify_ready(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)
        token = create_jwt("testuser", JWT_SECRET)

        with client.websocket_connect("/api/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == SERVER_HELLO

            ws.send_json({"type": "identify", "token": token})
            ready = ws.receive_json()
            assert ready["type"] == SERVER_READY
            assert ready["data"]["user_id"] == "testuser"

    def test_ws_reject_bad_token(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)

        with client.websocket_connect("/api/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "identify", "token": "invalid.jwt.token"})
            with pytest.raises(Exception):
                ws.receive_json()


class TestOpenAICompatIntegration:
    @patch("pyclaw.channels.web.openai_compat.run_agent_stream")
    def test_non_streaming_end_to_end(self, mock_stream: MagicMock) -> None:
        app, _ = _make_app()

        async def _fake_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            yield TextChunk(text="Hello from PyClaw!")
            yield Done(final_message="Hello from PyClaw!", usage={"prompt_tokens": 5, "completion_tokens": 4})

        mock_stream.side_effect = _fake_stream

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            headers=_auth_header(),
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "chat.completion"
        assert data["choices"][0]["message"]["content"] == "Hello from PyClaw!"
        assert data["choices"][0]["finish_reason"] == "stop"
        assert data["usage"]["prompt_tokens"] == 5

    @patch("pyclaw.channels.web.openai_compat.run_agent_stream")
    def test_streaming_end_to_end(self, mock_stream: MagicMock) -> None:
        app, _ = _make_app()

        async def _fake_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            yield TextChunk(text="chunk1")
            yield TextChunk(text="chunk2")
            yield Done(final_message="chunk1chunk2", usage={})

        mock_stream.side_effect = _fake_stream

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            headers=_auth_header(),
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        lines = resp.text.strip().split("\n\n")
        data_lines = [l for l in lines if l.startswith("data: ")]

        role_chunk = json.loads(data_lines[0].removeprefix("data: "))
        assert role_chunk["choices"][0]["delta"]["role"] == "assistant"

        text_chunks = [
            json.loads(l.removeprefix("data: "))
            for l in data_lines
            if l.startswith("data: ") and '"content"' in l
        ]
        texts = [c["choices"][0]["delta"]["content"] for c in text_chunks]
        assert "chunk1" in texts
        assert "chunk2" in texts

        assert data_lines[-1] == "data: [DONE]"

    def test_unauthenticated_request_rejected(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "test",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert resp.status_code == 401


class TestCrossRouterIntegration:
    @patch("pyclaw.channels.web.openai_compat.run_agent_stream")
    def test_auth_token_works_across_all_routers(self, mock_stream: MagicMock) -> None:
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/auth/token",
            json={"user_id": "testuser", "password": "testpass"},
        )
        assert resp.status_code == 200
        token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/api/sessions", headers=headers)
        assert resp.status_code == 200

        async def _fake_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            yield Done(final_message="ok", usage={})

        mock_stream.side_effect = _fake_stream

        resp = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert resp.status_code == 200

        resp = client.get("/v1/models", headers=headers)
        assert resp.status_code == 200
