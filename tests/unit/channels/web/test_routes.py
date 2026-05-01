from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from pyclaw.channels.web.auth import create_jwt
from pyclaw.channels.web.routes import web_router, set_web_deps
from pyclaw.channels.session_router import SessionRouter
from pyclaw.infra.settings import WebSettings
from pyclaw.models.session import MessageEntry, SessionTree
from pyclaw.storage.session.base import InMemorySessionStore


JWT_SECRET = "test-secret"


def _make_app() -> tuple[FastAPI, InMemorySessionStore]:
    app = FastAPI()
    settings = WebSettings(jwt_secret=JWT_SECRET)
    app.state.web_settings = settings
    app.include_router(web_router)

    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    set_web_deps(store=store, session_router=router)
    return app, store


def _auth_header(user_id: str = "user1") -> dict[str, str]:
    token = create_jwt(user_id, JWT_SECRET)
    return {"Authorization": f"Bearer {token}"}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestListSessions:
    def test_returns_empty_list_initially(self) -> None:
        app, _store = _make_app()
        client = TestClient(app)
        resp = client.get("/api/sessions", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_sessions_for_user(self) -> None:
        app, store = _make_app()
        client = TestClient(app)

        async def seed() -> str:
            tree = await store.create_new_session("web:user1", "default", "default")
            return tree.header.id

        sid = _run(seed())

        resp = client.get("/api/sessions", headers=_auth_header("user1"))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == sid

    def test_unauthenticated_returns_401(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.get("/api/sessions")
        assert resp.status_code == 401


class TestCreateSession:
    def test_creates_new_session(self) -> None:
        app, _store = _make_app()
        client = TestClient(app)
        resp = client.post("/api/sessions", headers=_auth_header("user1"))
        assert resp.status_code == 200
        body = resp.json()
        assert "session_id" in body
        assert isinstance(body["session_id"], str)

    def test_unauthenticated_returns_401(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/sessions")
        assert resp.status_code == 401


class TestGetSession:
    def test_returns_session_data(self) -> None:
        app, store = _make_app()
        client = TestClient(app)

        async def seed() -> str:
            tree = await store.create_new_session("web:user1", "default", "default")
            return tree.header.id

        sid = _run(seed())

        resp = client.get(f"/api/sessions/{sid}", headers=_auth_header("user1"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == sid

    def test_nonexistent_session_returns_404(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.get("/api/sessions/nosuch", headers=_auth_header("user1"))
        assert resp.status_code == 404

    def test_other_users_session_returns_403(self) -> None:
        app, store = _make_app()
        client = TestClient(app)

        async def seed() -> str:
            tree = await store.create_new_session("web:other", "default", "default")
            return tree.header.id

        sid = _run(seed())

        resp = client.get(f"/api/sessions/{sid}", headers=_auth_header("user1"))
        assert resp.status_code == 403


class TestGetMessages:
    def test_returns_paginated_messages(self) -> None:
        app, store = _make_app()
        client = TestClient(app)

        async def seed() -> str:
            tree = await store.create_new_session("web:user1", "default", "default")
            sid = tree.header.id
            for i in range(5):
                entry = MessageEntry(
                    id=f"e{i}",
                    parent_id=f"e{i-1}" if i > 0 else None,
                    role="user",
                    content=f"msg {i}",
                )
                await store.append_entry(sid, entry, leaf_id=f"e{i}")
            return sid

        sid = _run(seed())

        resp = client.get(
            f"/api/sessions/{sid}/messages?offset=0&limit=3",
            headers=_auth_header("user1"),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

    def test_returns_404_for_nonexistent_session(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.get(
            "/api/sessions/nosuch/messages", headers=_auth_header("user1")
        )
        assert resp.status_code == 404


class TestDeleteSession:
    def test_delete_returns_204(self) -> None:
        app, store = _make_app()
        client = TestClient(app)

        async def seed() -> str:
            tree = await store.create_new_session("web:user1", "default", "default")
            return tree.header.id

        sid = _run(seed())

        resp = client.delete(f"/api/sessions/{sid}", headers=_auth_header("user1"))
        assert resp.status_code == 204

    def test_delete_nonexistent_returns_404(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.delete("/api/sessions/nosuch", headers=_auth_header("user1"))
        assert resp.status_code == 404
