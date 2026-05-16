"""Real-MCP-server E2E tests, gated by env.

Requires: PYCLAW_TEST_MCP=1 + npx in PATH + network access for the
@modelcontextprotocol/server-everything package on first run.

Skipped by default in CI / normal regression runs to avoid network /
toolchain dependencies.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from pyclaw.core.agent.tools.registry import ToolContext, ToolRegistry
from pyclaw.infra.task_manager import TaskManager
from pyclaw.integrations.mcp.client_manager import MCPClientManager
from pyclaw.integrations.mcp.settings import McpServerConfig, McpSettings


pytestmark = pytest.mark.skipif(
    os.environ.get("PYCLAW_TEST_MCP") != "1" or shutil.which("npx") is None,
    reason="PYCLAW_TEST_MCP not set or npx not in PATH; skipping real-server E2E",
)


@pytest.mark.asyncio
async def test_filesystem_server_full_cycle(tmp_path):
    workspace = tmp_path / "mcp-fs-test"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello from MCP\n", encoding="utf-8")

    config = McpServerConfig(
        command="npx",
        args=[
            "-y",
            "@modelcontextprotocol/server-filesystem",
            str(workspace),
        ],
        connect_timeout_seconds=60.0,
    )
    settings = McpSettings(enabled=True, servers={"fs": config})

    task_manager = TaskManager()
    manager = MCPClientManager(settings, task_manager=task_manager)
    registry = ToolRegistry()

    try:
        manager.start_background()
        manager.attach_and_register(registry)
        await asyncio.wait_for(manager.ready.wait(), timeout=120.0)

        statuses = {s.name: s for s in manager.list_servers()}
        assert statuses["fs"].status == "connected", statuses["fs"].reason
        assert statuses["fs"].tool_count > 0

        names = registry.names()
        assert any(name.startswith("fs:") for name in names), names

        summary = manager.connection_summary()
        assert summary.n_connected == 1
        assert summary.total_tools == statuses["fs"].tool_count
        assert manager.is_ready() is True
    finally:
        await manager.shutdown()
        await task_manager.shutdown(grace_s=5.0)


@pytest.mark.asyncio
async def test_filesystem_server_dispatch_through_registry(tmp_path):
    from pyclaw.core.agent.tools.registry import ToolContext, execute_tool_calls

    workspace = tmp_path / "mcp-fs-dispatch"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello-content\n", encoding="utf-8")

    config = McpServerConfig(
        command="npx",
        args=[
            "-y",
            "@modelcontextprotocol/server-filesystem",
            str(workspace),
        ],
        connect_timeout_seconds=60.0,
        call_timeout_seconds=30.0,
    )
    settings = McpSettings(enabled=True, servers={"fs": config})

    task_manager = TaskManager()
    manager = MCPClientManager(settings, task_manager=task_manager)
    registry = ToolRegistry()

    try:
        manager.start_background()
        manager.attach_and_register(registry)
        await asyncio.wait_for(manager.ready.wait(), timeout=120.0)

        list_dir_name = next(
            (name for name in registry.names() if name == "fs:list_directory"),
            None,
        )
        assert list_dir_name is not None, registry.names()

        ctx = ToolContext(
            workspace_id="default",
            workspace_path=workspace,
            session_id="e2e-session",
        )
        ctx.extras["task_manager"] = task_manager
        ctx.extras["mcp_death_handler"] = manager._handle_server_death

        import json as _json
        results = await execute_tool_calls(
            registry,
            [
                {
                    "id": "c1",
                    "function": {
                        "name": "fs__list_directory",
                        "arguments": _json.dumps({"path": str(workspace)}),
                    },
                }
            ],
            ctx,
        )
        assert len(results) == 1
        assert not results[0].is_error, results[0].content[0].text
        text_blob = "".join(
            block.text for block in results[0].content if hasattr(block, "text")
        )
        assert "hello.txt" in text_blob
    finally:
        await manager.shutdown()
        await task_manager.shutdown(grace_s=5.0)
