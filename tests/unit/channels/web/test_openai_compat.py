from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from pyclaw.channels.web.auth import create_jwt
from pyclaw.channels.web.openai_compat import openai_router, set_openai_deps
from pyclaw.channels.session_router import SessionRouter
from pyclaw.infra.settings import WebSettings
from pyclaw.models.agent import Done, ErrorEvent, TextChunk, ToolCallStart, ToolCallEnd
from pyclaw.storage.session.base import InMemorySessionStore


JWT_SECRET = "test-secret"


def _make_app() -> tuple[FastAPI, InMemorySessionStore]:
    app = FastAPI()
    settings = WebSettings(jwt_secret=JWT_SECRET)
    app.state.web_settings = settings
    app.include_router(openai_router)

    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    deps = MagicMock()
    deps.session_store = store
    set_openai_deps(deps=deps, session_router=router)
    return app, store


def _auth_header(user_id: str = "user1") -> dict[str, str]:
    token = create_jwt(user_id, JWT_SECRET)
    return {"Authorization": f"Bearer {token}"}


class TestListModels:
    def test_returns_model_list(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.get("/v1/models", headers=_auth_header())
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert len(data["data"]) >= 1
        assert data["data"][0]["id"] == "pyclaw-default"
        assert data["data"][0]["object"] == "model"


class TestChatCompletionsNonStreaming:
    @patch("pyclaw.channels.web.openai_compat.run_agent_stream")
    def test_returns_openai_format(self, mock_stream: MagicMock) -> None:
        app, _ = _make_app()

        async def _fake_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            yield TextChunk(text="Hello ")
            yield TextChunk(text="world!")
            yield Done(final_message="Hello world!", usage={"prompt_tokens": 10, "completion_tokens": 5})

        mock_stream.side_effect = _fake_stream

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            headers=_auth_header(),
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "chat.completion"
        assert data["model"] == "test-model"
        assert len(data["choices"]) == 1
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert data["choices"][0]["message"]["content"] == "Hello world!"
        assert data["choices"][0]["finish_reason"] == "stop"

    def test_no_user_message_returns_400(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            headers=_auth_header(),
            json={
                "model": "test-model",
                "messages": [{"role": "system", "content": "you are a bot"}],
            },
        )
        assert resp.status_code == 400

    def test_unauthenticated_returns_401(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401


class TestChatCompletionsStreaming:
    @patch("pyclaw.channels.web.openai_compat.run_agent_stream")
    def test_streaming_returns_sse_chunks(self, mock_stream: MagicMock) -> None:
        app, _ = _make_app()

        async def _fake_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            yield TextChunk(text="one")
            yield TextChunk(text="two")
            yield Done(final_message="onetwo", usage={})

        mock_stream.side_effect = _fake_stream

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            headers=_auth_header(),
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        lines = resp.text.strip().split("\n\n")
        data_lines = [l for l in lines if l.startswith("data: ")]
        assert len(data_lines) >= 3

        first = json.loads(data_lines[0].removeprefix("data: "))
        assert first["object"] == "chat.completion.chunk"
        assert first["choices"][0]["delta"]["role"] == "assistant"

        text_chunk = json.loads(data_lines[1].removeprefix("data: "))
        assert text_chunk["choices"][0]["delta"]["content"] == "one"

        done_line = [l for l in data_lines if "finish_reason" in l and '"stop"' in l]
        assert len(done_line) >= 1

        assert data_lines[-1] == "data: [DONE]"

    @patch("pyclaw.channels.web.openai_compat.run_agent_stream")
    def test_streaming_skips_non_text_events(self, mock_stream: MagicMock) -> None:
        app, _ = _make_app()

        async def _fake_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            yield ToolCallStart(
                tool_call_id="tc1", name="bash", arguments={"cmd": "ls"}
            )
            yield TextChunk(text="result")
            yield Done(final_message="result", usage={})

        mock_stream.side_effect = _fake_stream

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            headers=_auth_header(),
            json={
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200
        text_chunks = [
            l
            for l in resp.text.strip().split("\n\n")
            if l.startswith("data: ") and '"content"' in l
        ]
        assert len(text_chunks) == 1
        parsed = json.loads(text_chunks[0].removeprefix("data: "))
        assert parsed["choices"][0]["delta"]["content"] == "result"


class TestChatCompletionsUserField:
    @patch("pyclaw.channels.web.openai_compat.run_agent_stream")
    def test_user_field_overrides_session_key(self, mock_stream: MagicMock) -> None:
        app, _ = _make_app()

        captured_requests: list[Any] = []

        async def _fake_stream(request: Any, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            captured_requests.append(request)
            yield Done(final_message="ok", usage={})

        mock_stream.side_effect = _fake_stream

        client = TestClient(app)
        client.post(
            "/v1/chat/completions",
            headers=_auth_header(),
            json={
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
                "user": "custom-user",
            },
        )
        assert len(captured_requests) == 1
        assert "openai:custom-user" in captured_requests[0].session_id
