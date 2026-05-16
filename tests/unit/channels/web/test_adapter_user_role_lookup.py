"""Sprint 3 hotfix: WebCommandAdapter populates ctx.raw['user_role'].

Regression test for the bug discovered during Sprint 3 真机 smoke (2026-05-16):
`/admin user set bob role=admin` writes Redis; bob then logs in and sends
`/admin user list` — but `_is_admin(ctx)` was reading `ctx.raw['user_role']`
which the command adapter never populated, falling through to Sprint 1's
`admin_user_ids` and rejecting the new admin.

Fix: command_adapter looks up UserProfile via resolve_profile_and_tier and
threads `user_profile.role` into `ctx.raw['user_role']`.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.web.command_adapter import WebCommandAdapter
from pyclaw.channels.web.websocket import ConnectionState
from pyclaw.core.commands.registry import CommandRegistry
from pyclaw.core.commands.spec import ALL_CHANNELS, CommandSpec
from pyclaw.infra.settings import FeishuSettings, Settings, WebSettings, WebUserConfig


def _make_state(user_id: str) -> tuple[ConnectionState, AsyncMock]:
    mock_ws = AsyncMock()
    mock_ws.app.state.workspace_base = Path(tempfile.mkdtemp())
    state = ConnectionState(
        ws=mock_ws,
        ws_session_id="s1",
        user_id=user_id,
        authenticated=True,
    )
    return state, mock_ws


def _make_settings(users: list[WebUserConfig]) -> Settings:
    s = Settings()
    s.channels.web = WebSettings(users=users, default_permission_tier="approval")
    return s


@pytest.mark.asyncio
async def test_admin_role_from_json_propagated_to_ctx_raw() -> None:
    captured: dict = {}

    async def handler(args: str, ctx) -> None:
        captured["user_role"] = ctx.raw.get("user_role")
        captured["user_id"] = ctx.user_id
        await ctx.reply("ok")

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="/probe",
            handler=handler,
            category="test",
            help_text="probe",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )

    state, _ = _make_state(user_id="alice")
    settings = _make_settings([
        WebUserConfig(id="alice", password="x", role="admin", tier_default="yolo"),
    ])

    adapter = WebCommandAdapter(registry=registry)
    handled = await adapter.handle(
        text="/probe",
        state=state,
        conversation_id="conv-x",
        session_id="web:alice:conv-x",
        deps=MagicMock(),
        session_router=MagicMock(),
        workspace_base=Path(tempfile.mkdtemp()),
        settings=settings,
        redis_client=None,
    )

    assert handled is True
    assert captured["user_id"] == "alice"
    assert captured["user_role"] == "admin", (
        f"expected user_role='admin' from JSON UserProfile, got {captured['user_role']!r}"
    )


@pytest.mark.asyncio
async def test_member_role_from_json_propagated() -> None:
    captured: dict = {}

    async def handler(args: str, ctx) -> None:
        captured["user_role"] = ctx.raw.get("user_role")
        await ctx.reply("ok")

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="/probe",
            handler=handler,
            category="test",
            help_text="probe",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )

    state, _ = _make_state(user_id="bob")
    settings = _make_settings([
        WebUserConfig(id="bob", password="x", role="member"),
    ])

    adapter = WebCommandAdapter(registry=registry)
    await adapter.handle(
        text="/probe",
        state=state,
        conversation_id="conv-y",
        session_id="web:bob:conv-y",
        deps=MagicMock(),
        session_router=MagicMock(),
        workspace_base=Path(tempfile.mkdtemp()),
        settings=settings,
        redis_client=None,
    )

    assert captured["user_role"] == "member"


@pytest.mark.asyncio
async def test_unknown_user_defaults_to_member_role() -> None:
    captured: dict = {}

    async def handler(args: str, ctx) -> None:
        captured["user_role"] = ctx.raw.get("user_role")
        await ctx.reply("ok")

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="/probe",
            handler=handler,
            category="test",
            help_text="probe",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )

    state, _ = _make_state(user_id="mallory")
    settings = _make_settings([])

    adapter = WebCommandAdapter(registry=registry)
    await adapter.handle(
        text="/probe",
        state=state,
        conversation_id="conv-z",
        session_id="web:mallory:conv-z",
        deps=MagicMock(),
        session_router=MagicMock(),
        workspace_base=Path(tempfile.mkdtemp()),
        settings=settings,
        redis_client=None,
    )

    assert captured["user_role"] == "member"


@pytest.mark.asyncio
async def test_redis_admin_overrides_json_member() -> None:
    """Sprint 3 spec invariant: Redis profile shadows JSON for the same user_id.

    This is the production scenario from 2026-05-16 真机 smoke: bob is `member` in
    JSON but admin promoted bob to `admin` via /admin user set, writing Redis.
    Subsequent /admin probes from bob must see role=admin.
    """
    captured: dict = {}

    async def handler(args: str, ctx) -> None:
        captured["user_role"] = ctx.raw.get("user_role")
        await ctx.reply("ok")

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="/probe",
            handler=handler,
            category="test",
            help_text="probe",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )

    state, _ = _make_state(user_id="bob")
    settings = _make_settings([
        WebUserConfig(id="bob", password="x", role="member"),
    ])

    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(
        return_value=b'{"channel":"web","user_id":"bob","role":"admin","tier_default":"yolo"}'
    )

    adapter = WebCommandAdapter(registry=registry)
    await adapter.handle(
        text="/probe",
        state=state,
        conversation_id="conv-redis",
        session_id="web:bob:conv-redis",
        deps=MagicMock(),
        session_router=MagicMock(),
        workspace_base=Path(tempfile.mkdtemp()),
        settings=settings,
        redis_client=redis_mock,
    )

    assert captured["user_role"] == "admin", (
        f"Redis admin must shadow JSON member; got {captured['user_role']!r}"
    )
