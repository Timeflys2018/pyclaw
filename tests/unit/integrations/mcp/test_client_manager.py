from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import mcp.types as mcp_types
import pytest

from pyclaw.core.agent.tools.registry import ToolRegistry
from pyclaw.infra.task_manager import TaskManager
from pyclaw.integrations.mcp.client_manager import (
    ConnectionSummary,
    MCPClientManager,
    RestartResult,
    ServerStatus,
)
from pyclaw.integrations.mcp.errors import MCPServerDeadError
from pyclaw.integrations.mcp.settings import McpServerConfig, McpSettings


def _make_remote_tool(name: str, read_only: bool = False) -> mcp_types.Tool:
    return mcp_types.Tool(
        name=name,
        description=f"Tool {name}",
        inputSchema={"type": "object", "properties": {}},
        annotations=mcp_types.ToolAnnotations(readOnlyHint=read_only),
    )


def _make_settings(servers: dict[str, McpServerConfig], enabled: bool = True) -> McpSettings:
    return McpSettings(enabled=enabled, servers=servers)


@pytest.fixture
def task_manager():
    tm = TaskManager()
    yield tm


@pytest.fixture
def basic_config():
    return McpServerConfig(
        command="echo",
        args=["test"],
        connect_timeout_seconds=2.0,
        call_timeout_seconds=2.0,
    )


class TestServerStatuses:
    def test_pending_for_enabled_servers(self, task_manager, basic_config):
        settings = _make_settings({"a": basic_config, "b": basic_config})
        mgr = MCPClientManager(settings, task_manager)
        statuses = mgr.list_servers()
        assert len(statuses) == 2
        assert all(s.status == "pending" for s in statuses)

    def test_disabled_for_enabled_false(self, task_manager, basic_config):
        disabled = McpServerConfig(command="x", enabled=False)
        settings = _make_settings({"a": basic_config, "b": disabled})
        mgr = MCPClientManager(settings, task_manager)
        statuses = {s.name: s.status for s in mgr.list_servers()}
        assert statuses == {"a": "pending", "b": "disabled"}

    def test_summary_empty(self, task_manager):
        mgr = MCPClientManager(McpSettings(enabled=False), task_manager)
        assert mgr.connection_summary() == ConnectionSummary()

    def test_summary_initial(self, task_manager, basic_config):
        settings = _make_settings(
            {"a": basic_config, "b": basic_config, "c": McpServerConfig(command="x", enabled=False)}
        )
        mgr = MCPClientManager(settings, task_manager)
        summary = mgr.connection_summary()
        assert summary.n_pending == 2
        assert summary.n_disabled == 1
        assert summary.n_connected == 0
        assert summary.total_tools == 0


class TestStartBackgroundDisabled:
    @pytest.mark.asyncio
    async def test_disabled_settings_immediate_ready(self, task_manager):
        mgr = MCPClientManager(McpSettings(enabled=False), task_manager)
        result = mgr.start_background()
        assert result is None
        assert mgr.is_ready()


class TestSupervisorReadinessGuarantee:
    @pytest.mark.asyncio
    async def test_ready_set_even_when_all_servers_fail(self, task_manager, basic_config):
        bad = McpServerConfig(
            command="this-binary-does-not-exist-anywhere",
            args=["--nope"],
            connect_timeout_seconds=0.5,
        )
        settings = _make_settings({"a": bad})
        mgr = MCPClientManager(settings, task_manager)
        mgr.start_background()
        try:
            await asyncio.wait_for(mgr.ready.wait(), timeout=5.0)
        finally:
            await task_manager.shutdown(grace_s=2.0)
        assert mgr.is_ready()
        assert mgr._servers["a"].status == "failed"

    @pytest.mark.asyncio
    async def test_supervisor_crash_still_sets_ready(self, task_manager, basic_config):
        settings = _make_settings({"a": basic_config})
        mgr = MCPClientManager(settings, task_manager)
        async def boom(*args, **kwargs):
            raise RuntimeError("synthetic supervisor failure")
        mgr._connect_one = boom
        mgr.start_background()
        await asyncio.wait_for(mgr.ready.wait(), timeout=2.0)
        await task_manager.shutdown(grace_s=2.0)
        assert mgr.is_ready()


class TestAttachAndRegisterIdempotent:
    @pytest.mark.asyncio
    async def test_idempotent_same_registry(self, task_manager):
        mgr = MCPClientManager(McpSettings(enabled=False), task_manager)
        registry = ToolRegistry()
        mgr.attach_and_register(registry)
        mgr.attach_and_register(registry)

    @pytest.mark.asyncio
    async def test_bulk_registers_pre_connected_adapters(self, task_manager, basic_config):
        from pyclaw.integrations.mcp.adapter import MCPToolAdapter

        mgr = MCPClientManager(_make_settings({"a": basic_config}), task_manager)
        adapter = MCPToolAdapter(
            server_name="a",
            remote_tool=_make_remote_tool("read_file", read_only=True),
            server_config=basic_config,
            group=MagicMock(),
            sdk_key="read_file",
        )
        mgr._adapters["a"] = [adapter]

        registry = ToolRegistry()
        mgr.attach_and_register(registry)
        assert registry.get("a:read_file") is adapter


