"""Sprint 3 Phase 3 T3.5/T3.5b/T3.5c — /admin user command.

Spec anchors:
- spec.md "/admin user slash command" Requirement + 4 scenarios
- 4-slot review F4: last-admin-protection guard
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.auth.profile import UserProfile
from pyclaw.auth.profile_store import RedisJsonStore
from pyclaw.core.commands.admin import cmd_admin


def _make_ctx(
    *,
    user_id: str = "alice",
    role: str = "admin",
    channel: str = "web",
    user_profile_store: Any = None,
    replies: list[str] | None = None,
) -> MagicMock:
    ctx = MagicMock()
    ctx.user_id = user_id
    ctx.channel = channel
    ctx.session_id = "s1"
    ctx.session_key = "sk1"
    ctx.workspace_id = "ws1"

    replies = replies if replies is not None else []
    async def _reply(text: str) -> None:
        replies.append(text)

    ctx.reply = _reply
    ctx.replies = replies

    ctx.raw = {"user_role": role}
    ctx.user_profile_store = user_profile_store
    ctx.redis_client = None
    settings = MagicMock()
    settings.channels.web.users = []
    settings.channels.feishu.users = []
    ctx.settings = settings
    return ctx


@pytest.fixture
def admin_store() -> RedisJsonStore:
    redis = AsyncMock()
    redis.setex = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock(return_value=1)

    async def _scan_iter(match: str = "", count: int | None = None):
        if False:
            yield b""
        return

    redis.scan_iter = _scan_iter
    return RedisJsonStore(redis_client=redis, json_source={})


class TestPermissionCheck:
    @pytest.mark.asyncio
    async def test_member_role_denied(self, admin_store: RedisJsonStore) -> None:
        ctx = _make_ctx(role="member", user_profile_store=admin_store)
        await cmd_admin("user list", ctx)
        assert any("Permission denied" in r or "❌" in r for r in ctx.replies)

    @pytest.mark.asyncio
    async def test_admin_role_allowed(self, admin_store: RedisJsonStore) -> None:
        ctx = _make_ctx(role="admin", user_profile_store=admin_store)
        await cmd_admin("user list", ctx)
        assert not any("Permission denied" in r for r in ctx.replies)


class TestSetSubcommand:
    @pytest.mark.asyncio
    async def test_set_persists_profile(self, admin_store: RedisJsonStore) -> None:
        ctx = _make_ctx(user_profile_store=admin_store)
        await cmd_admin("user set bob tier=read-only role=member", ctx)
        assert any("✅" in r or "updated" in r.lower() for r in ctx.replies)
        admin_store._redis.setex.assert_awaited()

    @pytest.mark.asyncio
    async def test_set_invalid_tier_rejected(self, admin_store: RedisJsonStore) -> None:
        ctx = _make_ctx(user_profile_store=admin_store)
        await cmd_admin("user set bob tier=bogus", ctx)
        assert any("❌" in r or "invalid" in r.lower() for r in ctx.replies)


class TestLastAdminProtection:
    """4-slot review F4 — sole admin cannot self-demote to member."""

    @pytest.mark.asyncio
    async def test_sole_admin_self_demote_rejected(self) -> None:
        redis = AsyncMock()
        redis.setex = AsyncMock()
        redis.get = AsyncMock(
            return_value=b'{"channel":"web","user_id":"alice","role":"admin"}'
        )

        async def _scan_iter(match: str = "", count: int | None = None):
            yield b"pyclaw:userprofile:web:alice"

        redis.scan_iter = _scan_iter
        store = RedisJsonStore(redis_client=redis, json_source={})

        ctx = _make_ctx(user_id="alice", user_profile_store=store)
        await cmd_admin("user set alice role=member", ctx)

        assert any(
            "last admin" in r.lower() or "last-admin" in r.lower() for r in ctx.replies
        )
        redis.setex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_self_demote_allowed_when_other_admins_exist(self) -> None:
        redis = AsyncMock()
        redis.setex = AsyncMock()
        redis.get = AsyncMock(
            return_value=b'{"channel":"web","user_id":"alice","role":"admin"}'
        )

        async def _scan_iter(match: str = "", count: int | None = None):
            yield b"pyclaw:userprofile:web:alice"
            yield b"pyclaw:userprofile:web:bob"

        redis.scan_iter = _scan_iter

        async def _get(key: bytes | str) -> bytes:
            k = key.decode() if isinstance(key, bytes) else key
            if k.endswith(":alice"):
                return b'{"channel":"web","user_id":"alice","role":"admin"}'
            return b'{"channel":"web","user_id":"bob","role":"admin"}'

        redis.get = AsyncMock(side_effect=_get)

        store = RedisJsonStore(redis_client=redis, json_source={})
        ctx = _make_ctx(user_id="alice", user_profile_store=store)
        await cmd_admin("user set alice role=member", ctx)

        assert not any(
            "last admin" in r.lower() or "last-admin" in r.lower() for r in ctx.replies
        )
        redis.setex.assert_awaited()


class TestListSubcommand:
    @pytest.mark.asyncio
    async def test_list_returns_table(self, admin_store: RedisJsonStore) -> None:
        ctx = _make_ctx(user_profile_store=admin_store)
        await cmd_admin("user list", ctx)
        assert ctx.replies, "expected at least one reply"


class TestSandboxCheck:
    """Sprint 3 Phase 4 — /admin sandbox check command."""

    @pytest.mark.asyncio
    async def test_sandbox_check_admin_only(self, admin_store: RedisJsonStore) -> None:
        ctx = _make_ctx(role="member", user_profile_store=admin_store)
        await cmd_admin("sandbox check", ctx)
        assert any("Permission denied" in r for r in ctx.replies)

    @pytest.mark.asyncio
    async def test_sandbox_check_includes_backend_and_servers(
        self, admin_store: RedisJsonStore
    ) -> None:
        from pyclaw.integrations.mcp.settings import (
            McpServerConfig,
            McpSandboxConfig,
            McpSettings,
        )
        from pyclaw.sandbox.state import SandboxState
        from pyclaw.sandbox.no_sandbox import NoSandboxPolicy

        ctx = _make_ctx(user_profile_store=admin_store)
        ctx.settings.mcp = McpSettings(
            enabled=True,
            servers={
                "fs": McpServerConfig(
                    command="/usr/local/bin/mcp-server-fs",
                    sandbox=McpSandboxConfig(enabled=True),
                ),
                "github": McpServerConfig(
                    command="npx",
                    args=["-y", "@github/server"],
                ),
            },
        )

        mcp_manager = MagicMock()
        mcp_manager._servers = {}
        ctx.mcp_manager = mcp_manager

        ctx.raw["sandbox_state"] = SandboxState(
            policy=NoSandboxPolicy(),
            backend="srt",
            srt_version="1.0.0",
            warning=None,
            override_active=False,
        )

        await cmd_admin("sandbox check", ctx)

        joined = "\n".join(ctx.replies)
        assert "backend=srt" in joined
        assert "1.0.0" in joined
        assert "fs" in joined
        assert "github" in joined

    @pytest.mark.asyncio
    async def test_sandbox_check_warns_npx_misconfig(
        self, admin_store: RedisJsonStore
    ) -> None:
        from pyclaw.integrations.mcp.settings import (
            McpServerConfig,
            McpSandboxConfig,
            McpSettings,
        )

        ctx = _make_ctx(user_profile_store=admin_store)
        ctx.settings.mcp = McpSettings(
            enabled=True,
            servers={
                "fs": McpServerConfig(
                    command="npx",
                    args=["-y", "@mcp/server-fs"],
                    sandbox=McpSandboxConfig(enabled=True),
                ),
            },
        )

        mcp_manager = MagicMock()
        mcp_manager._servers = {}
        ctx.mcp_manager = mcp_manager
        ctx.raw["sandbox_state"] = None

        await cmd_admin("sandbox check", ctx)

        joined = "\n".join(ctx.replies)
        assert "⚠️" in joined or "warning" in joined.lower()
        assert "registry" in joined.lower() or "npm" in joined.lower()


class TestShowSubcommand:
    @pytest.mark.asyncio
    async def test_show_returns_profile(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(
            return_value=b'{"channel":"web","user_id":"bob","role":"member","tier_default":"read-only"}'
        )

        async def _scan_iter(match: str = "", count: int | None = None):
            if False:
                yield b""
            return

        redis.scan_iter = _scan_iter
        store = RedisJsonStore(redis_client=redis, json_source={})

        ctx = _make_ctx(user_profile_store=store)
        await cmd_admin("user show bob", ctx)

        joined = "\n".join(ctx.replies)
        assert "bob" in joined
        assert "read-only" in joined
