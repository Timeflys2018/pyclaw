from __future__ import annotations

import pytest
from pydantic import ValidationError

from pyclaw.integrations.mcp.errors import MCPServerDeadError
from pyclaw.integrations.mcp.settings import McpServerConfig, McpSettings


class TestMcpServerConfig:
    def test_minimal_stdio(self):
        cfg = McpServerConfig(command="npx", args=["-y", "@scope/server"])
        assert cfg.command == "npx"
        assert cfg.transport == "stdio"
        assert cfg.enabled is True
        assert cfg.trust_annotations is True
        assert cfg.connect_timeout_seconds == 30.0

    def test_sse_transport_rejected(self):
        with pytest.raises(ValidationError):
            McpServerConfig(command="x", transport="sse")

    def test_negative_timeout_rejected(self):
        with pytest.raises(ValidationError):
            McpServerConfig(command="x", connect_timeout_seconds=-1.0)

    def test_forced_tier_yolo_accepted(self):
        cfg = McpServerConfig(command="x", forced_tier="yolo")
        assert cfg.forced_tier == "yolo"

    def test_forced_tier_invalid_value(self):
        with pytest.raises(ValidationError):
            McpServerConfig(command="x", forced_tier="bogus")


class TestMcpSettings:
    def test_default_disabled(self):
        s = McpSettings()
        assert s.enabled is False
        assert s.servers == {}

    def test_enabled_with_servers(self):
        s = McpSettings(
            enabled=True,
            servers={
                "filesystem": McpServerConfig(command="npx", args=["-y", "@x/fs"]),
                "github": McpServerConfig(command="npx", args=["-y", "@x/gh"]),
            },
        )
        assert s.enabled is True
        assert "filesystem" in s.servers
        assert "github" in s.servers

    def test_server_key_with_colon_rejected(self):
        with pytest.raises(ValidationError) as exc:
            McpSettings(servers={"bad:name": McpServerConfig(command="x")})
        assert "':'" in str(exc.value)

    def test_server_key_with_double_underscore_rejected(self):
        with pytest.raises(ValidationError) as exc:
            McpSettings(servers={"bad__name": McpServerConfig(command="x")})
        assert "'__'" in str(exc.value)

    def test_server_key_simple_underscore_allowed(self):
        s = McpSettings(servers={"my_server": McpServerConfig(command="x")})
        assert "my_server" in s.servers


class TestMCPServerDeadError:
    def test_two_arg_constructor(self):
        e = MCPServerDeadError("github", "broken pipe")
        assert e.server_name == "github"
        assert e.reason == "broken pipe"
        assert "github" in str(e)
        assert "broken pipe" in str(e)

    def test_is_exception(self):
        with pytest.raises(MCPServerDeadError) as exc:
            raise MCPServerDeadError("filesystem", "EOF")
        assert exc.value.server_name == "filesystem"
