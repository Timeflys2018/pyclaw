"""MCP (Model Context Protocol) client integration for PyClaw.

Connects PyClaw's agent to external MCP servers (filesystem, github, slack,
etc.) so their tools become indistinguishable from builtin tools at the runner
level. Reuses Sprint 1's permission tier system (:mod:`pyclaw.core.hooks`)
without modification.

Public API:

* :class:`MCPClientManager` — owns the lifecycle of all configured servers.
  Constructed once per PyClaw process (in the FastAPI lifespan); started via
  :meth:`MCPClientManager.start_background` (non-blocking); attached to the
  runner's :class:`ToolRegistry` via :meth:`MCPClientManager.attach_and_register`
  (called from the agent factory hook).
* :class:`MCPToolAdapter` — implements the duck-typed
  :class:`pyclaw.core.agent.tools.registry.Tool` Protocol; one per discovered
  remote tool. Carries dual names (``name`` canonical, ``_sdk_key`` for
  ``ClientSessionGroup.call_tool``).
* :class:`MCPServerDeadError` — raised by adapter ``execute()`` to signal a
  dead-server condition; caught inside ``_dispatch_single`` and converted
  into an error :class:`ToolResult` with a non-blocking
  ``_handle_server_death`` schedule.
* :class:`McpSettings`, :class:`McpServerConfig`, :class:`ServerStatus`,
  :class:`RestartResult`, :class:`ConnectionSummary` — Pydantic / dataclass
  types used in configuration, status reporting, and the ``/mcp`` slash
  command.

Architectural invariant: ``pyclaw.core.*`` MUST NOT import from
``pyclaw.integrations.mcp.*`` *except* for :class:`MCPServerDeadError`
(via :mod:`pyclaw.integrations.mcp.errors`). Wiring is via the
``external_tool_registrar`` callback parameter on the agent factory and
duck-typing in the runner's per-call tier evaluation.
"""

from pyclaw.integrations.mcp.errors import MCPServerDeadError

__all__ = [
    "MCPServerDeadError",
]
