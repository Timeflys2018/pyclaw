from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pyclaw.core.agent.tools.registry import (
    ToolContext,
    ToolRegistry,
    _dispatch_single,
    execute_tool_calls,
)
from pyclaw.integrations.mcp.errors import MCPServerDeadError
from pyclaw.models import TextBlock, ToolResult


@dataclass
class _FakeBuiltin:
    name: str = "bash"
    description: str = "Run a shell command."
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    side_effect: bool = True
    tool_class: str = "write"

    async def execute(self, args, context):
        return ToolResult(
            tool_call_id="t1", content=[TextBlock(text="ok")], is_error=False
        )


@dataclass
class _FakeMCPAdapter:
    name: str = "filesystem:read_file"
    description: str = "Read a file via MCP."
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    side_effect: bool = False
    tool_class: str = "read"
    server_name: str = "filesystem"
    server_config: Any = None
    _sdk_key: str = "read_file"

    async def execute(self, args, context):
        return ToolResult(
            tool_call_id="t1", content=[TextBlock(text="contents")], is_error=False
        )


@dataclass
class _DyingMCPAdapter:
    name: str = "github:search_issues"
    description: str = "Dies on call."
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    side_effect: bool = False
    tool_class: str = "read"
    server_name: str = "github"
    server_config: Any = None
    _sdk_key: str = "search_issues"

    async def execute(self, args, context):
        raise MCPServerDeadError(self.server_name, "broken pipe")


class TestRegistryGetBidirectional:
    def test_literal_lookup(self):
        reg = ToolRegistry()
        reg.register(_FakeBuiltin())
        assert reg.get("bash") is not None

    def test_get_none(self):
        reg = ToolRegistry()
        assert reg.get(None) is None
        assert reg.get("") is None

    def test_get_unknown(self):
        reg = ToolRegistry()
        assert reg.get("nonexistent") is None

    def test_mcp_canonical_lookup(self):
        reg = ToolRegistry()
        reg.register(_FakeMCPAdapter())
        assert reg.get("filesystem:read_file") is not None

    def test_mcp_llm_form_lookup(self):
        reg = ToolRegistry()
        reg.register(_FakeMCPAdapter())
        assert reg.get("filesystem__read_file") is not None
        assert reg.get("filesystem__read_file").name == "filesystem:read_file"

    def test_first_underscore_split(self):
        reg = ToolRegistry()
        adapter = _FakeMCPAdapter(name="myserver:get__data", _sdk_key="get__data")
        reg.register(adapter)
        result = reg.get("myserver__get__data")
        assert result is not None
        assert result.name == "myserver:get__data"

    def test_builtin_with_underscore_unchanged(self):
        reg = ToolRegistry()

        @dataclass
        class _UpdateWM:
            name: str = "update_working_memory"
            description: str = ""
            parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
            side_effect: bool = False
            tool_class: str = "read"

            async def execute(self, args, context):
                return ToolResult(tool_call_id="x", content=[], is_error=False)

        reg.register(_UpdateWM())
        assert reg.get("update_working_memory") is not None


class TestRegistryNameValidation:
    def test_reject_double_underscore_in_builtin(self):
        reg = ToolRegistry()

        @dataclass
        class _Bad:
            name: str = "foo__bar"
            description: str = ""
            parameters: dict = field(default_factory=dict)
            side_effect: bool = True
            tool_class: str = "write"

            async def execute(self, args, context):
                return ToolResult(tool_call_id="x", content=[], is_error=False)

        with pytest.raises(ValueError, match="must not contain '__'"):
            reg.register(_Bad())

    def test_reject_colon_in_builtin(self):
        reg = ToolRegistry()

        @dataclass
        class _Bad:
            name: str = "foo:bar"
            description: str = ""
            parameters: dict = field(default_factory=dict)
            side_effect: bool = True
            tool_class: str = "write"

            async def execute(self, args, context):
                return ToolResult(tool_call_id="x", content=[], is_error=False)

        with pytest.raises(ValueError, match="must not contain ':'"):
            reg.register(_Bad())

    def test_accept_single_colon_for_mcp(self):
        reg = ToolRegistry()
        reg.register(_FakeMCPAdapter())
        assert "filesystem:read_file" in reg.names()

    def test_reject_two_colons_for_mcp(self):
        reg = ToolRegistry()
        adapter = _FakeMCPAdapter(name="server:tool:extra", _sdk_key="tool:extra")
        with pytest.raises(ValueError, match="exactly one ':'"):
            reg.register(adapter)


