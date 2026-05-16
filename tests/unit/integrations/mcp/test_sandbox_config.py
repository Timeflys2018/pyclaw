"""Sprint 3 Phase 4 T4.1 — McpSandboxConfig + conditional default per command.

Spec anchor: spec.md "MCP per-server sandbox default ON ... npx/uvx auto-exempt"
4-slot review F1 fix.
"""
from __future__ import annotations

from pyclaw.integrations.mcp.settings import McpServerConfig, McpSandboxConfig


class TestNpxUvxAutoExempt:
    def test_npx_command_default_enabled_false(self) -> None:
        cfg = McpServerConfig(command="npx")
        assert cfg.sandbox.enabled is False

    def test_uvx_command_default_enabled_false(self) -> None:
        cfg = McpServerConfig(command="uvx")
        assert cfg.sandbox.enabled is False

    def test_npx_with_explicit_enabled_true_overrides_auto_exempt(self) -> None:
        cfg = McpServerConfig(
            command="npx",
            sandbox=McpSandboxConfig(enabled=True),
        )
        assert cfg.sandbox.enabled is True


class TestLocalBinaryDefault:
    def test_local_binary_path_default_enabled_true(self) -> None:
        cfg = McpServerConfig(command="/usr/local/bin/mcp-server-fs")
        assert cfg.sandbox.enabled is True

    def test_relative_path_default_enabled_true(self) -> None:
        cfg = McpServerConfig(command="./bin/mcp-server-fs")
        assert cfg.sandbox.enabled is True


class TestExplicitDisable:
    def test_local_binary_explicit_disabled(self) -> None:
        cfg = McpServerConfig(
            command="/usr/local/bin/mcp-server-fs",
            sandbox=McpSandboxConfig(enabled=False),
        )
        assert cfg.sandbox.enabled is False


class TestSandboxConfigShape:
    def test_default_has_filesystem_network_env_fields(self) -> None:
        sb = McpSandboxConfig(enabled=True)
        assert sb.enabled is True
        assert sb.filesystem == {} or hasattr(sb, "filesystem")
        assert hasattr(sb, "network")
        assert hasattr(sb, "env_allowlist")

    def test_filesystem_overrides_accepted(self) -> None:
        sb = McpSandboxConfig(
            enabled=True,
            filesystem={"allowWrite": ["/tmp/workspace"]},
            network={"allowedDomains": ["registry.npmjs.org"]},
        )
        assert sb.filesystem["allowWrite"] == ["/tmp/workspace"]
        assert sb.network["allowedDomains"] == ["registry.npmjs.org"]


class TestNpxAutoExemptDetection:
    """The auto-exempt classifier must only match argv[0] basename, not paths
    containing 'npx' literally."""

    def test_path_containing_npx_in_dir_not_exempt(self) -> None:
        cfg = McpServerConfig(command="/Users/alice/npx-tools/bin/server")
        assert cfg.sandbox.enabled is True

    def test_npx_with_full_path_still_auto_exempt(self) -> None:
        cfg = McpServerConfig(command="/usr/local/bin/npx")
        assert cfg.sandbox.enabled is False
