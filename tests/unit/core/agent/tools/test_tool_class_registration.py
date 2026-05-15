from __future__ import annotations

from typing import Any

import pytest

from pyclaw.core.agent.tools.registry import (
    ToolContext,
    ToolRegistry,
    wrap_tool_with_abort,
)
from pyclaw.models import ToolResult


class _ValidTool:
    name = "test_valid"
    description = "ok"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    side_effect = False
    tool_class = "read"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        raise NotImplementedError


class _MissingClassTool:
    name = "test_missing"
    description = "missing tool_class"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    side_effect = False

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        raise NotImplementedError


class _InvalidClassTool:
    name = "test_invalid"
    description = "invalid tool_class value"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    side_effect = False
    tool_class = "neither_read_nor_write"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        raise NotImplementedError


def test_register_accepts_valid_tool_class() -> None:
    registry = ToolRegistry()
    registry.register(_ValidTool())
    assert "test_valid" in registry


def test_register_rejects_missing_tool_class() -> None:
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="tool_class"):
        registry.register(_MissingClassTool())  # type: ignore[arg-type]


def test_register_rejects_invalid_tool_class() -> None:
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="neither_read_nor_write"):
        registry.register(_InvalidClassTool())  # type: ignore[arg-type]


def test_all_three_classes_accepted() -> None:
    registry = ToolRegistry()
    for cls_value, suffix in (
        ("read", "r"),
        ("memory-write-safe", "mws"),
        ("write", "w"),
    ):
        cls = type(
            f"_T_{suffix}",
            (),
            {
                "name": f"t_{suffix}",
                "description": "x",
                "parameters": {"type": "object", "properties": {}},
                "side_effect": False,
                "tool_class": cls_value,
                "execute": _ValidTool.execute,
            },
        )
        registry.register(cls())
    assert sorted(registry.names()) == ["t_mws", "t_r", "t_w"]


def test_builtin_tools_have_correct_tool_class() -> None:
    from pyclaw.core.agent.tools.builtin import BashTool, EditTool, ReadTool, WriteTool

    assert BashTool.tool_class == "write"
    assert ReadTool.tool_class == "read"
    assert WriteTool.tool_class == "write"
    assert EditTool.tool_class == "write"


def test_memory_tools_have_correct_tool_class() -> None:
    from pyclaw.core.agent.tools.forget import ForgetTool
    from pyclaw.core.agent.tools.memorize import MemorizeTool
    from pyclaw.core.agent.tools.update_working_memory import UpdateWorkingMemoryTool

    assert MemorizeTool.tool_class == "memory-write-safe"
    assert ForgetTool.tool_class == "write"
    assert UpdateWorkingMemoryTool.tool_class == "read"


def test_skill_view_tool_class() -> None:
    from pyclaw.core.agent.tools.skill_view import SkillViewTool

    assert SkillViewTool.tool_class == "read"


def test_wrap_tool_with_abort_preserves_tool_class() -> None:
    wrapped = wrap_tool_with_abort(_ValidTool())
    assert wrapped.tool_class == "read"


def test_wrap_tool_with_abort_falls_back_to_write_for_missing_class() -> None:
    wrapped = wrap_tool_with_abort(_MissingClassTool())  # type: ignore[arg-type]
    assert wrapped.tool_class == "write"
