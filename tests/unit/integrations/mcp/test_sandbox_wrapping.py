"""Sprint 3 Phase 4 T4.2-T4.6 — MCPClientManager sandbox wrapping.

Spec anchors:
- spec.md "MCP per-server sandbox default ON" + scenarios
- 4-slot review F1: npx/uvx auto-exempt
- 4-slot review F5: srt hang timeout cleanup
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.infra.task_manager import TaskManager
from pyclaw.integrations.mcp.client_manager import MCPClientManager
from pyclaw.integrations.mcp.settings import (
    McpSandboxConfig,
    McpServerConfig,
    McpSettings,
)


@pytest.fixture
def task_manager() -> TaskManager:
    return TaskManager(default_shutdown_grace_s=2.0)


def _captured_params_from(mgr: MCPClientManager) -> list[Any]:
    return mgr._test_captured_params  # type: ignore[attr-defined]


async def _run_connect(mgr: MCPClientManager, name: str, config: McpServerConfig) -> None:
    captured: list[Any] = []
    mgr._test_captured_params = captured  # type: ignore[attr-defined]

    mock_session = MagicMock()
    mock_session.tools = []

    async def _fake_connect_to_server(params: Any) -> Any:
        captured.append(params)
        return mock_session

    mgr._group.connect_to_server = AsyncMock(side_effect=_fake_connect_to_server)
    mgr._build_adapters_for_session = MagicMock(return_value=[])
    await mgr._connect_one(name, config)


class TestSandboxDisabledByteIdentical:
    @pytest.mark.asyncio
    async def test_explicit_disabled_passes_original_command_unchanged(
        self, task_manager: TaskManager
    ) -> None:
        cfg = McpServerConfig(
            command="/usr/local/bin/mcp-server-fs",
            args=["/tmp"],
            sandbox=McpSandboxConfig(enabled=False),
        )
        settings = McpSettings(enabled=True, servers={"fs": cfg})
        mgr = MCPClientManager(settings, task_manager)
        sandbox_policy = MagicMock()
        sandbox_policy.backend = "none"
        mgr.set_sandbox_policy(sandbox_policy)

        await _run_connect(mgr, "fs", cfg)

        params = _captured_params_from(mgr)[0]
        assert params.command == "/usr/local/bin/mcp-server-fs"
        assert params.args == ["/tmp"]


class TestNpxAutoExemptByteIdentical:
    @pytest.mark.asyncio
    async def test_npx_default_passes_through_unchanged(
        self, task_manager: TaskManager
    ) -> None:
        cfg = McpServerConfig(command="npx", args=["-y", "@mcp/server-fs"])
        assert cfg.sandbox.enabled is False
        settings = McpSettings(enabled=True, servers={"fs": cfg})
        mgr = MCPClientManager(settings, task_manager)
        sandbox_policy = MagicMock()
        sandbox_policy.backend = "srt"
        mgr.set_sandbox_policy(sandbox_policy)

        await _run_connect(mgr, "fs", cfg)

        params = _captured_params_from(mgr)[0]
        assert params.command == "npx"
        assert params.args == ["-y", "@mcp/server-fs"]


class TestSandboxEnabledWithSrt:
    @pytest.mark.asyncio
    async def test_local_binary_default_wraps_via_srt_policy(
        self, task_manager: TaskManager
    ) -> None:
        cfg = McpServerConfig(
            command="/usr/local/bin/mcp-server-fs",
            args=["/tmp"],
        )
        assert cfg.sandbox.enabled is True
        settings = McpSettings(enabled=True, servers={"fs": cfg})
        mgr = MCPClientManager(settings, task_manager)

        wrapped_params = MagicMock()
        wrapped_params.command = "/opt/homebrew/bin/srt"
        wrapped_params.args = [
            "--settings",
            "/tmp/cfg.json",
            "/usr/local/bin/mcp-server-fs",
            "/tmp",
        ]

        sandbox_policy = MagicMock()
        sandbox_policy.backend = "srt"
        sandbox_policy.wrap_mcp_stdio = MagicMock(return_value=wrapped_params)
        mgr.set_sandbox_policy(sandbox_policy)

        await _run_connect(mgr, "fs", cfg)

        sandbox_policy.wrap_mcp_stdio.assert_called_once()
        params = _captured_params_from(mgr)[0]
        assert params.command == "/opt/homebrew/bin/srt"
        assert "/usr/local/bin/mcp-server-fs" in params.args


class TestSrtUnavailableDegradation:
    @pytest.mark.asyncio
    async def test_sandbox_enabled_srt_missing_marks_failed(
        self, task_manager: TaskManager
    ) -> None:
        cfg = McpServerConfig(
            command="/usr/local/bin/mcp-server-fs", args=["/tmp"]
        )
        settings = McpSettings(enabled=True, servers={"fs": cfg})
        mgr = MCPClientManager(settings, task_manager)

        sandbox_policy = MagicMock()
        sandbox_policy.backend = "none"
        mgr.set_sandbox_policy(sandbox_policy)

        await mgr._connect_one("fs", cfg)

        status = mgr._servers["fs"]
        assert status.status == "failed"
        assert "srt" in (status.reason or "").lower()


class TestMigrationWarning:
    def test_one_time_warning_per_server_at_startup(
        self,
        task_manager: TaskManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        cfg = McpServerConfig(command="/usr/local/bin/mcp-server-fs")
        assert cfg.sandbox.enabled is True
        settings = McpSettings(enabled=True, servers={"fs": cfg})
        mgr = MCPClientManager(settings, task_manager)

        sandbox_policy = MagicMock()
        sandbox_policy.backend = "srt"
        mgr.set_sandbox_policy(sandbox_policy)

        with caplog.at_level(logging.WARNING):
            mgr._emit_sandbox_startup_advisories()

        warnings = [
            r for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert any("sandbox" in r.message.lower() for r in warnings)

    def test_npx_emits_info_not_warning(
        self,
        task_manager: TaskManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        cfg = McpServerConfig(command="npx", args=["-y", "@mcp/server-fs"])
        settings = McpSettings(enabled=True, servers={"fs": cfg})
        mgr = MCPClientManager(settings, task_manager)

        sandbox_policy = MagicMock()
        sandbox_policy.backend = "srt"
        mgr.set_sandbox_policy(sandbox_policy)

        with caplog.at_level(logging.INFO):
            mgr._emit_sandbox_startup_advisories()

        infos = [r for r in caplog.records if "auto-exempt" in r.message.lower()]
        assert len(infos) >= 1


class TestSrtHangTimeoutCleanup:
    """4-slot review F5 — srt subprocess hang must trigger timeout + cleanup."""

    @pytest.mark.asyncio
    async def test_connect_timeout_marks_server_failed(
        self, task_manager: TaskManager
    ) -> None:
        import asyncio

        cfg = McpServerConfig(
            command="/usr/local/bin/mcp-server-fs",
            connect_timeout_seconds=0.1,
        )
        settings = McpSettings(enabled=True, servers={"fs": cfg})
        mgr = MCPClientManager(settings, task_manager)

        sandbox_policy = MagicMock()
        sandbox_policy.backend = "srt"
        sandbox_policy.wrap_mcp_stdio = MagicMock(side_effect=lambda p, *a, **kw: p)
        mgr.set_sandbox_policy(sandbox_policy)

        async def _hang(params: Any) -> Any:
            await asyncio.sleep(10)
            return MagicMock()

        mgr._group.connect_to_server = AsyncMock(side_effect=_hang)
        mgr._build_adapters_for_session = MagicMock(return_value=[])

        await mgr._connect_one("fs", cfg)

        status = mgr._servers["fs"]
        assert status.status == "failed"
        assert "timeout" in (status.reason or "").lower()
