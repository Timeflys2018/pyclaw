from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pyclaw.core.agent.tools.registry import (
    Tool,
    ToolContext,
    ToolRegistry,
    execute_tool_calls,
    text_result,
)
from pyclaw.models import ToolResult


class _StubTool:
    def __init__(
        self,
        name: str,
        side_effect: bool = False,
        delay: float = 0.0,
        raises: Exception | None = None,
    ) -> None:
        self.name = name
        self.description = f"stub tool {name}"
        self.parameters = {"type": "object", "properties": {}}
        self.side_effect = side_effect
        self.executed_at: float | None = None
        self._delay = delay
        self._raises = raises

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        if self._raises:
            raise self._raises
        if self._delay:
            self.executed_at = asyncio.get_event_loop().time()
            await asyncio.sleep(self._delay)
        else:
            self.executed_at = asyncio.get_event_loop().time()
        return text_result(args.get("_call_id", "x"), f"{self.name}_done")


def _ctx() -> ToolContext:
    return ToolContext(workspace_id="default", workspace_path=Path("/tmp"), session_id="s1")


def _call(name: str, call_id: str = "c1") -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": {"_call_id": call_id}}}


class TestToolRegistry:
    def test_register_and_get(self) -> None:
        registry = ToolRegistry()
        tool = _StubTool("echo")
        registry.register(tool)
        assert registry.get("echo") is tool
        assert "echo" in registry
        assert registry.get("missing") is None

    def test_duplicate_registration_raises(self) -> None:
        registry = ToolRegistry()
        registry.register(_StubTool("echo"))
        with pytest.raises(ValueError):
            registry.register(_StubTool("echo"))

    def test_list_for_llm_wraps_in_function_schema(self) -> None:
        registry = ToolRegistry()
        registry.register(_StubTool("read"))
        llm_list = registry.list_for_llm()
        assert llm_list[0]["type"] == "function"
        assert llm_list[0]["function"]["name"] == "read"
        assert llm_list[0]["function"]["parameters"]["type"] == "object"


class TestExecuteToolCalls:
    async def test_reads_run_in_parallel(self) -> None:
        registry = ToolRegistry()
        r1 = _StubTool("read1", side_effect=False, delay=0.05)
        r2 = _StubTool("read2", side_effect=False, delay=0.05)
        registry.register(r1)
        registry.register(r2)

        calls = [_call("read1", "c1"), _call("read2", "c2")]
        start = asyncio.get_event_loop().time()
        results = await execute_tool_calls(registry, calls, _ctx())
        elapsed = asyncio.get_event_loop().time() - start

        assert len(results) == 2
        assert all(not r.is_error for r in results)
        assert elapsed < 0.1  # parallel is roughly 50ms, sequential would be 100ms

    async def test_side_effect_tools_run_sequentially(self) -> None:
        registry = ToolRegistry()
        w1 = _StubTool("write1", side_effect=True, delay=0.05)
        w2 = _StubTool("write2", side_effect=True, delay=0.05)
        registry.register(w1)
        registry.register(w2)

        calls = [_call("write1", "c1"), _call("write2", "c2")]
        start = asyncio.get_event_loop().time()
        await execute_tool_calls(registry, calls, _ctx())
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed >= 0.09  # two 50ms delays sequentially

    async def test_missing_tool_returns_error(self) -> None:
        registry = ToolRegistry()
        results = await execute_tool_calls(registry, [_call("ghost")], _ctx())
        assert results[0].is_error

    async def test_tool_exception_returns_error(self) -> None:
        registry = ToolRegistry()
        registry.register(_StubTool("bad", raises=RuntimeError("boom")))
        results = await execute_tool_calls(registry, [_call("bad")], _ctx())
        assert results[0].is_error
        assert "boom" in results[0].content[0].text
