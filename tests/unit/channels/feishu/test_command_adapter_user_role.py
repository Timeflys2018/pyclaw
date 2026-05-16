"""Sprint 3.0.3 hotfix — symmetric Feishu /admin user_role lookup.

3.0.1 hotfix (commit 37b0766) wired UserProfile lookup into Web's
command_adapter.py but missed Feishu's adapter. After 4-slot review v2,
we discovered the same bug exists symmetrically in
``feishu/command_adapter.py`` — runtime admin grant via
``/admin user set ou_xxx role=admin`` writes Redis but the new admin
cannot use ``/admin`` commands until added to ``settings.admin_user_ids``
+ restart.

This test mirrors ``test_adapter_user_role_lookup.py`` for Feishu.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.feishu.command_adapter import FeishuCommandAdapter
from pyclaw.core.commands.registry import CommandRegistry
from pyclaw.core.commands.spec import ALL_CHANNELS, CommandSpec
from pyclaw.infra.settings import (
    FeishuSettings,
    FeishuUserConfig,
    Settings,
)


def _make_event(open_id: str) -> MagicMock:
    sender = MagicMock()
    sender.sender_id.open_id = open_id
    event = MagicMock()
    event.event.sender = sender
    return event


def _make_ctx(*, settings_full: Settings) -> MagicMock:
    ctx = MagicMock()
    ctx.settings_full = settings_full
    ctx.deps = MagicMock()
    ctx.session_router = MagicMock()
    ctx.workspace_base = Path(tempfile.mkdtemp())
    ctx.redis_client = None
    ctx.memory_store = None
    ctx.evolution_settings = None
    ctx.nudge_hook = None
    ctx.agent_settings = None
    ctx.queue_registry = None
    ctx.admin_user_ids = []
    ctx.worker_registry = None
    ctx.gateway_router = None
    ctx.mcp_manager = None
    ctx.feishu_client = MagicMock()
    ctx.feishu_client.reply_text = AsyncMock()
    return ctx


def _make_settings(users: list[FeishuUserConfig]) -> Settings:
    s = Settings()
    s.channels.feishu = FeishuSettings(users=users, default_permission_tier="approval")
    return s


@pytest.mark.asyncio
async def test_feishu_admin_role_propagated_to_ctx_raw_user_role() -> None:
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

    settings = _make_settings([
        FeishuUserConfig(
            openId="ou_alice", role="admin", tier_default="yolo"
        ),
    ])
    ctx = _make_ctx(settings_full=settings)
    event = _make_event("ou_alice")

    adapter = FeishuCommandAdapter(registry=registry)
    handled = await adapter.handle(
        text="/probe",
        session_key="feishu:app:ou_alice",
        session_id="feishu:app:ou_alice:s:abc",
        message_id="msg-1",
        event=event,
        ctx=ctx,
    )

    assert handled is True
    assert captured["user_id"] == "ou_alice"
    assert captured["user_role"] == "admin"


@pytest.mark.asyncio
async def test_feishu_member_role_propagated() -> None:
    captured: dict = {}

    async def handler(args: str, ctx) -> None:
        captured["user_role"] = ctx.raw.get("user_role")
        await ctx.reply("ok")

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="/probe", handler=handler, category="test",
            help_text="probe", channels=ALL_CHANNELS, requires_idle=False,
        )
    )

    settings = _make_settings([
        FeishuUserConfig(openId="ou_bob", role="member"),
    ])
    ctx = _make_ctx(settings_full=settings)
    event = _make_event("ou_bob")

    adapter = FeishuCommandAdapter(registry=registry)
    await adapter.handle(
        text="/probe",
        session_key="feishu:app:ou_bob",
        session_id="feishu:app:ou_bob:s:abc",
        message_id="msg-2",
        event=event,
        ctx=ctx,
    )

    assert captured["user_role"] == "member"


@pytest.mark.asyncio
async def test_feishu_redis_admin_shadows_json_member() -> None:
    """Symmetric to web hotfix scenario: admin promotes ou_bob to admin via
    Redis; bob's next /admin command on Feishu must see role=admin."""
    captured: dict = {}

    async def handler(args: str, ctx) -> None:
        captured["user_role"] = ctx.raw.get("user_role")
        await ctx.reply("ok")

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="/probe", handler=handler, category="test",
            help_text="probe", channels=ALL_CHANNELS, requires_idle=False,
        )
    )

    settings = _make_settings([
        FeishuUserConfig(openId="ou_bob", role="member"),
    ])
    ctx = _make_ctx(settings_full=settings)

    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(
        return_value=b'{"channel":"feishu","user_id":"ou_bob","role":"admin","tier_default":"yolo"}'
    )
    ctx.redis_client = redis_mock

    event = _make_event("ou_bob")

    adapter = FeishuCommandAdapter(registry=registry)
    await adapter.handle(
        text="/probe",
        session_key="feishu:app:ou_bob",
        session_id="feishu:app:ou_bob:s:abc",
        message_id="msg-3",
        event=event,
        ctx=ctx,
    )

    assert captured["user_role"] == "admin", (
        f"4-slot v2 Feishu hotfix: Redis admin must shadow JSON member; "
        f"got {captured['user_role']!r}"
    )
