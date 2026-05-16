"""Sprint 2 failure-mode integration tests.

Covers the spec's most important survivability guarantees without spinning
up real MCP servers:

* Server crashes mid-call → tools removed, agent loop continues.
* Server slow startup → other servers connect first; lifespan does NOT
  block on the slow one.
* `restart_server` atomicity — failed restart removes old adapters
  (the spec's safer-by-default semantic).
* MCPServerDeadError caught inside _dispatch_single does NOT cancel
  sibling parallel calls in the same iteration (closes review v2 C1+H).

Uses MagicMock + AsyncMock for the SDK boundary; no real subprocess.
"""

from __future__ import annotations

import asyncio
import errno
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anyio
import mcp.types as mcp_types
import pytest

from pyclaw.core.agent.tools.registry import (
    ToolContext,
    ToolRegistry,
    execute_tool_calls,
)
from pyclaw.infra.task_manager import TaskManager
from pyclaw.integrations.mcp.adapter import MCPToolAdapter
from pyclaw.integrations.mcp.client_manager import MCPClientManager, ServerStatus
from pyclaw.integrations.mcp.errors import MCPServerDeadError
from pyclaw.integrations.mcp.settings import McpServerConfig, McpSettings


def _make_remote_tool(name: str, read_only: bool = True) -> mcp_types.Tool:
    return mcp_types.Tool(
        name=name,
        description=f"Tool {name}",
        inputSchema={"type": "object", "properties": {}},
        annotations=mcp_types.ToolAnnotations(readOnlyHint=read_only),
    )


def _make_call_result(text: str = "ok") -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text)],
        isError=False,
    )


def _make_adapter(server_name: str, tool_name: str, group: Any) -> MCPToolAdapter:
    cfg = McpServerConfig(command="echo", call_timeout_seconds=2.0)
    return MCPToolAdapter(
        server_name=server_name,
        remote_tool=_make_remote_tool(tool_name, read_only=True),
        server_config=cfg,
        group=group,
        sdk_key=tool_name,
    )


@pytest.fixture
def task_manager():
    return TaskManager()


@pytest.fixture
def base_context():
    return ToolContext(
        workspace_id="w",
        workspace_path=Path("/tmp"),
        session_id="s",
    )


class TestParallelGatherSurvivesDeadServer:
    @pytest.mark.asyncio
    async def test_one_dead_server_does_not_cancel_siblings(self, base_context, task_manager):
        good_group = MagicMock()
        good_group.call_tool = AsyncMock(return_value=_make_call_result("filesystem-result"))
        good_adapter = _make_adapter("filesystem", "read_file", good_group)

        bad_group = MagicMock()
        bad_group.call_tool = AsyncMock(side_effect=anyio.ClosedResourceError())
        bad_adapter = _make_adapter("github", "search_issues", bad_group)

        registry = ToolRegistry()
        registry.register(good_adapter)
        registry.register(bad_adapter)

        spawned = []

        class _FakeTM:
            def spawn(self, name, coro, category):
                spawned.append((name, category))
                coro.close()
                return "tid"

        async def _handler(server_name):
            return None

        base_context.extras["task_manager"] = _FakeTM()
        base_context.extras["mcp_death_handler"] = _handler

        calls = [
            {"id": "a", "function": {"name": "filesystem:read_file", "arguments": "{}"}},
            {"id": "b", "function": {"name": "github:search_issues", "arguments": "{}"}},
        ]
        results = await execute_tool_calls(registry, calls, base_context)

        assert len(results) == 2
        assert not results[0].is_error
        assert "filesystem-result" in results[0].content[0].text
        assert results[1].is_error
        assert "github" in results[1].content[0].text
        assert "unavailable" in results[1].content[0].text

        assert ("mcp:death:github", "mcp") in spawned

    @pytest.mark.asyncio
    async def test_multiple_dead_calls_same_server_idempotent(
        self, base_context, task_manager
    ):
        bad_group = MagicMock()
        bad_group.call_tool = AsyncMock(side_effect=anyio.ClosedResourceError())
        adapter1 = _make_adapter("github", "search_issues", bad_group)
        adapter2 = _make_adapter("github", "list_repos", bad_group)

        registry = ToolRegistry()
        registry.register(adapter1)
        registry.register(adapter2)

        spawn_calls = []

        class _FakeTM:
            def spawn(self, name, coro, category):
                spawn_calls.append(name)
                coro.close()
                return "tid"

        async def _handler(server_name):
            return None

        base_context.extras["task_manager"] = _FakeTM()
        base_context.extras["mcp_death_handler"] = _handler

        calls = [
            {"id": "a", "function": {"name": "github:search_issues", "arguments": "{}"}},
            {"id": "b", "function": {"name": "github:list_repos", "arguments": "{}"}},
        ]
        results = await execute_tool_calls(registry, calls, base_context)

        assert len(results) == 2
        assert all(r.is_error for r in results)
        assert all("github" in r.content[0].text for r in results)
        assert spawn_calls.count("mcp:death:github") == 2


