"""Per-call tier evaluation regression + de-escalation contract tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from pyclaw.integrations.mcp.settings import McpServerConfig


@dataclass
class _BuiltinTool:
    name: str = "bash"
    description: str = "Run shell commands."
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    side_effect: bool = True
    tool_class: str = "write"

    async def execute(self, args, context):
        from pyclaw.models import TextBlock, ToolResult
        return ToolResult(tool_call_id="t", content=[TextBlock(text="ok")], is_error=False)


@dataclass
class _MCPAdapter:
    """Duck-typed MCP adapter — has server_config + server_name (the per-call tier eval triggers)."""

    name: str = "github:search"
    description: str = "Search."
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    side_effect: bool = False
    tool_class: str = "read"
    server_name: str = "github"
    server_config: Any = None

    async def execute(self, args, context):
        from pyclaw.models import TextBlock, ToolResult
        return ToolResult(tool_call_id="t", content=[TextBlock(text="found")], is_error=False)


def _evaluate_per_call(
    tools_dict: dict,
    parsed_calls: list[tuple[dict, str, dict]],
    per_turn_tier: str,
    permission_tier_override: str | None,
):
    """Inline copy of the runner's per-call eval algorithm to test in isolation."""
    _RANK = {"read-only": 2, "approval": 1, "yolo": 0}
    per_turn_source = (
        "per-turn" if permission_tier_override is not None else "channel-default"
    )
    out = []
    for call, llm_tool_name, raw_args in parsed_calls:
        tool_obj = tools_dict.get(llm_tool_name)
        canonical_name = (
            getattr(tool_obj, "name", llm_tool_name) if tool_obj is not None else llm_tool_name
        )
        forced = None
        cfg = getattr(tool_obj, "server_config", None) if tool_obj is not None else None
        if cfg is not None:
            forced = getattr(cfg, "forced_tier", None)
        if forced is not None and _RANK[forced] > _RANK[per_turn_tier]:
            call_tier = forced
            tier_source = "forced-by-server-config"
            forced_server = getattr(tool_obj, "server_name", None)
        else:
            call_tier = per_turn_tier
            tier_source = per_turn_source
            forced_server = None
        out.append((call, llm_tool_name, raw_args, canonical_name, call_tier, tier_source, forced_server))
    return out


class TestForceTierDeEscalationOnly:
    def test_yolo_per_turn_with_forced_approval_gates(self):
        cfg = McpServerConfig(command="x", forced_tier="approval")
        adapter = _MCPAdapter(server_config=cfg)
        tools = {"github:search": adapter}
        result = _evaluate_per_call(
            tools, [({}, "github:search", {})], per_turn_tier="yolo",
            permission_tier_override="yolo",
        )
        _, _, _, canonical, call_tier, tier_source, forced_server = result[0]
        assert canonical == "github:search"
        assert call_tier == "approval"
        assert tier_source == "forced-by-server-config"
        assert forced_server == "github"

    def test_approval_per_turn_with_forced_yolo_does_not_escalate(self):
        cfg = McpServerConfig(command="x", forced_tier="yolo")
        adapter = _MCPAdapter(server_config=cfg)
        tools = {"github:search": adapter}
        result = _evaluate_per_call(
            tools, [({}, "github:search", {})], per_turn_tier="approval",
            permission_tier_override="approval",
        )
        _, _, _, _, call_tier, tier_source, _ = result[0]
        assert call_tier == "approval"
        assert tier_source == "per-turn"

    def test_read_only_per_turn_with_forced_approval_does_not_escalate(self):
        cfg = McpServerConfig(command="x", forced_tier="approval")
        adapter = _MCPAdapter(server_config=cfg)
        tools = {"github:search": adapter}
        result = _evaluate_per_call(
            tools, [({}, "github:search", {})], per_turn_tier="read-only",
            permission_tier_override="read-only",
        )
        _, _, _, _, call_tier, tier_source, _ = result[0]
        assert call_tier == "read-only"
        assert tier_source == "per-turn"

    def test_read_only_per_turn_with_forced_approval_for_lower_rank(self):
        cfg = McpServerConfig(command="x", forced_tier="yolo")
        adapter = _MCPAdapter(server_config=cfg)
        tools = {"github:search": adapter}
        result = _evaluate_per_call(
            tools, [({}, "github:search", {})], per_turn_tier="read-only",
            permission_tier_override="read-only",
        )
        _, _, _, _, call_tier, _, _ = result[0]
        assert call_tier == "read-only"

    def test_yolo_per_turn_with_forced_read_only_de_escalates(self):
        cfg = McpServerConfig(command="x", forced_tier="read-only")
        adapter = _MCPAdapter(server_config=cfg, tool_class="write", side_effect=True)
        tools = {"github:write_thing": adapter}
        result = _evaluate_per_call(
            tools, [({}, "github:write_thing", {})], per_turn_tier="yolo",
            permission_tier_override="yolo",
        )
        _, _, _, _, call_tier, tier_source, _ = result[0]
        assert call_tier == "read-only"
        assert tier_source == "forced-by-server-config"


class TestMixedBatch:
    def test_yolo_builtin_plus_forced_approval_mcp(self):
        cfg = McpServerConfig(command="x", forced_tier="approval")
        adapter = _MCPAdapter(server_config=cfg)
        tools = {"bash": _BuiltinTool(), "github:search": adapter}
        result = _evaluate_per_call(
            tools,
            [({}, "bash", {}), ({}, "github:search", {})],
            per_turn_tier="yolo",
            permission_tier_override="yolo",
        )
        bash_tier = result[0][4]
        mcp_tier = result[1][4]
        assert bash_tier == "yolo"
        assert mcp_tier == "approval"

    def test_no_forced_tier_falls_through_to_per_turn(self):
        adapter = _MCPAdapter(server_config=McpServerConfig(command="x"))
        tools = {"github:search": adapter}
        result = _evaluate_per_call(
            tools, [({}, "github:search", {})], per_turn_tier="yolo",
            permission_tier_override=None,
        )
        _, _, _, _, call_tier, tier_source, _ = result[0]
        assert call_tier == "yolo"
        assert tier_source == "channel-default"


class TestCanonicalNameResolution:
    def test_canonical_used_when_tool_found(self):
        adapter = _MCPAdapter()
        adapter.server_config = McpServerConfig(command="x")
        tools = {"github:search": adapter}
        result = _evaluate_per_call(
            tools, [({}, "github:search", {})], per_turn_tier="approval",
            permission_tier_override=None,
        )
        canonical = result[0][3]
        assert canonical == "github:search"

    def test_llm_form_tool_name_resolves_to_canonical_via_registry(self):
        from pyclaw.core.agent.tools.registry import ToolRegistry

        adapter = _MCPAdapter()
        adapter.server_config = McpServerConfig(command="x")
        reg = ToolRegistry()
        reg.register(adapter)

        found = reg.get("github__search")
        assert found is adapter
        assert found.name == "github:search"

    def test_unknown_tool_keeps_llm_form(self):
        tools = {}
        result = _evaluate_per_call(
            tools, [({}, "unknown__tool", {})], per_turn_tier="approval",
            permission_tier_override=None,
        )
        canonical = result[0][3]
        assert canonical == "unknown__tool"