class TestRegistryUnregister:
    def test_unregister_known(self):
        reg = ToolRegistry()
        reg.register(_FakeBuiltin())
        assert reg.unregister("bash") is True
        assert reg.get("bash") is None

    def test_unregister_unknown(self):
        reg = ToolRegistry()
        assert reg.unregister("nonexistent") is False

    def test_unregister_mcp(self):
        reg = ToolRegistry()
        reg.register(_FakeMCPAdapter())
        assert reg.unregister("filesystem:read_file") is True
        assert reg.get("filesystem:read_file") is None
        assert reg.get("filesystem__read_file") is None


class TestToOpenAIFunction:
    def test_builtin_passthrough(self):
        reg = ToolRegistry()
        reg.register(_FakeBuiltin())
        funcs = reg.list_for_llm()
        assert funcs[0]["function"]["name"] == "bash"

    def test_mcp_rewrite(self):
        reg = ToolRegistry()
        reg.register(_FakeMCPAdapter())
        funcs = reg.list_for_llm()
        assert funcs[0]["function"]["name"] == "filesystem__read_file"


class TestDispatchSingleMCPDeadError:
    @pytest.fixture
    def context(self):
        return ToolContext(
            workspace_id="w",
            workspace_path=Path("/tmp"),
            session_id="s",
        )

    def _make_call(self, name: str) -> dict:
        return {"id": "c1", "function": {"name": name, "arguments": "{}"}}

    @pytest.mark.asyncio
    async def test_dead_error_returns_tool_result_not_raise(self, context):
        reg = ToolRegistry()
        reg.register(_DyingMCPAdapter())
        result = await _dispatch_single(reg, self._make_call("github:search_issues"), context)
        assert result.is_error is True
        assert "github" in result.content[0].text
        assert "unavailable" in result.content[0].text

    @pytest.mark.asyncio
    async def test_dead_error_schedules_handler_when_extras_present(self, context):
        spawned = []

        class _FakeTM:
            def spawn(self, name, coro, category):
                spawned.append((name, category))
                coro.close()
                return "task_id_1"

        async def _handler(server_name):
            return None

        context.extras["task_manager"] = _FakeTM()
        context.extras["mcp_death_handler"] = _handler

        reg = ToolRegistry()
        reg.register(_DyingMCPAdapter())
        result = await _dispatch_single(reg, self._make_call("github:search_issues"), context)
        assert result.is_error
        assert spawned == [("mcp:death:github", "mcp")]

    @pytest.mark.asyncio
    async def test_dead_error_no_extras_returns_error_silently(self, context):
        reg = ToolRegistry()
        reg.register(_DyingMCPAdapter())
        result = await _dispatch_single(reg, self._make_call("github:search_issues"), context)
        assert result.is_error

    @pytest.mark.asyncio
    async def test_dead_error_spawn_failure_logged_not_raised(self, context):
        class _ClosedTM:
            def spawn(self, name, coro, category):
                coro.close()
                raise RuntimeError("TaskManager closed")

        async def _handler(server_name):
            return None

        context.extras["task_manager"] = _ClosedTM()
        context.extras["mcp_death_handler"] = _handler

        reg = ToolRegistry()
        reg.register(_DyingMCPAdapter())
        result = await _dispatch_single(reg, self._make_call("github:search_issues"), context)
        assert result.is_error

    @pytest.mark.asyncio
    async def test_dead_error_does_not_cancel_siblings_in_gather(self, context):
        reg = ToolRegistry()
        reg.register(_DyingMCPAdapter())
        reg.register(_FakeMCPAdapter())

        calls = [
            self._make_call("github:search_issues"),
            self._make_call("filesystem:read_file"),
        ]
        results = await execute_tool_calls(reg, calls, context)
        assert len(results) == 2
        assert results[0].is_error
        assert not results[1].is_error