class TestEnvSubstitutionInConnect:
    @pytest.mark.asyncio
    async def test_missing_env_var_marks_server_failed(self, task_manager, monkeypatch):
        monkeypatch.delenv("DEFINITELY_NOT_SET_TEST_VAR", raising=False)
        config = McpServerConfig(
            command="echo",
            env={"TOKEN": "{env:DEFINITELY_NOT_SET_TEST_VAR}"},
            connect_timeout_seconds=0.5,
        )
        mgr = MCPClientManager(_make_settings({"a": config}), task_manager)
        mgr.start_background()
        await asyncio.wait_for(mgr.ready.wait(), timeout=3.0)
        await task_manager.shutdown(grace_s=2.0)
        assert mgr._servers["a"].status == "failed"
        assert "TOKEN" in (mgr._servers["a"].reason or "")


class TestHandleServerDeath:
    @pytest.mark.asyncio
    async def test_idempotent(self, task_manager, basic_config):
        mgr = MCPClientManager(_make_settings({"a": basic_config}), task_manager)
        mgr._servers["a"] = ServerStatus(name="a", status="connected", tool_count=3)

        await mgr._handle_server_death("a")
        first_failed_at = mgr._servers["a"]
        assert first_failed_at.status == "failed"

        await mgr._handle_server_death("a")
        assert mgr._servers["a"] is first_failed_at

    @pytest.mark.asyncio
    async def test_unregisters_adapters_from_registry(self, task_manager, basic_config):
        from pyclaw.integrations.mcp.adapter import MCPToolAdapter

        mgr = MCPClientManager(_make_settings({"a": basic_config}), task_manager)
        registry = ToolRegistry()
        mgr.attach_and_register(registry)
        adapter = MCPToolAdapter(
            server_name="a",
            remote_tool=_make_remote_tool("read_file", read_only=True),
            server_config=basic_config,
            group=MagicMock(),
            sdk_key="read_file",
        )
        registry.register(adapter)
        mgr._adapters["a"] = [adapter]
        mgr._servers["a"] = ServerStatus(name="a", status="connected", tool_count=1)

        await mgr._handle_server_death("a")
        assert registry.get("a:read_file") is None
        assert mgr._adapters["a"] == []
        assert mgr._servers["a"].status == "failed"


class TestRestartServerUnknownOrDisabled:
    @pytest.mark.asyncio
    async def test_unknown_server(self, task_manager):
        mgr = MCPClientManager(McpSettings(enabled=False), task_manager)
        result = await mgr.restart_server("nope")
        assert result.ok is False
        assert "not configured" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_disabled_server(self, task_manager):
        disabled = McpServerConfig(command="x", enabled=False)
        mgr = MCPClientManager(_make_settings({"a": disabled}), task_manager)
        result = await mgr.restart_server("a")
        assert result.ok is False
        assert "disabled" in (result.reason or "")


class TestGetLogsRedaction:
    def test_resolved_env_value_redacted(self, task_manager, monkeypatch):
        monkeypatch.setenv("MY_SECRET_TOKEN", "ghp_super_secret")
        config = McpServerConfig(
            command="x",
            env={"TOKEN": "{env:MY_SECRET_TOKEN}"},
        )
        mgr = MCPClientManager(_make_settings({"a": config}), task_manager)
        from collections import deque
        mgr._stderr_buffers["a"] = deque([
            "Authenticated with token ghp_super_secret",
            "ready",
        ])
        logs = mgr.get_logs("a")
        assert "ghp_super_secret" not in logs
        assert "<REDACTED>" in logs
        assert "ready" in logs

    def test_literal_env_value_also_redacted(self, task_manager):
        config = McpServerConfig(
            command="x",
            env={"INLINE_TOKEN": "literal_secret_xyz"},
        )
        mgr = MCPClientManager(_make_settings({"a": config}), task_manager)
        from collections import deque
        mgr._stderr_buffers["a"] = deque(["leaked literal_secret_xyz here"])
        assert "literal_secret_xyz" not in mgr.get_logs("a")

    def test_unknown_server_returns_empty(self, task_manager):
        mgr = MCPClientManager(McpSettings(enabled=False), task_manager)
        assert mgr.get_logs("nonexistent") == ""


class TestShutdownGracefulWithoutConnections:
    @pytest.mark.asyncio
    async def test_shutdown_no_servers(self, task_manager):
        mgr = MCPClientManager(McpSettings(enabled=False), task_manager)
        await mgr.shutdown()
