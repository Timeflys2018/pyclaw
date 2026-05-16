from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

SandboxBackend = Literal["srt", "none", "docker"]


@runtime_checkable
class SandboxPolicy(Protocol):
    """Spawn-time wrapper that adds isolation around BashTool / MCP stdio.

    Implementations:
    - ``NoSandboxPolicy`` (Sprint 3.0 default, backward compat) — passthrough
    - ``SrtPolicy`` (Sprint 3 Phase 3) — wraps via ``srt`` CLI
    - ``DockerPolicy`` (out-of-scope Sprint 3) — placeholder for future
    """

    backend: SandboxBackend

    def wrap_bash_command(
        self, cmd: str, ctx: Any
    ) -> tuple[str, list[str]]:
        """Return ``(executable, args)`` for ``asyncio.create_subprocess_exec``.

        ``ctx`` is a ``ToolContext`` carrying ``workspace_path``, ``user_id``,
        ``role``, and ``user_profile`` for per-user policy resolution.
        Implementations MUST preserve the original command's stdout/stderr/exit
        code semantics (Sprint 1 invariants 6/7/8).
        """
        ...

    def wrap_mcp_stdio(
        self,
        params: Any,
        server_name: str,
        sandbox_config: Any,
    ) -> Any:
        """Return ``StdioServerParameters`` (possibly wrapped) for MCP spawn.

        Sprint 3 Phase 4: ``SrtPolicy`` rewrites ``params.command`` to ``srt``
        and prepends ``--settings <path>`` + original command + args.
        ``NoSandboxPolicy`` returns ``params`` unchanged.
        """
        ...
