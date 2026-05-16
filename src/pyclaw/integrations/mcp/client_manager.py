"""MCPClientManager — owns the lifecycle of all configured MCP servers.

One instance per PyClaw process. Constructed in :func:`pyclaw.app._lifespan`
during startup. Connects to servers in a non-blocking background supervisor
task (via :meth:`start_background`) so the FastAPI lifespan does NOT block
on per-server ``connect_timeout_seconds``. The agent factory hook calls
:meth:`attach_and_register` to bind the manager to a runner's
:class:`ToolRegistry`; servers connecting after that point register their
adapters live (per §5.5 of the spec).

The manager exposes:

* :meth:`start_background` — non-blocking; returns the supervisor task id
  (or ``None`` if the underlying ``TaskManager`` is already closed).
* :attr:`ready` — :class:`asyncio.Event` set when the supervisor's
  ``gather`` returns (success OR all-failed). ``try/finally`` in
  ``_run_supervisor`` guarantees this is set even on supervisor crash.
* :meth:`is_ready` / :meth:`connection_summary` — synchronous probes for
  ``/health`` and ``/mcp list``.
* :meth:`attach_and_register` — synchronous, idempotent. Binds a
  :class:`ToolRegistry` and bulk-registers any already-connected
  adapters.
* :meth:`restart_server` — per-server lock, atomic adapter swap on
  success, removes old adapters on failure (the spec's safer-by-default
  semantic).
* :meth:`get_logs` — stderr ring buffer with secret redaction.
* :meth:`list_servers` — returns one :class:`ServerStatus` per
  configured server.
* :meth:`shutdown` — cancels supervisor + drains MCP-tagged tasks +
  closes the underlying ``ClientSessionGroup``.

Architectural invariant: ``pyclaw.core.*`` MUST NOT import this module.
Wiring is via ``deps.mcp_death_handler`` (a bound coroutine published into
``ToolContext.extras``) and the ``external_tool_registrar`` callback on
the agent factory.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

import anyio
import mcp.types as mcp_types
from mcp.client.session_group import ClientSessionGroup
from mcp.client.stdio import StdioServerParameters
from mcp.shared.exceptions import McpError

from pyclaw.integrations.mcp.adapter import MCPToolAdapter
from pyclaw.integrations.mcp.errors import MCPServerDeadError
from pyclaw.integrations.mcp.settings import (
    McpServerConfig,
    McpSettings,
    _substitute_env_placeholder,
)

if TYPE_CHECKING:
    from pyclaw.core.agent.tools.registry import ToolRegistry
    from pyclaw.infra.task_manager import TaskManager


logger = logging.getLogger(__name__)


_STDERR_BUFFER_LINES = 200
_TOOL_COUNT_WARN_THRESHOLD = 100


ServerStatusLiteral = Literal["connected", "failed", "disabled", "pending"]


@dataclass
class ServerStatus:
    name: str
    status: ServerStatusLiteral
    tool_count: int = 0
    last_connect_at: datetime | None = None
    reason: str | None = None


@dataclass
class RestartResult:
    ok: bool
    tool_count: int = 0
    reason: str | None = None


@dataclass
class ConnectionSummary:
    n_connected: int = 0
    n_failed: int = 0
    n_pending: int = 0
    n_disabled: int = 0
    total_tools: int = 0


class MCPClientManager:
    def __init__(
        self,
        settings: McpSettings,
        task_manager: TaskManager,
    ) -> None:
        self._settings = settings
        self._task_manager = task_manager
        self._adapters: dict[str, list[MCPToolAdapter]] = {}
        self._servers: dict[str, ServerStatus] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._impl_to_config: dict[str, str] = {}
        self._registry: ToolRegistry | None = None
        self._stderr_buffers: dict[str, deque[str]] = {}
        self._supervisor_task_id: str | None = None
        self._group: ClientSessionGroup = ClientSessionGroup()
        self._sessions: dict[str, mcp_types.Implementation] = {}
        self.ready: asyncio.Event = asyncio.Event()
        self._initialize_server_statuses()

    def _initialize_server_statuses(self) -> None:
        for name, config in self._settings.servers.items():
            if not config.enabled:
                self._servers[name] = ServerStatus(name=name, status="disabled")
            else:
                self._servers[name] = ServerStatus(name=name, status="pending")

    def _enabled_servers(self) -> list[tuple[str, McpServerConfig]]:
        return [
            (name, config)
            for name, config in self._settings.servers.items()
            if config.enabled
        ]

    def _get_lock(self, name: str) -> asyncio.Lock:
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]

    def start_background(self) -> str | None:
        if not self._settings.enabled:
            self.ready.set()
            return None
        try:
            task_id = self._task_manager.spawn(
                name="mcp:supervisor",
                coro=self._run_supervisor(),
                category="mcp",
            )
        except Exception as exc:
            logger.error(
                "mcp.start_background.skipped reason=%s details=%s",
                type(exc).__name__,
                exc,
            )
            self.ready.set()
            return None
        self._supervisor_task_id = task_id
        return task_id

    async def _run_supervisor(self) -> None:
        try:
            await asyncio.gather(
                *[
                    self._connect_one(name, config)
                    for name, config in self._enabled_servers()
                ],
                return_exceptions=True,
            )
            self._log_startup_summary()
        except Exception:
            logger.exception("MCP supervisor crashed unexpectedly; marking ready anyway")
        finally:
            self.ready.set()

    def _log_startup_summary(self) -> None:
        summary = self.connection_summary()
        level = (
            logging.WARNING
            if summary.total_tools > _TOOL_COUNT_WARN_THRESHOLD
            else logging.INFO
        )
        logger.log(
            level,
            "MCP startup summary: connected=%d failed=%d disabled=%d total_tools=%d",
            summary.n_connected,
            summary.n_failed,
            summary.n_disabled,
            summary.total_tools,
        )
        if summary.total_tools > _TOOL_COUNT_WARN_THRESHOLD:
            logger.warning(
                "MCP registered %d tools across %d connected servers; this may "
                "consume significant prompt budget. Consider disabling unused "
                "servers via 'enabled: false'.",
                summary.total_tools,
                summary.n_connected,
            )

    async def _connect_one(self, name: str, config: McpServerConfig) -> None:
        async with self._get_lock(name):
            self._stderr_buffers.setdefault(name, deque(maxlen=_STDERR_BUFFER_LINES))

            resolved_env: dict[str, str] = {}
            for key, raw_value in config.env.items():
                resolved, status = _substitute_env_placeholder(raw_value)
                if status == "missing-env-var":
                    self._servers[name] = ServerStatus(
                        name=name,
                        status="failed",
                        reason=f"env var referenced in {key!r} is not set",
                    )
                    logger.warning(
                        "MCP server %r failed: env var referenced in %r is not set",
                        name,
                        key,
                    )
                    return
                resolved_env[key] = resolved

            stdio_params = StdioServerParameters(
                command=config.command,
                args=list(config.args),
                env=resolved_env or None,
            )

            try:
                async with asyncio.timeout(config.connect_timeout_seconds):
                    session = await self._group.connect_to_server(stdio_params)
            except (asyncio.TimeoutError, TimeoutError):
                self._servers[name] = ServerStatus(
                    name=name,
                    status="failed",
                    reason=f"connect timeout ({config.connect_timeout_seconds}s)",
                )
                logger.warning(
                    "MCP server %r failed: connect timeout (%ss)",
                    name,
                    config.connect_timeout_seconds,
                )
                return
            except McpError as exc:
                err_msg = exc.error.message if hasattr(exc, "error") else str(exc)
                if "already" in err_msg.lower() and "exist" in err_msg.lower():
                    self._servers[name] = ServerStatus(
                        name=name,
                        status="failed",
                        reason=(
                            f"tool name collision: another connected server already "
                            f"provides at least one tool with the same remote name"
                        ),
                    )
                else:
                    self._servers[name] = ServerStatus(
                        name=name, status="failed", reason=f"McpError: {err_msg}"
                    )
                logger.warning("MCP server %r failed: %s", name, err_msg)
                return
            except (
                anyio.ClosedResourceError,
                anyio.BrokenResourceError,
                ConnectionError,
                OSError,
                FileNotFoundError,
            ) as exc:
                self._servers[name] = ServerStatus(
                    name=name,
                    status="failed",
                    reason=f"{type(exc).__name__}: {exc}",
                )
                logger.warning(
                    "MCP server %r failed: %s: %s",
                    name,
                    type(exc).__name__,
                    exc,
                )
                return

            adapters = self._build_adapters_for_session(name, config, session)
            self._adapters[name] = adapters
            if self._registry is not None:
                for adapter in adapters:
                    self._registry.register(adapter)
            tool_count = len(adapters)
            self._servers[name] = ServerStatus(
                name=name,
                status="connected",
                tool_count=tool_count,
                last_connect_at=datetime.now(timezone.utc),
            )
            level = logging.WARNING if tool_count == 0 else logging.INFO
            logger.log(
                level,
                "MCP server %r connected: %d tools%s",
                name,
                tool_count,
                "" if tool_count > 0 else " (check server config — server reports zero tools)",
            )

    def _build_adapters_for_session(
        self,
        server_name: str,
        config: McpServerConfig,
        session,
    ) -> list[MCPToolAdapter]:
        adapters: list[MCPToolAdapter] = []
        for sdk_key, tool in self._group.tools.items():
            if self._group._tool_to_session.get(sdk_key) is not session:
                continue
            try:
                adapter = MCPToolAdapter(
                    server_name=server_name,
                    remote_tool=tool,
                    server_config=config,
                    group=self._group,
                    sdk_key=sdk_key,
                )
            except ValueError as exc:
                logger.warning(
                    "MCP tool %r:%r rejected: %s. Skipping this tool only.",
                    server_name,
                    tool.name,
                    exc,
                )
                continue
            adapters.append(adapter)
        return adapters

    def attach_and_register(self, registry: ToolRegistry) -> None:
        if self._registry is registry:
            return
        if self._registry is not None:
            logger.warning(
                "mcp.attach_and_register.rebind from %s to %s",
                id(self._registry),
                id(registry),
            )
            for adapters in self._adapters.values():
                for adapter in adapters:
                    self._registry.unregister(adapter.name)
        self._registry = registry
        for adapters in self._adapters.values():
            for adapter in adapters:
                registry.register(adapter)

    async def restart_server(self, name: str) -> RestartResult:
        if name not in self._settings.servers:
            return RestartResult(ok=False, reason=f"server {name!r} not configured")
        config = self._settings.servers[name]
        if not config.enabled:
            return RestartResult(ok=False, reason=f"server {name!r} disabled")
        async with self._get_lock(name):
            old_adapters = list(self._adapters.get(name, []))
            for adapter in old_adapters:
                if self._registry is not None:
                    self._registry.unregister(adapter.name)
            self._adapters[name] = []

        await self._connect_one(name, config)

        status = self._servers.get(name)
        if status is None or status.status != "connected":
            reason = (status.reason if status else "unknown") or "reconnect failed"
            return RestartResult(ok=False, reason=reason)
        return RestartResult(ok=True, tool_count=status.tool_count)

    def get_logs(self, name: str) -> str:
        buffer = self._stderr_buffers.get(name)
        if buffer is None:
            return ""
        joined = "\n".join(buffer)
        config = self._settings.servers.get(name)
        if config is None:
            return joined
        for raw_value in config.env.values():
            resolved, status = _substitute_env_placeholder(raw_value)
            if status == "resolved" and resolved:
                joined = joined.replace(resolved, "<REDACTED>")
            elif status == "literal" and raw_value:
                joined = joined.replace(raw_value, "<REDACTED>")
        return joined

    def list_servers(self) -> list[ServerStatus]:
        return [self._servers[name] for name in self._settings.servers]

    def is_ready(self) -> bool:
        return self.ready.is_set()

    def connection_summary(self) -> ConnectionSummary:
        summary = ConnectionSummary()
        for status in self._servers.values():
            if status.status == "connected":
                summary.n_connected += 1
                summary.total_tools += status.tool_count
            elif status.status == "failed":
                summary.n_failed += 1
            elif status.status == "pending":
                summary.n_pending += 1
            elif status.status == "disabled":
                summary.n_disabled += 1
        return summary

    async def _handle_server_death(self, name: str) -> None:
        async with self._get_lock(name):
            current = self._servers.get(name)
            if current is None or current.status == "failed":
                return
            adapters = list(self._adapters.get(name, []))
            for adapter in adapters:
                if self._registry is not None:
                    self._registry.unregister(adapter.name)
            self._adapters[name] = []
            self._servers[name] = ServerStatus(
                name=name,
                status="failed",
                reason="server died mid-call",
                last_connect_at=current.last_connect_at,
            )
            logger.warning("mcp.server_died name=%s", name)

    async def shutdown(self) -> None:
        if self._supervisor_task_id is not None:
            try:
                await self._task_manager.cancel(self._supervisor_task_id)
            except Exception:
                logger.debug("mcp.shutdown supervisor cancel error", exc_info=True)
        try:
            await self._group.__aexit__(None, None, None)
        except Exception:
            logger.debug("mcp.shutdown group close error", exc_info=True)


__all__ = [
    "ConnectionSummary",
    "MCPClientManager",
    "RestartResult",
    "ServerStatus",
]
