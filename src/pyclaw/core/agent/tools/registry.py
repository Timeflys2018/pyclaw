from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pyclaw.models import TextBlock, ToolResult

ToolClass = Literal["read", "memory-write-safe", "write"]
"""Permission classification consumed by the runner under ``read-only`` tier.

- ``read``: pure read or session-isolated mutation (e.g. ``update_working_memory``)
- ``memory-write-safe``: writes to L2/L3 memory with the subsystem's own guards
  (e.g. ``memorize``); allowed in ``read-only`` per spec ``tool-approval-tiers``
- ``write``: file mutation, shell, destructive memory ops; auto-denied in
  ``read-only`` tier
"""


@dataclass
class ToolContext:
    workspace_id: str
    workspace_path: Path
    session_id: str
    abort: asyncio.Event = field(default_factory=asyncio.Event)
    extras: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]
    side_effect: bool
    tool_class: str

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult: ...


async def check_abort_or_run(
    tool: Tool,
    args: dict[str, Any],
    context: ToolContext,
) -> ToolResult:
    call_id = args.get("_call_id", "")
    if context.abort.is_set():
        return _error(call_id, f"{tool.name}: aborted before execution")
    return await tool.execute(args, context)


def wrap_tool_with_abort(tool: Tool) -> Tool:
    wrapped_class: ToolClass = getattr(tool, "tool_class", "write")

    class _AbortWrapped:
        name = tool.name
        description = tool.description
        parameters = tool.parameters
        side_effect = tool.side_effect
        tool_class: str = wrapped_class
        timeout_seconds = getattr(tool, "timeout_seconds", None)
        max_output_chars = getattr(tool, "max_output_chars", None)

        async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
            return await check_abort_or_run(tool, args, context)

    return _AbortWrapped()  # type: ignore[return-value]


def _tool_timeout_seconds(tool: Tool, default_s: float) -> float:
    override = getattr(tool, "timeout_seconds", None)
    if override is None:
        return default_s
    try:
        value = float(override)
    except (TypeError, ValueError):
        return default_s
    return value if value > 0 else 0.0


_VALID_TOOL_CLASSES: frozenset[str] = frozenset(("read", "memory-write-safe", "write"))


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        cls = getattr(tool, "tool_class", None)
        if cls not in _VALID_TOOL_CLASSES:
            raise ValueError(
                f"tool {tool.name!r} missing or invalid tool_class "
                f"(got {cls!r}; expected one of {sorted(_VALID_TOOL_CLASSES)})"
            )
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def list_for_llm(self) -> list[dict[str, Any]]:
        return [self._to_openai_function(t) for t in self._tools.values()]

    @staticmethod
    def _to_openai_function(tool: Tool) -> dict[str, Any]:
        params = tool.parameters or {"type": "object", "properties": {}}
        if "type" not in params:
            params = {"type": "object", "properties": params.get("properties", params)}
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": params,
            },
        }


async def execute_tool_calls(
    registry: ToolRegistry,
    tool_calls: list[dict[str, Any]],
    context: ToolContext,
    *,
    default_tool_timeout_s: float = 120.0,
) -> list[ToolResult]:
    parallel: list[tuple[int, dict[str, Any]]] = []
    sequential: list[tuple[int, dict[str, Any]]] = []

    for i, call in enumerate(tool_calls):
        name = _function_name(call)
        tool = registry.get(name) if name else None
        if tool is None or tool.side_effect:
            sequential.append((i, call))
        else:
            parallel.append((i, call))

    results: list[ToolResult | None] = [None] * len(tool_calls)

    async def run_one(i: int, call: dict[str, Any]) -> None:
        results[i] = await _dispatch_single(
            registry, call, context, default_timeout_s=default_tool_timeout_s
        )

    if parallel:
        await asyncio.gather(*(run_one(i, c) for i, c in parallel))
    for i, call in sequential:
        await run_one(i, call)

    final: list[ToolResult] = []
    for r in results:
        assert r is not None
        final.append(r)
    return final


async def _dispatch_single(
    registry: ToolRegistry,
    call: dict[str, Any],
    context: ToolContext,
    *,
    default_timeout_s: float = 120.0,
) -> ToolResult:
    from pyclaw.core.agent.runtime_util import (
        AgentAbortedError,
        AgentTimeoutError,
        run_with_timeout,
    )

    call_id = call.get("id", "")
    name = _function_name(call)
    args = _function_args(call)

    if not name:
        return _error(call_id, "tool call missing function name")

    tool = registry.get(name)
    if tool is None:
        return _error(call_id, f"tool {name!r} not registered")

    tool_timeout_s = _tool_timeout_seconds(tool, default_timeout_s)

    try:
        return await run_with_timeout(
            tool.execute(args, context),
            timeout_s=tool_timeout_s,
            abort_event=context.abort,
            kind="tool",
        )
    except AgentTimeoutError as te:
        return _error(call_id, f"{name} timed out after {te.limit_seconds}s")
    except AgentAbortedError:
        return _error(call_id, f"{name} aborted")
    except Exception as exc:
        return _error(call_id, f"{name} raised {type(exc).__name__}: {exc}")


def _function_name(call: dict[str, Any]) -> str | None:
    fn = call.get("function") or {}
    return fn.get("name") if isinstance(fn, dict) else None


def _function_args(call: dict[str, Any]) -> dict[str, Any]:
    fn = call.get("function") or {}
    args = fn.get("arguments") if isinstance(fn, dict) else None
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        import json

        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {"_raw": args}
        except json.JSONDecodeError:
            return {"_raw": args}
    return {}


def _error(tool_call_id: str, message: str) -> ToolResult:
    return ToolResult(
        tool_call_id=tool_call_id,
        content=[TextBlock(text=message)],
        is_error=True,
    )


def text_result(tool_call_id: str, text: str) -> ToolResult:
    return ToolResult(tool_call_id=tool_call_id, content=[TextBlock(text=text)], is_error=False)


def error_result(tool_call_id: str, text: str) -> ToolResult:
    return ToolResult(tool_call_id=tool_call_id, content=[TextBlock(text=text)], is_error=True)


def tool_result_to_llm_content(result: ToolResult) -> str:
    parts: list[str] = []
    for block in result.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
    return "\n".join(parts) if parts else ""