class TestSlowStartupNonBlocking:
    @pytest.mark.asyncio
    async def test_lifespan_does_not_block_on_slow_server(self, task_manager):
        slow = McpServerConfig(
            command="this-binary-does-not-exist-anywhere",
            connect_timeout_seconds=0.5,
        )
        settings = McpSettings(enabled=True, servers={"slow": slow})
        manager = MCPClientManager(settings, task_manager=task_manager)

        start = asyncio.get_event_loop().time()
        task_id = manager.start_background()
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 0.1
        assert task_id is not None

        await asyncio.wait_for(manager.ready.wait(), timeout=3.0)
        await task_manager.shutdown(grace_s=2.0)

        statuses = {s.name: s.status for s in manager.list_servers()}
        assert statuses["slow"] == "failed"


class TestRestartFailureRemovesOldAdapters:
    @pytest.mark.asyncio
    async def test_failed_restart_marks_failed_and_clears_adapters(self, task_manager):
        cfg = McpServerConfig(command="echo", connect_timeout_seconds=0.5)
        settings = McpSettings(enabled=True, servers={"flaky": cfg})
        manager = MCPClientManager(settings, task_manager=task_manager)

        registry = ToolRegistry()
        manager.attach_and_register(registry)

        good_group = MagicMock()
        existing = _make_adapter("flaky", "old_tool", good_group)
        manager._adapters["flaky"] = [existing]
        registry.register(existing)
        manager._servers["flaky"] = ServerStatus(
            name="flaky",
            status="connected",
            tool_count=1,
            last_connect_at=datetime.now(timezone.utc),
        )

        async def fail_connect(name, config):
            manager._servers[name] = ServerStatus(
                name=name, status="failed", reason="simulated reconnect failure"
            )

        manager._connect_one = fail_connect

        result = await manager.restart_server("flaky")

        assert result.ok is False
        assert "simulated" in (result.reason or "")
        assert registry.get("flaky:old_tool") is None
        assert manager._adapters.get("flaky") == []
        assert manager._servers["flaky"].status == "failed"


class TestHandleServerDeathIdempotent:
    @pytest.mark.asyncio
    async def test_concurrent_death_collapses_to_one_unregister(self, task_manager):
        cfg = McpServerConfig(command="echo")
        settings = McpSettings(enabled=True, servers={"github": cfg})
        manager = MCPClientManager(settings, task_manager=task_manager)
        registry = ToolRegistry()
        manager.attach_and_register(registry)

        good_group = MagicMock()
        for tool in ("a", "b", "c"):
            adapter = _make_adapter("github", tool, good_group)
            manager._adapters.setdefault("github", []).append(adapter)
            registry.register(adapter)
        manager._servers["github"] = ServerStatus(
            name="github",
            status="connected",
            tool_count=3,
            last_connect_at=datetime.now(timezone.utc),
        )

        await asyncio.gather(
            manager._handle_server_death("github"),
            manager._handle_server_death("github"),
            manager._handle_server_death("github"),
        )

        assert manager._servers["github"].status == "failed"
        assert manager._adapters["github"] == []
        for tool in ("a", "b", "c"):
            assert registry.get(f"github:{tool}") is None
