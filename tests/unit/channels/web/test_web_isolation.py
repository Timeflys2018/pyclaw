from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from pyclaw.channels.session_router import SessionRouter
from pyclaw.channels.web.auth import create_jwt
from pyclaw.channels.web.chat import _run_chat
from pyclaw.channels.web.openai_compat import openai_router, set_openai_deps
from pyclaw.channels.web.protocol import (
    SERVER_ERROR,
    ChatSendMessage,
)
from pyclaw.channels.web.websocket import ConnectionState
from pyclaw.infra.settings import WebSettings
from pyclaw.models.agent import Done
from pyclaw.storage.session.base import InMemorySessionStore

JWT_SECRET = "test-secret"


def _make_mock_ws(workspace_base: Path | None = None) -> AsyncMock:
    mock_ws = AsyncMock()
    if workspace_base is None:
        workspace_base = Path(tempfile.mkdtemp())
    mock_ws.app.state.workspace_base = workspace_base
    return mock_ws


def _patch_agent_stream():
    from contextlib import contextmanager

    captured_kwargs: list[dict[str, Any]] = []

    async def _fake_stream(*args: Any, **kwargs: Any):
        captured_kwargs.append(kwargs)
        yield Done(final_message="ok", usage={})

    fake_deps = MagicMock(spec=["llm", "tools", "config"])

    @contextmanager
    def _combined():
        with (
            patch("pyclaw.channels.web.chat.run_agent_stream", side_effect=_fake_stream),
            patch("pyclaw.channels.web.chat._get_runner_deps", return_value=fake_deps),
        ):
            yield captured_kwargs

    return _combined()


class TestConversationIdOwnership:
    @pytest.mark.asyncio
    async def test_cross_user_conversation_id_rejected(self) -> None:
        workspace_base = Path(tempfile.mkdtemp())
        mock_ws = _make_mock_ws(workspace_base)
        state = ConnectionState(ws=mock_ws, ws_session_id="s1", user_id="me", authenticated=True)
        settings = WebSettings(jwt_secret="s", heartbeat_interval=60, pong_timeout=10)

        msg = ChatSendMessage(conversation_id="web:other_user:xxx", content="hi")

        with _patch_agent_stream() as captured:
            await _run_chat(state, msg, settings)

        assert len(captured) == 0

        sent_calls = mock_ws.send_json.call_args_list
        assert len(sent_calls) >= 1
        payload = sent_calls[0][0][0]
        assert payload["type"] == SERVER_ERROR
        assert "Access denied" in payload["data"]["message"]

    @pytest.mark.asyncio
    async def test_own_prefixed_conversation_id_accepted(self) -> None:
        workspace_base = Path(tempfile.mkdtemp())
        mock_ws = _make_mock_ws(workspace_base)
        state = ConnectionState(ws=mock_ws, ws_session_id="s1", user_id="me", authenticated=True)
        settings = WebSettings(jwt_secret="s", heartbeat_interval=60, pong_timeout=10)

        msg = ChatSendMessage(conversation_id="web:me:my_conv", content="hi")

        with _patch_agent_stream() as captured:
            await _run_chat(state, msg, settings)

        assert len(captured) == 1

    @pytest.mark.asyncio
    async def test_plain_id_gets_user_prefix(self) -> None:
        workspace_base = Path(tempfile.mkdtemp())
        mock_ws = _make_mock_ws(workspace_base)
        state = ConnectionState(ws=mock_ws, ws_session_id="s1", user_id="me", authenticated=True)
        settings = WebSettings(jwt_secret="s", heartbeat_interval=60, pong_timeout=10)

        msg = ChatSendMessage(conversation_id="plain_id", content="hi")

        captured_requests: list[Any] = []

        async def _fake_stream(request: Any, *args: Any, **kwargs: Any):
            captured_requests.append(request)
            yield Done(final_message="ok", usage={})

        fake_deps = MagicMock(spec=["llm", "tools", "config"])
        with (
            patch("pyclaw.channels.web.chat.run_agent_stream", side_effect=_fake_stream),
            patch("pyclaw.channels.web.chat._get_runner_deps", return_value=fake_deps),
        ):
            await _run_chat(state, msg, settings)

        assert len(captured_requests) == 1
        assert captured_requests[0].session_id == "web:me:plain_id"


class TestToolWorkspacePath:
    @pytest.mark.asyncio
    async def test_workspace_resolves_to_per_user_dir(self) -> None:
        workspace_base = Path(tempfile.mkdtemp())
        mock_ws = _make_mock_ws(workspace_base)
        state = ConnectionState(
            ws=mock_ws, ws_session_id="s1", user_id="testuser", authenticated=True
        )
        settings = WebSettings(jwt_secret="s", heartbeat_interval=60, pong_timeout=10)

        msg = ChatSendMessage(conversation_id="c1", content="hi")

        with _patch_agent_stream() as captured:
            await _run_chat(state, msg, settings)

        assert len(captured) == 1
        tool_workspace = captured[0]["tool_workspace_path"]
        expected = workspace_base / "web_testuser"
        assert Path(tool_workspace) == expected
        assert expected.is_dir()


class TestOpenaiCompatIgnoresBodyUser:
    @patch("pyclaw.channels.web.openai_compat.run_agent_stream")
    def test_body_user_ignored_session_uses_jwt(self, mock_stream: MagicMock) -> None:
        workspace_base = Path(tempfile.mkdtemp())
        app = FastAPI()
        settings = WebSettings(jwt_secret=JWT_SECRET)
        app.state.web_settings = settings
        app.include_router(openai_router)

        store = InMemorySessionStore()
        router = SessionRouter(store=store)
        deps = MagicMock()
        deps.session_store = store
        set_openai_deps(deps=deps, session_router=router, workspace_base=workspace_base)

        captured_requests: list[Any] = []

        async def _fake_stream(request: Any, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            captured_requests.append(request)
            yield Done(final_message="ok", usage={})

        mock_stream.side_effect = _fake_stream

        token = create_jwt("real_user", JWT_SECRET)
        client = TestClient(app)
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
                "user": "attacker",
            },
        )

        assert len(captured_requests) == 1
        assert "openai:real_user" in captured_requests[0].session_id
        assert "attacker" not in captured_requests[0].session_id

    @patch("pyclaw.channels.web.openai_compat.run_agent_stream")
    def test_workspace_uses_per_user_path(self, mock_stream: MagicMock) -> None:
        workspace_base = Path(tempfile.mkdtemp())
        app = FastAPI()
        settings = WebSettings(jwt_secret=JWT_SECRET)
        app.state.web_settings = settings
        app.include_router(openai_router)

        store = InMemorySessionStore()
        router = SessionRouter(store=store)
        deps = MagicMock()
        deps.session_store = store
        set_openai_deps(deps=deps, session_router=router, workspace_base=workspace_base)

        captured_kwargs: list[dict[str, Any]] = []

        async def _fake_stream(request: Any, deps: Any, **kwargs: Any) -> AsyncIterator[Any]:
            captured_kwargs.append(kwargs)
            yield Done(final_message="ok", usage={})

        mock_stream.side_effect = _fake_stream

        token = create_jwt("myuser", JWT_SECRET)
        client = TestClient(app)
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

        assert len(captured_kwargs) == 1
        tool_workspace = captured_kwargs[0]["tool_workspace_path"]
        expected = workspace_base / "web_myuser"
        assert Path(tool_workspace) == expected
        assert expected.is_dir()
