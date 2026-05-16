"""/mcp slash command — list, restart, logs.

Routes to MCPClientManager which lives on `app.state.mcp_manager` and is
exposed via :class:`CommandContext.mcp_manager`. When MCP is disabled,
the command degrades gracefully with a single explanatory reply.
"""

from __future__ import annotations

import logging
import shlex
from typing import Any

from pyclaw.core.commands.context import CommandContext

logger = logging.getLogger(__name__)


_USAGE = "用法: /mcp list | /mcp restart <name> | /mcp logs <name>"


def _format_status(status: Any) -> str:
    last = (
        status.last_connect_at.isoformat(timespec="seconds")
        if status.last_connect_at is not None
        else "never"
    )
    tools = "-" if status.status == "pending" else str(status.tool_count)
    line = f"  {status.name:<20s}  {status.status:<10s}  tools={tools:<4s}  last_connect_at={last}"
    if status.reason:
        line += f"  reason={status.reason}"
    return line


async def cmd_mcp(args: str, ctx: CommandContext) -> None:
    manager = getattr(ctx, "mcp_manager", None)
    if manager is None:
        await ctx.reply(
            "MCP integration is disabled. Set 'mcp.enabled: true' in pyclaw.json to enable."
        )
        return

    parts = shlex.split(args.strip()) if args.strip() else []
    if not parts:
        await ctx.reply(_USAGE)
        return

    sub = parts[0]
    rest = parts[1:]

    if sub == "list":
        await _handle_list(ctx, manager)
    elif sub == "restart":
        await _handle_restart(ctx, manager, rest)
    elif sub == "logs":
        await _handle_logs(ctx, manager, rest)
    else:
        await ctx.reply(f"❌ unknown subcommand: {sub!r}\n{_USAGE}")


async def _handle_list(ctx: CommandContext, manager: Any) -> None:
    summary = manager.connection_summary()
    statuses = manager.list_servers()
    if not statuses:
        await ctx.reply(
            "MCP enabled but no servers configured.\n"
            "Add server entries under 'mcp.servers' in pyclaw.json."
        )
        return
    lines = [
        f"MCP servers (ready={manager.is_ready()}; connected={summary.n_connected} "
        f"failed={summary.n_failed} pending={summary.n_pending} disabled={summary.n_disabled} "
        f"total_tools={summary.total_tools}):"
    ]
    for status in statuses:
        lines.append(_format_status(status))
    await ctx.reply("\n".join(lines))


async def _handle_restart(ctx: CommandContext, manager: Any, rest: list[str]) -> None:
    if len(rest) != 1:
        await ctx.reply("用法: /mcp restart <name>")
        return
    name = rest[0]
    result = await manager.restart_server(name)
    if result.ok:
        await ctx.reply(
            f"Restarted MCP server {name!r}: connected, {result.tool_count} tools."
        )
    else:
        await ctx.reply(
            f"Failed to restart {name!r}: {result.reason or 'unknown reason'}"
        )


async def _handle_logs(ctx: CommandContext, manager: Any, rest: list[str]) -> None:
    if len(rest) != 1:
        await ctx.reply("用法: /mcp logs <name>")
        return
    name = rest[0]
    if name not in {s.name for s in manager.list_servers()}:
        await ctx.reply(f"MCP server {name!r} not configured.")
        return
    logs = manager.get_logs(name)
    if not logs:
        await ctx.reply(f"(no stderr captured for MCP server {name!r})")
        return
    body = logs[-3000:]
    await ctx.reply(f"--- MCP server {name!r} stderr (last lines, secrets redacted) ---\n{body}")


__all__ = ["cmd_mcp"]
