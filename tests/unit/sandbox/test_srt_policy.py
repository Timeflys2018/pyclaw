"""Sprint 3 Phase 3 T3.1-T3.4 — SrtPolicy implementation.

Spec anchor: spec.md "SandboxPolicy abstraction" + scenarios:
- "SrtPolicy wraps BashTool when configured"
- "srt 1.0.0 schema constraints enforced in SrtPolicy generated settings"
- spike S0.2 findings: filesystem.{allowWrite, denyWrite, denyRead} required;
  network.{allowedDomains (no '*'), deniedDomains} required.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyclaw.infra.settings import (
    SandboxFilesystemConfig,
    SandboxNetworkConfig,
    SandboxSettings,
)
from pyclaw.sandbox.srt import SrtBinaryNotFound, SrtPolicy


@pytest.fixture
def ctx(tmp_path: Path) -> MagicMock:
    c = MagicMock()
    c.workspace_path = tmp_path
    c.user_id = "alice"
    c.role = "member"
    c.user_profile = None
    return c


@pytest.fixture
def sandbox_settings() -> SandboxSettings:
    return SandboxSettings(
        policy="srt",
        defaultFilesystem=SandboxFilesystemConfig(
            allowWrite=["/tmp/workspace"],
            denyRead=["~/.ssh", "~/.aws"],
            denyWrite=[],
        ),
        defaultNetwork=SandboxNetworkConfig(
            allowedDomains=["api.anthropic.com"],
            deniedDomains=["169.254.169.254"],
        ),
    )


class TestBackend:
    def test_backend_is_srt(self, sandbox_settings: SandboxSettings) -> None:
        with patch("pyclaw.sandbox.srt.shutil.which", return_value="/opt/homebrew/bin/srt"):
            policy = SrtPolicy(settings=sandbox_settings)
            assert policy.backend == "srt"


class TestBinaryDetection:
    def test_missing_binary_raises_when_required(
        self, sandbox_settings: SandboxSettings
    ) -> None:
        with patch("pyclaw.sandbox.srt.shutil.which", return_value=None):
            with pytest.raises(SrtBinaryNotFound):
                SrtPolicy(settings=sandbox_settings, require_binary=True)

    def test_missing_binary_allowed_with_require_binary_false(
        self, sandbox_settings: SandboxSettings
    ) -> None:
        with patch("pyclaw.sandbox.srt.shutil.which", return_value=None):
            policy = SrtPolicy(settings=sandbox_settings, require_binary=False)
            assert policy.binary_path is None

    def test_binary_path_cached(self, sandbox_settings: SandboxSettings) -> None:
        with patch("pyclaw.sandbox.srt.shutil.which", return_value="/usr/local/bin/srt") as m:
            policy = SrtPolicy(settings=sandbox_settings)
            _ = policy.binary_path
            _ = policy.binary_path
            assert m.call_count == 1


class TestWrapBashCommand:
    def test_returns_srt_settings_then_command(
        self, sandbox_settings: SandboxSettings, ctx: MagicMock
    ) -> None:
        with patch("pyclaw.sandbox.srt.shutil.which", return_value="/opt/homebrew/bin/srt"):
            policy = SrtPolicy(settings=sandbox_settings)
            executable, args = policy.wrap_bash_command("echo hello", ctx)

        assert executable == "/opt/homebrew/bin/srt"
        assert args[0] == "--settings"
        assert args[1].endswith(".json")
        assert Path(args[1]).exists()
        assert args[2:] == ["/bin/sh", "-c", "echo hello"]


class TestSettingsJsonGeneration:
    """spike S0.2 findings: srt 1.0.0 schema enforces required fields."""

    def test_all_required_filesystem_fields_present(
        self, sandbox_settings: SandboxSettings, ctx: MagicMock
    ) -> None:
        with patch("pyclaw.sandbox.srt.shutil.which", return_value="/opt/homebrew/bin/srt"):
            policy = SrtPolicy(settings=sandbox_settings)
            _, args = policy.wrap_bash_command("echo x", ctx)

        with open(args[1]) as f:
            cfg = json.load(f)

        assert "filesystem" in cfg
        assert "allowWrite" in cfg["filesystem"]
        assert "denyWrite" in cfg["filesystem"]
        assert "denyRead" in cfg["filesystem"]

    def test_all_required_network_fields_present(
        self, sandbox_settings: SandboxSettings, ctx: MagicMock
    ) -> None:
        with patch("pyclaw.sandbox.srt.shutil.which", return_value="/opt/homebrew/bin/srt"):
            policy = SrtPolicy(settings=sandbox_settings)
            _, args = policy.wrap_bash_command("echo x", ctx)

        with open(args[1]) as f:
            cfg = json.load(f)

        assert "network" in cfg
        assert "allowedDomains" in cfg["network"]
        assert "deniedDomains" in cfg["network"]

    def test_network_allowed_domains_does_not_contain_wildcard(
        self, sandbox_settings: SandboxSettings, ctx: MagicMock
    ) -> None:
        """srt 1.0.0 rejects ['*']; spec scenario asserts this explicitly."""
        with patch("pyclaw.sandbox.srt.shutil.which", return_value="/opt/homebrew/bin/srt"):
            policy = SrtPolicy(settings=sandbox_settings)
            _, args = policy.wrap_bash_command("echo x", ctx)

        with open(args[1]) as f:
            cfg = json.load(f)

        assert "*" not in cfg["network"]["allowedDomains"]

    def test_imds_protection_in_denied_domains_default(
        self, ctx: MagicMock
    ) -> None:
        """169.254.169.254 (cloud metadata) MUST be in deniedDomains by default."""
        settings = SandboxSettings(policy="srt")
        with patch("pyclaw.sandbox.srt.shutil.which", return_value="/opt/homebrew/bin/srt"):
            policy = SrtPolicy(settings=settings)
            _, args = policy.wrap_bash_command("echo x", ctx)

        with open(args[1]) as f:
            cfg = json.load(f)

        assert "169.254.169.254" in cfg["network"]["deniedDomains"]

    def test_per_user_sandbox_overrides_merge(self, ctx: MagicMock) -> None:
        from pyclaw.auth.profile import UserProfile

        settings = SandboxSettings(
            policy="srt",
            defaultFilesystem=SandboxFilesystemConfig(allowWrite=["/tmp/base"]),
        )
        ctx.user_profile = UserProfile(
            channel="web",
            user_id="alice",
            sandbox_overrides={"filesystem": {"allowWrite": ["/tmp/alice"]}},
        )

        with patch("pyclaw.sandbox.srt.shutil.which", return_value="/opt/homebrew/bin/srt"):
            policy = SrtPolicy(settings=settings)
            _, args = policy.wrap_bash_command("echo x", ctx)

        with open(args[1]) as f:
            cfg = json.load(f)

        assert "/tmp/alice" in cfg["filesystem"]["allowWrite"]


class TestArgTooLongFallback:
    """Linux ARG_MAX exceeded → fall back to NoSandboxPolicy semantics + audit."""

    def test_oversized_command_falls_back_to_passthrough(
        self, sandbox_settings: SandboxSettings, ctx: MagicMock
    ) -> None:
        from pyclaw.sandbox.srt import ARG_MAX_FALLBACK_THRESHOLD

        long_cmd = "echo " + "a" * (ARG_MAX_FALLBACK_THRESHOLD + 1)
        with patch("pyclaw.sandbox.srt.shutil.which", return_value="/opt/homebrew/bin/srt"):
            policy = SrtPolicy(settings=sandbox_settings)
            executable, args = policy.wrap_bash_command(long_cmd, ctx)

        assert executable == "/bin/sh"
        assert args == ["-c", long_cmd]


class TestWrapMcpStdio:
    def test_wraps_with_srt_settings_prefix(
        self, sandbox_settings: SandboxSettings
    ) -> None:
        from mcp import StdioServerParameters

        params = StdioServerParameters(
            command="/usr/local/bin/mcp-server-fs",
            args=["/tmp"],
        )

        with patch("pyclaw.sandbox.srt.shutil.which", return_value="/opt/homebrew/bin/srt"):
            policy = SrtPolicy(settings=sandbox_settings)
            wrapped = policy.wrap_mcp_stdio(
                params, server_name="fs", sandbox_config=None
            )

        assert wrapped.command == "/opt/homebrew/bin/srt"
        assert wrapped.args[0] == "--settings"
        assert wrapped.args[1].endswith(".json")
        assert wrapped.args[2:] == ["/usr/local/bin/mcp-server-fs", "/tmp"]
