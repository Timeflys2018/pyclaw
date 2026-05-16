from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.core.commands.mcp import cmd_mcp


def _make_ctx(mcp_manager=None):
    ctx = MagicMock()
    ctx.mcp_manager = mcp_manager
    ctx.reply = AsyncMock()
    return ctx


def _make_status(name, status, tool_count=0, last=None, reason=None):
    s = MagicMock()
    s.name = name
    s.status = status
    s.tool_count = tool_count
    s.last_connect_at = last
    s.reason = reason
    return s


def _make_summary(connected=0, failed=0, pending=0, disabled=0, total=0):
    s = MagicMock()
    s.n_connected = connected
    s.n_failed = failed
    s.n_pending = pending
    s.n_disabled = disabled
    s.total_tools = total
    return s


class TestMcpDisabled:
    @pytest.mark.asyncio
    async def test_no_manager_explains(self):
        ctx = _make_ctx(mcp_manager=None)
        await cmd_mcp("list", ctx)
        msg = ctx.reply.call_args[0][0]
        assert "disabled" in msg.lower()
        assert "mcp.enabled" in msg


class TestMcpUsage:
    @pytest.mark.asyncio
    async def test_no_args_shows_usage(self):
        mgr = MagicMock()
        ctx = _make_ctx(mgr)
        await cmd_mcp("", ctx)
        msg = ctx.reply.call_args[0][0]
        assert "用法" in msg

    @pytest.mark.asyncio
    async def test_unknown_subcommand(self):
        mgr = MagicMock()
        ctx = _make_ctx(mgr)
        await cmd_mcp("dance", ctx)
        msg = ctx.reply.call_args[0][0]
        assert "unknown" in msg.lower()


class TestMcpList:
    @pytest.mark.asyncio
    async def test_list_no_servers(self):
        mgr = MagicMock()
        mgr.is_ready.return_value = True
        mgr.connection_summary.return_value = _make_summary()
        mgr.list_servers.return_value = []
        ctx = _make_ctx(mgr)
        await cmd_mcp("list", ctx)
        msg = ctx.reply.call_args[0][0]
        assert "no servers configured" in msg.lower()

    @pytest.mark.asyncio
    async def test_list_with_servers(self):
        mgr = MagicMock()
        mgr.is_ready.return_value = True
        mgr.connection_summary.return_value = _make_summary(connected=1, pending=1, total=11)
        mgr.list_servers.return_value = [
            _make_status("filesystem", "connected", tool_count=11, last=datetime.now(timezone.utc)),
            _make_status("github", "pending"),
        ]
        ctx = _make_ctx(mgr)
        await cmd_mcp("list", ctx)
        msg = ctx.reply.call_args[0][0]
        assert "filesystem" in msg
        assert "connected" in msg
        assert "github" in msg
        assert "pending" in msg

    @pytest.mark.asyncio
    async def test_list_pending_shows_dash_and_never(self):
        mgr = MagicMock()
        mgr.is_ready.return_value = False
        mgr.connection_summary.return_value = _make_summary(pending=1)
        mgr.list_servers.return_value = [_make_status("a", "pending")]
        ctx = _make_ctx(mgr)
        await cmd_mcp("list", ctx)
        msg = ctx.reply.call_args[0][0]
        assert "tools=-" in msg
        assert "never" in msg


class TestMcpRestart:
    @pytest.mark.asyncio
    async def test_restart_missing_name(self):
        mgr = MagicMock()
        ctx = _make_ctx(mgr)
        await cmd_mcp("restart", ctx)
        msg = ctx.reply.call_args[0][0]
        assert "用法" in msg

    @pytest.mark.asyncio
    async def test_restart_success(self):
        mgr = MagicMock()
        result = MagicMock()
        result.ok = True
        result.tool_count = 11
        mgr.restart_server = AsyncMock(return_value=result)
        ctx = _make_ctx(mgr)
        await cmd_mcp("restart filesystem", ctx)
        mgr.restart_server.assert_awaited_once_with("filesystem")
        msg = ctx.reply.call_args[0][0]
        assert "Restarted" in msg
        assert "11 tools" in msg

    @pytest.mark.asyncio
    async def test_restart_failure(self):
        mgr = MagicMock()
        result = MagicMock()
        result.ok = False
        result.tool_count = 0
        result.reason = "connect timeout"
        mgr.restart_server = AsyncMock(return_value=result)
        ctx = _make_ctx(mgr)
        await cmd_mcp("restart filesystem", ctx)
        msg = ctx.reply.call_args[0][0]
        assert "Failed" in msg
        assert "connect timeout" in msg


class TestMcpLogs:
    @pytest.mark.asyncio
    async def test_logs_missing_name(self):
        mgr = MagicMock()
        ctx = _make_ctx(mgr)
        await cmd_mcp("logs", ctx)
        msg = ctx.reply.call_args[0][0]
        assert "用法" in msg

    @pytest.mark.asyncio
    async def test_logs_unknown_server(self):
        mgr = MagicMock()
        mgr.list_servers.return_value = [_make_status("a", "connected")]
        ctx = _make_ctx(mgr)
        await cmd_mcp("logs zoo", ctx)
        msg = ctx.reply.call_args[0][0]
        assert "not configured" in msg

    @pytest.mark.asyncio
    async def test_logs_empty(self):
        mgr = MagicMock()
        mgr.list_servers.return_value = [_make_status("a", "connected")]
        mgr.get_logs.return_value = ""
        ctx = _make_ctx(mgr)
        await cmd_mcp("logs a", ctx)
        msg = ctx.reply.call_args[0][0]
        assert "no stderr" in msg

    @pytest.mark.asyncio
    async def test_logs_present(self):
        mgr = MagicMock()
        mgr.list_servers.return_value = [_make_status("a", "connected")]
        mgr.get_logs.return_value = "line1\nline2"
        ctx = _make_ctx(mgr)
        await cmd_mcp("logs a", ctx)
        msg = ctx.reply.call_args[0][0]
        assert "line1" in msg
        assert "line2" in msg
        assert "redacted" in msg.lower()
