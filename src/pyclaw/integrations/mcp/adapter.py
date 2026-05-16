"""MCPToolAdapter — bridges an MCP server's tool to PyClaw's :class:`Tool` Protocol.

Each adapter wraps exactly one tool from one connected MCP server. The
manager (:class:`pyclaw.integrations.mcp.client_manager.MCPClientManager`)
constructs adapters after each ``connect_to_server`` call and registers them
into PyClaw's :class:`ToolRegistry`.

The adapter holds **two name attributes** (the dual-name pattern that closes
review v3 finding J1):

* ``name`` — canonical ``{server_name}:{remote_tool_name}`` (e.g.,
  ``"filesystem:read_file"``). This is the registry key, what the LLM
  ultimately calls (after the registry's ``:`` → ``__`` rewrite at the
  ``_to_openai_function`` boundary), and what runner-level per-call tier
  evaluation reads.
* ``_sdk_key`` — the key the underlying ``ClientSessionGroup`` uses
  internally to dispatch the tool. Without a ``component_name_hook`` the
  SDK's ``_component_name`` returns the bare ``remote_tool.name``, so
  ``_sdk_key`` simply equals ``remote_tool.name`` in practice. The manager
  passes this in explicitly so a future spec revision (manager-side hook,
  multi-group sharding, etc.) can change the rule without touching the
  adapter.

The dispatch boundary is :meth:`MCPToolAdapter.execute` which calls
``group.call_tool(self._sdk_key, args)`` — NOT ``self.name`` (the canonical
form would KeyError because the SDK doesn't store it under that key).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import anyio
import mcp.types as mcp_types
from mcp.shared.exceptions import McpError

from pyclaw.integrations.mcp.errors import MCPServerDeadError
from pyclaw.integrations.mcp.settings import McpServerConfig
from pyclaw.models import ImageBlock, TextBlock, ToolResult

if TYPE_CHECKING:
    from mcp.client.session_group import ClientSessionGroup


_DEAD_SERVER_EXCEPTIONS: tuple[type[BaseException], ...] = (
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
    EOFError,
)


def _is_connection_loss_mcp_error(exc: McpError) -> bool:
    code = getattr(getattr(exc, "error", None), "code", None)
    if code is None:
        return False
    return code in {-32000, -32099}


def _is_broken_pipe_oserror(exc: OSError) -> bool:
    import errno

    return exc.errno in {errno.EPIPE, errno.ECONNRESET, errno.EBADF}


def _derive_tool_class(
    server_config: McpServerConfig,
    annotations: mcp_types.ToolAnnotations | None,
) -> Literal["read", "write"]:
    if server_config.forced_tool_class is not None:
        return server_config.forced_tool_class
    if (
        server_config.trust_annotations
        and annotations is not None
        and annotations.readOnlyHint is True
    ):
        return "read"
    return "write"


def _convert_content_block(
    block: Any,
) -> TextBlock | ImageBlock:
    if isinstance(block, mcp_types.TextContent):
        return TextBlock(text=block.text)
    if isinstance(block, mcp_types.ImageContent):
        return ImageBlock(data=block.data, mime_type=block.mimeType)
    if isinstance(block, mcp_types.AudioContent):
        size = len(block.data) if block.data else 0
        return TextBlock(text=f"[audio: {size} bytes, {block.mimeType}]")
    if isinstance(block, mcp_types.EmbeddedResource):
        uri = getattr(getattr(block, "resource", None), "uri", "<unknown>")
        return TextBlock(text=f"[resource: {uri}]")
    if isinstance(block, mcp_types.ResourceLink):
        return TextBlock(text=f"[resource: {block.uri}]")
    return TextBlock(text=f"[unsupported content block: {type(block).__name__}]")


@dataclass
class MCPToolAdapter:
    """Implements :class:`pyclaw.core.agent.tools.registry.Tool` Protocol via duck typing."""

    server_name: str
    remote_tool: mcp_types.Tool
    server_config: McpServerConfig
    group: ClientSessionGroup
    sdk_key: str

    name: str = field(init=False)
    description: str = field(init=False)
    parameters: dict[str, Any] = field(init=False)
    tool_class: Literal["read", "write"] = field(init=False)
    side_effect: bool = field(init=False)
    _sdk_key: str = field(init=False)

    def __post_init__(self) -> None:
        if ":" in self.remote_tool.name:
            raise ValueError(
                f"MCP tool {self.server_name!r}:{self.remote_tool.name!r} rejected: "
                f"remote tool name must not contain ':' (conflicts with namespace separator)"
            )
        self.name = f"{self.server_name}:{self.remote_tool.name}"
        self.description = self.remote_tool.description or ""
        self.parameters = self.remote_tool.inputSchema or {
            "type": "object",
            "properties": {},
        }
        self.tool_class = _derive_tool_class(self.server_config, self.remote_tool.annotations)
        self.side_effect = self.tool_class != "read"
        self._sdk_key = self.sdk_key

    async def execute(self, args: dict[str, Any], context: Any) -> ToolResult:
        call_id = getattr(context, "tool_call_id", "") or ""
        timeout_s = self.server_config.call_timeout_seconds
        try:
            async with asyncio.timeout(timeout_s):
                result: mcp_types.CallToolResult = await self.group.call_tool(
                    self._sdk_key, args
                )
        except asyncio.TimeoutError:
            return ToolResult(
                tool_call_id=call_id,
                content=[
                    TextBlock(text=f"MCP tool {self.name!r} timed out after {timeout_s}s")
                ],
                is_error=True,
            )
        except _DEAD_SERVER_EXCEPTIONS as exc:
            raise MCPServerDeadError(self.server_name, type(exc).__name__) from exc
        except OSError as exc:
            if _is_broken_pipe_oserror(exc):
                raise MCPServerDeadError(
                    self.server_name, f"OSError errno={exc.errno}"
                ) from exc
            return ToolResult(
                tool_call_id=call_id,
                content=[TextBlock(text=f"{self.name} raised OSError: {exc}")],
                is_error=True,
            )
        except McpError as exc:
            if _is_connection_loss_mcp_error(exc):
                raise MCPServerDeadError(
                    self.server_name, f"McpError code={exc.error.code}"
                ) from exc
            return ToolResult(
                tool_call_id=call_id,
                content=[TextBlock(text=f"{self.name} raised McpError: {exc}")],
                is_error=True,
            )

        blocks: list[Any] = []
        for raw in result.content or []:
            blocks.append(_convert_content_block(raw))
        return ToolResult(
            tool_call_id=call_id,
            content=blocks or [TextBlock(text="")],
            is_error=bool(result.isError),
        )


__all__ = ["MCPToolAdapter"]
