"""Sprint 3 Phase 2 T2.1/T2.2 — NoSandboxPolicy passthrough.

Spec anchor: spec.md "SandboxPolicy abstraction for BashTool" + scenario
"NoSandboxPolicy is the default when sandbox settings absent".
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pyclaw.sandbox.no_sandbox import NoSandboxPolicy


@pytest.fixture
def ctx() -> MagicMock:
    c = MagicMock()
    c.workspace_path = Path("/tmp/ws")
    c.user_id = "alice"
    c.role = "member"
    c.user_profile = None
    return c


class TestWrapBashCommand:
    def test_returns_sh_dash_c_for_arbitrary_command(self, ctx: MagicMock) -> None:
        policy = NoSandboxPolicy()
        executable, args = policy.wrap_bash_command("echo hello", ctx)
        assert executable == "/bin/sh"
        assert args == ["-c", "echo hello"]

    def test_preserves_command_with_pipes_and_quotes(self, ctx: MagicMock) -> None:
        policy = NoSandboxPolicy()
        cmd = "ls -la | grep 'foo bar' && echo done"
        executable, args = policy.wrap_bash_command(cmd, ctx)
        assert executable == "/bin/sh"
        assert args == ["-c", cmd]

    def test_empty_command_passes_through_unchanged(self, ctx: MagicMock) -> None:
        policy = NoSandboxPolicy()
        executable, args = policy.wrap_bash_command("", ctx)
        assert executable == "/bin/sh"
        assert args == ["-c", ""]


class TestBackend:
    def test_backend_is_none(self) -> None:
        policy = NoSandboxPolicy()
        assert policy.backend == "none"


class TestWrapMcpStdio:
    def test_passthrough_returns_input_unchanged(self) -> None:
        policy = NoSandboxPolicy()

        params = MagicMock()
        params.command = "/usr/local/bin/mcp-server-fs"
        params.args = ["/tmp"]

        result = policy.wrap_mcp_stdio(params, server_name="fs", sandbox_config=None)

        assert result is params
