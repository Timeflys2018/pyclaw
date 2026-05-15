from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pyclaw.core.agent.tools.registry import (
    ToolContext,
    ToolRegistry,
    execute_tool_calls,
)
from pyclaw.models import TextBlock, ToolResult


class _SlowTool:
    name = "slow"
    description = "a slow tool"
    parameters: dict = {"type": "object", "properties": {}}
    side_effect = False
    tool_class = "read"

    async def execute(self, args: dict, context: ToolContext) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult(tool_call_id="unused", content=[TextBlock(text="late")])


class _FastTool:
    name = "fast"
    description = "a fast tool"
    parameters: dict = {"type": "object", "properties": {}}
    side_effect = False
    tool_class = "read"

    async def execute(self, args: dict, context: ToolContext) -> ToolResult:
        return ToolResult(tool_call_id="unused", content=[TextBlock(text="ok")])


class _PerToolOverride:
    name = "override"
    description = "declares its own timeout"
    parameters: dict = {"type": "object", "properties": {}}
    side_effect = False
    tool_class = "read"
    timeout_seconds = 0.05

    async def execute(self, args: dict, context: ToolContext) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult(tool_call_id="unused", content=[TextBlock(text="unreachable")])


def _ctx(abort: asyncio.Event | None = None) -> ToolContext:
    return ToolContext(
        workspace_id="default",
        workspace_path=Path("."),
        session_id="test",
        abort=abort or asyncio.Event(),
    )


def _call(name: str, cid: str = "c1") -> dict:
    return {"id": cid, "function": {"name": name, "arguments": {}}}


@pytest.mark.asyncio
async def test_tool_default_timeout_enforced() -> None:
    reg = ToolRegistry()
    reg.register(_SlowTool())
    results = await execute_tool_calls(reg, [_call("slow")], _ctx(), default_tool_timeout_s=0.05)
    assert len(results) == 1
    assert results[0].is_error
    assert "timed out" in results[0].content[0].text.lower()


@pytest.mark.asyncio
async def test_tool_per_tool_timeout_override() -> None:
    reg = ToolRegistry()
    reg.register(_PerToolOverride())
    results = await execute_tool_calls(
        reg, [_call("override")], _ctx(), default_tool_timeout_s=100.0
    )
    assert len(results) == 1
    assert results[0].is_error
    assert "timed out" in results[0].content[0].text.lower()


@pytest.mark.asyncio
async def test_tool_finishes_within_timeout() -> None:
    reg = ToolRegistry()
    reg.register(_FastTool())
    results = await execute_tool_calls(reg, [_call("fast")], _ctx(), default_tool_timeout_s=1.0)
    assert len(results) == 1
    assert not results[0].is_error
    assert results[0].content[0].text == "ok"


@pytest.mark.asyncio
async def test_tool_zero_timeout_disables() -> None:
    reg = ToolRegistry()
    reg.register(_FastTool())
    results = await execute_tool_calls(reg, [_call("fast")], _ctx(), default_tool_timeout_s=0.0)
    assert len(results) == 1
    assert not results[0].is_error
