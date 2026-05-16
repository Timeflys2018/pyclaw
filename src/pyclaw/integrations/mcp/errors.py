"""Error types for the MCP integration.

Defined in this module specifically to keep the import topology clean:
``core/agent/tools/registry.py:_dispatch_single`` needs to catch
``MCPServerDeadError`` (per spec ``tool-registry``), and lives in the
core layer. Importing from ``pyclaw.integrations.mcp.errors`` directly
keeps ``core/`` from depending on the heavier MCP adapter / manager
modules.
"""

from __future__ import annotations


class MCPServerDeadError(Exception):
    """Raised by an :class:`MCPToolAdapter` when a tool call detects that the
    backing MCP server has died (broken stdio pipe, EOF on transport,
    connection-loss ``McpError``).

    ``_dispatch_single`` catches this exception **inside the dispatcher**
    (before the generic ``except Exception``), schedules the manager-side
    death handler via ``task_manager.spawn`` (non-blocking), and returns a
    normal error :class:`ToolResult` to the runner. The exception MUST NOT
    propagate above ``_dispatch_single`` because ``execute_tool_calls`` uses
    ``asyncio.gather`` without ``return_exceptions=True`` for parallel calls
    and a bare ``for await`` loop for sequential calls — propagation would
    cancel sibling tasks and crash the post-dispatch ``assert r is not None``.

    Attributes:
        server_name: The user-chosen config key of the dead server (e.g.,
            ``"github"``), NOT the SDK-internal identifier.
        reason: Short human-readable description (e.g., ``"broken pipe"``,
            ``"EOF on transport"``, ``"connection-loss McpError code -32099"``).
    """

    def __init__(self, server_name: str, reason: str) -> None:
        super().__init__(f"MCP server {server_name!r} is unavailable: {reason}")
        self.server_name = server_name
        self.reason = reason


__all__ = ["MCPServerDeadError"]
